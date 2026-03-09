import json
import asyncio
import logging
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


class ExecutiveAgent:
    def __init__(self, name: str, role: str, department: str, model: str,
                 event_bus, task_queue, workers: dict,
                 context_length: int = 16384, compaction_threshold: float = 0.75):
        self.name = name
        self.role = role
        self.department = department
        self.model = model
        self._bus = event_bus
        self._task_queue = task_queue
        self._workers = workers

        # Load shared executive context + company structure
        self._context = load_prompt("COMPANY", "EXECUTIVE")

        # Memory & context management
        self._memory = VectorMemory(f"{self.department}_exec")
        self._ctx_mgr = ContextManager(model, context_length, compaction_threshold)

        logger.info(
            f"[Executive] {name} ({department}) ready — "
            f"context window: {context_length} tokens, "
            f"compaction at {int(compaction_threshold*100)}%"
        )

    def _build_breakdown_prompt(self) -> str:
        worker_types = list(self._workers.keys())
        return f"""{self._context}

---

You are {self.name}, {self.role} at Corporate AI.
Your available workers: {', '.join(worker_types)}

Break the task into 1–3 sub-tasks. Respond with ONLY the JSON array specified above.
worker_type must be one of: {', '.join(worker_types)}"""

    def _build_synthesis_prompt(self) -> str:
        return f"""{self._context}

---

You are {self.name}, {self.role}. Your workers have completed their tasks.
Write a concise executive summary of what was accomplished.
Include file paths, key outputs, and any issues encountered."""

    async def handle(self, task: str) -> str:
        await self._bus.publish(self.department, "agent_started", self.name,
                                {"task": task[:200]}, role=self.role)

        # ── Long-term memory: recall relevant past work ───────────────────────
        past_context = ""
        recalled = await self._memory.recall(task, n_results=3)
        if recalled:
            past_context = "\n\nRelevant past work:\n" + "\n---\n".join(recalled[:3])

        # ── Break task into sub-tasks ─────────────────────────────────────────
        breakdown_prompt = self._build_breakdown_prompt()
        breakdown_messages = [
            {"role": "system", "content": breakdown_prompt},
            {"role": "user", "content": task + past_context},
        ]

        if self._ctx_mgr.needs_compaction(breakdown_messages):
            breakdown_messages = await self._ctx_mgr.compact(breakdown_messages)

        subtasks = []
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=breakdown_messages,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                subtasks = json.loads(raw[start:end])
        except Exception as e:
            logger.error(f"[Executive:{self.name}] Breakdown error: {e}")
            worker_types = list(self._workers.keys())
            wtype = worker_types[0] if worker_types else "coder"
            subtasks = [{"title": task[:60], "description": task, "worker_type": wtype}]

        # ── Enqueue sub-tasks ─────────────────────────────────────────────────
        from orchestrator.task_queue import Task as TaskObj
        worker_types = list(self._workers.keys())
        pending_tasks = []
        for st in subtasks[:3]:
            wtype = st.get("worker_type", worker_types[0] if worker_types else "coder")
            if wtype not in self._workers:
                wtype = worker_types[0] if worker_types else "coder"
            worker = self._workers.get(wtype)
            t = TaskObj(
                title=st.get("title", "Task"),
                description=st.get("description", task),
                worker_type=wtype,
                assigned_to=self.name,
                worker=worker,
            )
            await self._task_queue.enqueue(t)
            pending_tasks.append(t)

        # ── Wait for all tasks to complete (5 min timeout) ────────────────────
        timeout = 300
        elapsed = 0
        while elapsed < timeout:
            if all(t.status in ("completed", "failed") for t in pending_tasks):
                break
            await asyncio.sleep(2)
            elapsed += 2

        results = [f"[{t.title}] ({t.status}): {t.result or 'no result'}" for t in pending_tasks]
        results_text = "\n\n".join(results)

        # ── Store results in long-term memory ─────────────────────────────────
        await self._memory.store(
            f"Task: {task}\nResults:\n{results_text}",
            {"department": self.department, "agent": self.name},
        )

        # ── Synthesize executive summary ──────────────────────────────────────
        synth_prompt = self._build_synthesis_prompt()
        synth_messages = [
            {"role": "system", "content": synth_prompt},
            {"role": "user", "content": f"Original task: {task}"},
            {"role": "assistant", "content": f"Worker results:\n{results_text}"},
            {"role": "user", "content": "Write your executive summary now."},
        ]

        if self._ctx_mgr.needs_compaction(synth_messages):
            synth_messages = await self._ctx_mgr.compact(synth_messages)

        try:
            final = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=synth_messages,
                temperature=0.4,
            )
            summary = final.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[Executive:{self.name}] Synthesis error: {e}")
            summary = results_text or f"Tasks completed. (synthesis error: {e})"

        await self._bus.publish(self.department, "agent_finished", self.name,
                                {"summary": summary[:300]}, role=self.role)
        return summary
