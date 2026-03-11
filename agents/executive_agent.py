import json
import re
import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import litellm
except ImportError:
    litellm = None

from prompts import load_prompt
from memory.vector_memory import VectorMemory
from memory.context_manager import ContextManager

if TYPE_CHECKING:
    from orchestrator.event_bus import EventBus
    from orchestrator.task_queue import TaskQueue, Task

logger = logging.getLogger(__name__)

MAX_STEPS = 12   # max atomic worker dispatches per task

_SKILLS_DIR = Path(__file__).parent.parent / "workspace" / "skills"


def _load_skills_summary() -> str:
    """
    Read workspace/skills/ and return a short summary for injection into prompts.
    Fast synchronous read — skills are small markdown files.
    """
    try:
        if not _SKILLS_DIR.exists():
            return ""
        skills = sorted(_SKILLS_DIR.glob("*.md"))
        if not skills:
            return ""
        lines = ["## Available Skills (workspace/skills/)"]
        for s in skills:
            try:
                text = s.read_text(encoding="utf-8", errors="replace")
                desc = ""
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        for line in text[3:end].splitlines():
                            if line.strip().startswith("description:"):
                                desc = line.split(":", 1)[1].strip().lstrip(">").strip()
                                break
                lines.append(f"- **{s.stem}**: {desc}" if desc else f"- **{s.stem}**")
            except Exception:
                lines.append(f"- **{s.stem}**")
        lines.append(
            "\nWorkers can read any skill with `read_skill(name)` before starting a task, "
            "or create new ones with `create_skill(name, content)`."
        )
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_json(raw: str) -> dict | None:
    """
    Robustly extract the first valid JSON object from a model response.

    Handles:
    - <think>...</think> blocks that contain JSON snippets
    - Markdown code fences (```json ... ```)
    - Plain JSON at any position in the response

    Strategy: strip thinking blocks, then scan backwards from the last '}'
    to find the outermost balanced {...} object and parse it.
    """
    # 1. Remove <think>...</think> blocks (thinking models like qwen3, deepseek-r1)
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # 2. Extract from ```json ... ``` fence if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Scan backwards: find the outermost {...} by matching braces
    depth = 0
    end = -1
    for i in range(len(cleaned) - 1, -1, -1):
        ch = cleaned[i]
        if ch == "}":
            if end == -1:
                end = i
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0 and end != -1:
                try:
                    return json.loads(cleaned[i : end + 1])
                except json.JSONDecodeError:
                    # Keep scanning in case there's an earlier valid object
                    end = -1
                    depth = 0

    # 4. Fallback: first { to last }
    start = cleaned.find("{")
    last = cleaned.rfind("}") + 1
    if 0 <= start < last:
        try:
            return json.loads(cleaned[start:last])
        except json.JSONDecodeError:
            pass

    return None


class ExecutiveAgent:
    def __init__(self, name: str, role: str, department: str, model: str,
                 event_bus, task_queue, workers: dict,
                 exec_index: int = 0,
                 context_length: int = 16384, compaction_threshold: float = 0.75):
        self.name = name
        self.role = role
        self.department = department
        self.model = model
        self._bus = event_bus
        self._task_queue = task_queue
        self._workers = workers

        self._context = load_prompt("COMPANY", "USER", "EXECUTIVE", f"EXECUTIVE{exec_index + 1}")
        self._memory = VectorMemory(f"{self.department}_exec")
        self._ctx_mgr = ContextManager(model, context_length, compaction_threshold)

        logger.info(
            f"[Executive] {name} ({department}) ready — "
            f"context window: {context_length} tokens, "
            f"compaction at {int(compaction_threshold*100)}%"
        )

    # ── Prompts ────────────────────────────────────────────────────────────

    def _build_thinking_prompt(self) -> str:
        return f"""{self._context}

---

You are {self.name}, {self.role}. You have been given a task to plan and execute.

Think through it carefully before acting:
1. What exactly is being asked? What does a complete, successful result look like?
2. What steps are needed and in what order?
3. What could go wrong, and how do you mitigate it?
4. Choose a short project folder name (slug, no spaces) for the workspace.

Be direct and concise — this is internal reasoning. Under 300 words.
Do NOT produce a step list yet. Just think."""

    def _build_action_prompt(self) -> str:
        worker_types = list(self._workers.keys())
        skills_block = _load_skills_summary()
        skills_section = f"\n\n{skills_block}" if skills_block else ""
        return f"""{self._context}

---

You are {self.name}, {self.role}. You are orchestrating workers to complete a task.

## Your Workers

{', '.join(worker_types)}

**Workers are small local models.** They execute precise instructions but cannot reason or plan.
You are the brain. Workers are your hands.{skills_section}

## How to Direct Workers

- **docs**: Tell it the EXACT path and EXACT content to write. Write the file content yourself — the worker just saves it.
- **coder**: Tell it the EXACT path and EXACT code to write. Include the full file content. Then tell it which command to run to test it. Always instruct it to commit locally — never push to remote.
- **research**: Give it the EXACT search query. It returns raw results for you to process.
- **qa**: Tell it EXACTLY which files to read and what to verify. For verification, use syntax checks and code review — not long-running servers (60s timeout). A code-review PASS is a valid result.

Each step does ONE thing: one file write, one search, one command.

## Using Skills in Worker Instructions

If a relevant skill exists in the skills list above, name it explicitly in your task instructions.
Workers will call `read_skill(name)` themselves at the start of the task to load the full content.

For example: if a `web-design-pro` skill exists and you are building a web UI, include in your
task: "Load and follow the `web-design-pro` skill." The worker will fetch and apply it.
Always name specific skills by their exact slug (e.g. `anti-slop-design`, not "the design skill").

## Understanding the Project Before Dispatching

**Before dispatching any write task on a project that may already have files, you MUST know what exists.**

Use the coder's `list_dir` and `read_file` tools to gather this information first:
- Dispatch a coder step: "List the project directory and return the contents of all existing source files."
- Read the results carefully — they tell you exact file paths, element IDs, class names, function signatures, and APIs already in place.
- Only then dispatch write tasks, and include the relevant existing content in your instructions so the worker writes code that is fully compatible with what is already there.

**You are the brain. Workers cannot reason about what other workers wrote. You must be the bridge.** If one step produces a file with specific element IDs, you must carry that information into the next step's instructions so the next worker uses the same IDs. Never dispatch a write task and assume workers will independently figure out how to be consistent with each other.

## Reading Worker Results

Worker results tell you what actually happened. Read them carefully:
- A result starting with "Written:", "Committed", or similar = the action succeeded
- A result that is plain prose or a description = the worker wrote text instead of using tools — the action DID NOT happen. Try again with clearer instructions, or use a different worker.
- A result with "error" or "failed" = something went wrong, decide whether to retry or skip

**Do NOT declare done until the actual deliverables exist** — files written, code tested, etc.
Research returning text summaries is useful context, but it does not mean the task is complete.

**CRITICAL — Past memory is context only, NOT proof of completion.**
If your memory or past context mentions similar work, that is a previous session. You must still
dispatch workers and produce real deliverables in THIS session. Never declare done at step 0.

## Capturing Skills

After completing a multi-step task successfully, you may optionally dispatch one final docs step
to save the workflow as a reusable skill. Do this when:
- The task required a non-obvious sequence of steps that worked well
- You discovered the best way to use a combination of tools for this type of work
- This kind of task will likely recur

To save a skill, dispatch: worker=docs, task="Call create_skill(name='<kebab-name>', content='<full skill markdown>'). The skill should document the workflow, trigger conditions, step sequence, and any lessons learned."

Only do this if the pattern is genuinely reusable — not for one-off tasks.

## Response Format

Respond with ONLY a JSON object — no markdown, no explanation, no thinking tags.

To dispatch a worker:
{{"worker": "<type>", "title": "<short step name>", "task": "<complete atomic instructions including exact content/commands>"}}

To declare the task complete (only when workers in THIS session have confirmed deliverables):
{{"done": true, "summary": "<executive summary: what was built, file paths, outcomes>"}}

Maximum {MAX_STEPS} steps. Declare done before that limit."""

    def _build_synthesis_prompt(self) -> str:
        return f"""{self._context}

---

You are {self.name}, {self.role}. Your workers have completed their tasks.
Write a concise executive summary of what was accomplished.
Include file paths, key outputs, and any issues encountered."""

    # ── LLM calls ──────────────────────────────────────────────────────────

    async def _think(self, task: str, past_context: str) -> str:
        """Reasoning pass — deliberate analysis before acting."""
        messages = [
            {"role": "system", "content": self._build_thinking_prompt()},
            {"role": "user", "content": task + (f"\n\n{past_context}" if past_context else "")},
        ]
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.5,
            )
            thinking = resp.choices[0].message.content.strip()
            logger.info(f"[Executive:{self.name}] Thinking complete ({len(thinking)} chars)")
            return thinking
        except Exception as e:
            logger.error(f"[Executive:{self.name}] Think error: {e}")
            return ""

    async def _next_action(self, task: str, thinking: str, step_history: list[dict]) -> dict:
        """
        Executive decides the next atomic worker action based on current state.
        Returns a dict with either:
          {"worker": ..., "title": ..., "task": ...}  — dispatch a worker
          {"done": True, "summary": ...}              — task complete
        """
        history_text = ""
        if step_history:
            history_text = "\n\n## Steps completed:\n"
            for s in step_history:
                history_text += f"\n**Step {s['step']} — {s['worker']}: {s['title']}**\n"
                history_text += f"Result: {s['result']}\n"

        user_content = (
            f"Task:\n{task}"
            + (f"\n\nMy analysis:\n{thinking}" if thinking else "")
            + history_text
            + f"\n\nSteps used: {len(step_history)}/{MAX_STEPS}. What is the next action?"
        )

        messages = [
            {"role": "system", "content": self._build_action_prompt()},
            {"role": "user", "content": user_content},
        ]

        if self._ctx_mgr.needs_compaction(messages):
            messages = await self._ctx_mgr.compact(messages)

        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            action = _extract_json(raw)
            if action and isinstance(action, dict):
                # Guard: never accept "done" at step 0 with no workers dispatched
                if action.get("done") and not step_history:
                    logger.warning(
                        f"[Executive:{self.name}] Model declared done at step 0 with no work done "
                        f"(likely false confidence from memory) — ignoring, forcing first worker step"
                    )
                    # Return a safe fallback: ask the coder to list the workspace first
                    return {
                        "worker": list(self._workers.keys())[0],
                        "title": "Start — explore workspace",
                        "task": f"List the workspace directory and report what files exist. Task context: {task[:300]}",
                    }
                logger.info(
                    f"[Executive:{self.name}] Next action: "
                    f"{'DONE' if action.get('done') else action.get('worker','?') + ' — ' + action.get('title','?')}"
                )
                return action
            logger.warning(f"[Executive:{self.name}] Could not extract JSON from response: {raw[:200]}")
        except Exception as e:
            logger.error(f"[Executive:{self.name}] next_action error: {e}")

        # Return None summary so handle() falls through to _synthesize() from step_history
        return {"done": True, "summary": None}

    async def _synthesize(self, task: str, results_text: str) -> str:
        """Fallback synthesis when max steps reached without explicit done."""
        messages = [
            {"role": "system", "content": self._build_synthesis_prompt()},
            {"role": "user", "content": f"Original task: {task}"},
            {"role": "assistant", "content": f"Worker results:\n{results_text}"},
            {"role": "user", "content": "Write your executive summary now."},
        ]
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.4,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[Executive:{self.name}] Synthesis error: {e}")
            return results_text or "Tasks completed."

    # ── Worker dispatch ────────────────────────────────────────────────────

    async def _run_worker_task(self, title: str, description: str, worker_type: str) -> str:
        """Dispatch a single atomic task to a worker and return its result."""
        from orchestrator.task_queue import Task as TaskObj

        worker_types = list(self._workers.keys())
        if worker_type not in self._workers:
            logger.warning(
                f"[Executive:{self.name}] Unknown worker '{worker_type}', "
                f"falling back to '{worker_types[0]}'"
            )
            worker_type = worker_types[0]
        worker = self._workers[worker_type]

        t = TaskObj(
            title=title,
            description=description,
            worker_type=worker_type,
            assigned_to=self.name,
            channel=self.department,
            worker=worker,
        )
        await self._task_queue.enqueue(t)

        timeout, elapsed = 600, 0
        while elapsed < timeout:
            if t.status in ("completed", "failed"):
                break
            await asyncio.sleep(2)
            elapsed += 2

        if elapsed >= timeout:
            logger.warning(f"[Executive:{self.name}] Step '{title}' timed out after {timeout}s")

        return t.result or f"(no result — status: {t.status})"

    # ── Main entry point ───────────────────────────────────────────────────

    async def handle(self, task: str) -> str:
        await self._bus.publish(self.department, "agent_started", self.name,
                                {"task": task[:200]}, role=self.role)

        # ── Long-term memory: recall relevant past work ───────────────────
        past_context = ""
        recalled = await self._memory.recall(task, n_results=3)
        if recalled:
            past_context = "\n\nRelevant past work:\n" + "\n---\n".join(recalled[:3])

        # ── Thinking pass ─────────────────────────────────────────────────
        thinking = await self._think(task, past_context)
        if thinking:
            await self._bus.publish(
                self.department, "agent_thinking", self.name,
                {"thinking": thinking[:500]}, role=self.role,
            )

        # ── Agentic loop: executive decides each step dynamically ─────────
        step_history: list[dict] = []
        summary = ""

        for step_num in range(MAX_STEPS):
            action = await self._next_action(task, thinking, step_history)

            if action.get("done"):
                raw_summary = action.get("summary") or ""
                if raw_summary:
                    summary = raw_summary
                    logger.info(f"[Executive:{self.name}] Done after {step_num} steps.")
                else:
                    logger.warning(
                        f"[Executive:{self.name}] _next_action returned no summary at step {step_num} "
                        f"— will synthesize from step history"
                    )
                break

            worker_type = action.get("worker", "")
            title = action.get("title", f"Step {step_num + 1}")
            atomic_task = action.get("task", "")

            if not atomic_task:
                logger.warning(f"[Executive:{self.name}] Empty task in action: {action}")
                continue

            # Dedup guard: if this exact title has already failed/been tried twice, skip it
            prior_attempts = sum(1 for s in step_history if s["title"] == title)
            if prior_attempts >= 2:
                logger.warning(
                    f"[Executive:{self.name}] Title '{title}' has been attempted {prior_attempts}x already — skipping to avoid loop"
                )
                step_history.append({
                    "step": step_num + 1,
                    "worker": worker_type,
                    "title": title,
                    "result": "(skipped — already attempted twice with no progress)",
                })
                continue

            logger.info(f"[Executive:{self.name}] Step {step_num + 1}: [{worker_type}] {title}")
            await self._bus.publish(
                self.department, "agent_thinking", self.name,
                {"thinking": f"Step {step_num + 1}: [{worker_type}] {title}"},
                role=self.role,
            )

            result = await self._run_worker_task(title, atomic_task, worker_type)

            # Keep results capped so the executive's context doesn't explode
            step_history.append({
                "step": step_num + 1,
                "worker": worker_type,
                "title": title,
                "result": result[:800] if result else "(no result)",
            })

        # ── If we exhausted steps without a done signal, synthesize ───────
        if not summary:
            results_text = "\n".join(
                f"[{s['step']}] {s['worker']}: {s['title']} → {s['result']}"
                for s in step_history
            )
            summary = await self._synthesize(task, results_text)

        # ── Store in long-term memory ─────────────────────────────────────
        results_text = "\n".join(
            f"[{s['step']}] {s['worker']}: {s['title']} → {s['result']}"
            for s in step_history
        )
        await self._memory.store(
            f"Task: {task}\nResults:\n{results_text}",
            {"department": self.department, "agent": self.name},
        )

        await self._bus.publish(self.department, "agent_finished", self.name,
                                {"summary": summary[:300]}, role=self.role)
        return summary
