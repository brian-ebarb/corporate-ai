import json
import asyncio
import logging
import urllib.request
import urllib.error
from typing import TYPE_CHECKING

try:
    import litellm
except ImportError:
    litellm = None

from prompts import load_prompt
from memory.conversation_store import ConversationStore
from memory.context_manager import ContextManager
from memory.vector_memory import VectorMemory
from memory.daily_memory import DailyMemory

if TYPE_CHECKING:
    from orchestrator.event_bus import EventBus
    from agents.executive_agent import ExecutiveAgent

logger = logging.getLogger(__name__)

# Module-level conversation store — shared across all requests
_conv_store = ConversationStore()

# Ollama base URL — override via OLLAMA_HOST env var if needed
import os
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class SupervisorAgent:
    def __init__(self, name: str, role: str, model: str, event_bus: "EventBus",
                 executives: list, context_length: int = 32768,
                 compaction_threshold: float = 0.75):
        self.name = name
        self.role = role
        self.model = model
        self._bus = event_bus
        self._executives = executives

        # Load context files once at startup — editable without restarting
        self._context = load_prompt("COMPANY", "USER", "SUPERVISOR")

        # Memory & context management
        self._ctx_mgr = ContextManager(model, context_length, compaction_threshold)
        self._daily_mem = DailyMemory("supervisor")
        self._vector_mem = VectorMemory("supervisor")
        self._last_call: dict = {}   # populated after every LiteLLM completion

        logger.info(
            f"[Supervisor] {name} ready — context window: {context_length} tokens, "
            f"compaction at {int(compaction_threshold*100)}%"
        )

    # ── LLM call logging ──────────────────────────────────────────────────

    @staticmethod
    def _models_match(configured: str, actual: str) -> bool:
        """
        Compare configured vs actual model strings, tolerating provider prefix
        differences.  e.g. "openrouter/moonshotai/kimi-k2.5" matches
        "moonshotai/kimi-k2.5", and "ollama/deepseek-r1:latest" matches
        "deepseek-r1:latest".
        """
        if configured == actual:
            return True
        # One may be a trailing segment of the other
        return configured.endswith("/" + actual) or actual.endswith("/" + configured)

    def _record_usage(self, resp, label: str):
        """Extract model + token usage from a LiteLLM response and log it."""
        try:
            # Prefer the raw response JSON "model" field — most reliable source.
            # resp.model_dump() returns the full ModelResponse as a dict.
            actual_model = None
            try:
                rd = resp.model_dump() if hasattr(resp, "model_dump") else {}
                actual_model = rd.get("model") or None
            except Exception:
                pass
            # Fall back to the attribute (may echo configured model for some providers)
            if not actual_model:
                actual_model = getattr(resp, "model", None)
            actual_model = actual_model or self.model

            usage = getattr(resp, "usage", None)
            prompt_tok     = getattr(usage, "prompt_tokens",     0) if usage else 0
            completion_tok = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tok      = getattr(usage, "total_tokens",      0) if usage else 0

            match = self._models_match(self.model, actual_model)
            self._last_call = {
                "label":          label,
                "configured":     self.model,
                "actual":         actual_model,
                "prompt_tokens":  prompt_tok,
                "completion_tok": completion_tok,
                "total_tokens":   total_tok,
                "match":          match,
            }
            logger.info(
                f"[Supervisor] {label} | configured={self.model} | actual={actual_model} "
                f"| {'OK' if match else 'MISMATCH'} "
                f"| tokens: prompt={prompt_tok} completion={completion_tok} total={total_tok}"
            )
            if not match:
                logger.warning(
                    f"[Supervisor] MODEL MISMATCH: expected '{self.model}', got '{actual_model}'"
                )
        except Exception as e:
            logger.debug(f"[Supervisor] _record_usage failed: {e}")

    # ── Slash command handler ──────────────────────────────────────────────

    def _detect_provider(self) -> str:
        m = self.model.lower()
        if m.startswith("ollama/") or m.startswith("ollama_chat/"):
            return "ollama"
        if m.startswith("claude") or m.startswith("anthropic/"):
            return "anthropic"
        if m.startswith("gpt") or m.startswith("openai/") or m.startswith("o1") or m.startswith("o3"):
            return "openai"
        if m.startswith("openrouter/"):
            return "openrouter"
        return "unknown"

    def _http_get(self, url: str, headers: dict | None = None) -> dict:
        """Blocking HTTP GET — run in a thread."""
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return {"ok": True, "data": json.loads(resp.read().decode())}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code} {e.reason}"}
        except urllib.error.URLError as e:
            return {"ok": False, "error": str(e.reason)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fetch_status(self) -> str:
        provider = self._detect_provider()

        if provider == "ollama":
            result = self._http_get(f"{OLLAMA_BASE}/api/ps")
            if not result["ok"]:
                return (
                    f"Ollama ({OLLAMA_BASE}): unreachable - {result['error']}"
                    + self._format_last_call()
                )
            data   = result["data"]
            models = data.get("models", [])
            if not models:
                return (
                    f"Configured: {self.model}\n"
                    f"Ollama ({OLLAMA_BASE}): running, no models currently loaded in memory.\n"
                    f"(Cloud models served via Ollama may not appear in /api/ps between requests.)"
                    + self._format_last_call()
                )
            lines = [f"Configured: {self.model}\n"
                     f"Ollama ({OLLAMA_BASE}) - models in memory:\n"]
            for m in models:
                name    = m.get("name", "unknown")
                details = m.get("details", {})
                params  = details.get("parameter_size", "?")
                quant   = details.get("quantization_level", "?")
                ram_gb  = m.get("size", 0) / 1_073_741_824
                vram_gb = m.get("size_vram", 0) / 1_073_741_824
                expires = m.get("expires_at", "")
                exp_str = f"\n  Expires: {expires[:19].replace('T', ' ')}" if expires else ""
                lines.append(
                    f"  {name} ({params}, {quant})\n"
                    f"  RAM: {ram_gb:.2f} GB  |  VRAM: {vram_gb:.2f} GB{exp_str}"
                )
            return "\n".join(lines) + self._format_last_call()

        if provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            key_hint = f"sk-ant-...{api_key[-4:]}" if len(api_key) > 8 else ("(not set)" if not api_key else "(set)")
            result = self._http_get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            status = "connected" if result["ok"] else f"unreachable - {result['error']}"
            return (
                f"Provider:   Anthropic\n"
                f"Model:      {self.model}\n"
                f"API key:    {key_hint}\n"
                f"API status: {status}"
                + self._format_last_call()
            )

        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            key_hint = f"sk-...{api_key[-4:]}" if len(api_key) > 8 else ("(not set)" if not api_key else "(set)")
            result = self._http_get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            status = "connected" if result["ok"] else f"unreachable - {result['error']}"
            return (
                f"Provider:   OpenAI\n"
                f"Model:      {self.model}\n"
                f"API key:    {key_hint}\n"
                f"API status: {status}"
                + self._format_last_call()
            )

        if provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            key_hint = f"sk-or-...{api_key[-4:]}" if len(api_key) > 8 else ("(not set)" if not api_key else "(set)")
            result = self._http_get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            status = "connected" if result["ok"] else f"unreachable - {result['error']}"
            return (
                f"Provider:   OpenRouter\n"
                f"Model:      {self.model}\n"
                f"API key:    {key_hint}\n"
                f"API status: {status}"
                + self._format_last_call()
            )

        # Fallback for unrecognised provider strings
        result = f"Model: {self.model}\nProvider: unknown (could not detect from model string)"

        if self._last_call:
            result += "\n" + self._format_last_call()
        return result

    def _format_last_call(self) -> str:
        c = self._last_call
        if not c:
            return "\nLast LLM call: none yet this session"
        configured = c.get("configured", "?")
        actual     = c.get("actual", "?")
        match      = c.get("match", configured == actual)
        match_str  = "OK" if match else f"MISMATCH"
        return (
            f"\nLast LLM call ({c.get('label', '?')}):\n"
            f"  Configured: {configured}\n"
            f"  Actual:     {actual}  [{match_str}]\n"
            f"  Tokens:     {c.get('prompt_tokens', 0)} prompt  "
            f"{c.get('completion_tok', 0)} completion  "
            f"{c.get('total_tokens', 0)} total"
        )

    async def _handle_slash_command(self, message: str, session_id: str) -> "str | None":
        """
        Intercept slash commands before LLM routing.
        Returns a reply string if the command was handled, None otherwise.
        """
        cmd = message.strip().lower().split()[0] if message.strip().startswith("/") else ""

        if cmd == "/status":
            reply  = await asyncio.to_thread(self._fetch_status)
            _conv_store.append(session_id, "user", message)
            _conv_store.append(session_id, "assistant", reply)
            await asyncio.to_thread(self._daily_mem.log, message, "Slash command: /status", reply)
            await self._bus.publish("executive", "message_posted", self.name,
                                    {"message": reply}, role="supervisor")
            await self._bus.publish("executive", "agent_finished", self.name,
                                    {"result": reply[:400]}, role="supervisor")
            return reply

        if cmd == "/reset-memory":
            reply = await self._reset_memory(session_id)
            await self._bus.publish("executive", "message_posted", self.name,
                                    {"message": reply}, role="supervisor")
            await self._bus.publish("executive", "agent_finished", self.name,
                                    {"result": reply[:400]}, role="supervisor")
            return reply

        return None

    async def _reset_memory(self, session_id: str) -> str:
        import shutil
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cleared = []
        errors = []

        # Short-term: clear all sessions
        try:
            _conv_store._sessions.clear()
            _conv_store._save()
            cleared.append("conversations")
        except Exception as e:
            errors.append(f"conversations: {e}")

        # Mid-term: delete daily logs
        try:
            daily_dir = os.path.join(root, "memory", "daily")
            if os.path.isdir(daily_dir):
                for f in os.listdir(daily_dir):
                    if f.endswith(".md"):
                        os.unlink(os.path.join(daily_dir, f))
            cleared.append("daily logs")
        except Exception as e:
            errors.append(f"daily logs: {e}")

        # Long-term: wipe ChromaDB and reset ALL collection references in process
        try:
            memory_dir = os.path.join(root, ".memory")
            if os.path.isdir(memory_dir):
                shutil.rmtree(memory_dir)
            from memory.vector_memory import VectorMemory
            n = VectorMemory.reset_all()   # invalidates supervisor + all executives
            cleared.append(f"vector memory ({n} agents)")
        except Exception as e:
            errors.append(f"vector memory: {e}")

        if errors:
            reply = f"Memory reset partially complete.\nCleared: {', '.join(cleared)}\nErrors: {', '.join(errors)}"
        else:
            reply = f"All memory cleared: {', '.join(cleared)}.\nConversation history, daily logs, and vector memory have been wiped. Starting fresh."
        return reply

    # ── Prompts ────────────────────────────────────────────────────────────

    def _exec_roster(self) -> str:
        return "\n".join(
            f"- {e.name} ({e.role}, department key: `{e.department}`)"
            for e in self._executives
        )

    def _dept_keys(self) -> str:
        return ", ".join(e.department for e in self._executives)

    def _build_thinking_prompt(self) -> str:
        from agents.executive_agent import _load_skills_summary
        skills_block = _load_skills_summary()
        skills_section = f"\n\n{skills_block}" if skills_block else ""
        return f"""{self._context}

---

You are {self.name}, {self.role}. Before you act, think through the request carefully.

## Your Executives
{self._exec_roster()}{skills_section}

## What to reason about

1. What is the user actually asking for? What does complete success look like?
2. Is this simple (one executive can handle it) or complex (multiple executives in sequence)?
3. Which executives are needed? In what order? What does each one need to do?
4. Are there dependencies between steps? (e.g. research must finish before building starts, \
or Rita's market data feeds into both Paul's architecture and Michael's GTM)
5. What context needs to flow from one step to the next? Name the files or outputs explicitly.
6. Are any skills relevant? Name them — executives will tell workers to load them.
7. What should I tell the user right now so they feel informed before I disappear to work?

Think out loud. Be direct and specific. Under 500 words."""

    def _build_planning_prompt(self) -> str:
        return f"""{self._context}

---

You are {self.name}, {self.role}. You have thought through the request. Now produce your action plan.

## Your Executives
{self._exec_roster()}

Available department keys: {self._dept_keys()}

## Response Format

**For tasks requiring work — single or multi-step:**
{{
  "acknowledgment": "Message sent to the user RIGHT NOW before any work starts. Confirm you understand the goal, outline your plan clearly, name who is doing what, set honest expectations about scope. This is the user's only visibility until you report back — make it count.",
  "steps": [
    {{"executive": "<dept key>", "title": "<short step name>", "task": "<complete self-contained instructions for this executive. Include file paths where prior steps will deposit results so this executive knows where to look.>"}},
    ...
  ]
}}

**For direct replies (greetings, status questions, things you can answer yourself):**
{{"reply": "..."}}

## Planning rules

- Steps execute **in order**. An executive only starts after the previous one finishes.
- If step 2 depends on step 1's output, say so explicitly in step 2's task: \
"Research results will be at `research/<topic>.md` — read it before proceeding."
- Each step's task must be fully self-contained — the executive only sees their own instructions.
- Name relevant skills in task instructions so the executive tells workers to load them.
- acknowledgment goes to the user immediately — be direct, professional, specific.
- For complex multi-step plans, tell the user how many phases there are and roughly what each covers.
- Respond with ONLY the JSON object. No markdown fences, no explanation outside the JSON."""

    def _build_synthesis_prompt(self, steps: list[dict]) -> str:
        step_summary = "\n".join(
            f"- Step {i+1} ({s['executive']}): {s['title']}"
            for i, s in enumerate(steps)
        )
        return f"""{self._context}

---

You are {self.name}, {self.role}. Your executives have completed all assigned work.

Steps that were executed:
{step_summary}

Write the final report to the user as PLAIN TEXT — no JSON, no markdown code fences.
- Lead with what was accomplished overall
- Cover each major deliverable: what was built/researched/planned, file paths, key outcomes
- Flag anything that failed or needs follow-up
- Close with next steps or what the user can do now
Be thorough but concise. This is the user's complete project report."""

    # ── LLM helpers ────────────────────────────────────────────────────────

    async def _think(self, message: str, history: list, daily_log: str, recalled: list) -> str:
        """Reasoning pass — Bob thinks before planning."""
        context_blocks = ""
        if daily_log:
            context_blocks += f"\n\n## Today's activity log:\n{daily_log}"
        if recalled:
            context_blocks += "\n\n## Relevant past context:\n" + "\n---\n".join(recalled)

        messages = [
            {"role": "system", "content": self._build_thinking_prompt() + context_blocks},
            *history[-6:],
            {"role": "user", "content": message},
        ]
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.5,
            )
            self._record_usage(resp, "thinking")
            thinking = resp.choices[0].message.content.strip()
            logger.info(f"[Supervisor] Thinking complete ({len(thinking)} chars)")
            return thinking
        except Exception as e:
            logger.error(f"[Supervisor] Think error: {e}")
            return ""

    async def _plan(self, message: str, thinking: str, history: list,
                    daily_log: str, recalled: list) -> dict:
        """Planning pass — produces acknowledgment + ordered steps (or direct reply)."""
        context_blocks = ""
        if daily_log:
            context_blocks += f"\n\n## Today's activity log:\n{daily_log}"
        if recalled:
            context_blocks += "\n\n## Relevant past context:\n" + "\n---\n".join(recalled)

        messages = [
            {"role": "system", "content": self._build_planning_prompt() + context_blocks},
            *history,
            {"role": "user", "content": message},
        ]
        if thinking:
            messages.append({"role": "assistant", "content": f"<thinking>\n{thinking}\n</thinking>"})
            messages.append({"role": "user", "content": "Now output your action plan as JSON."})

        if self._ctx_mgr.needs_compaction(messages):
            messages = await self._ctx_mgr.compact(messages)

        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.3,
            )
            self._record_usage(resp, "planning")
            raw = resp.choices[0].message.content.strip()
            # Strip think tags that some models emit even when not asked
            import re as _re
            raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(raw[start:end])
                logger.info(
                    f"[Supervisor] Plan: "
                    f"{'direct reply' if 'reply' in plan else str(len(plan.get('steps', []))) + ' step(s)'}"
                )
                return plan
        except Exception as e:
            logger.error(f"[Supervisor] Planning error: {e}")

        # Fallback: route to first executive
        return {
            "acknowledgment": "On it — I'll get the team working on this now.",
            "steps": [{
                "executive": self._executives[0].department if self._executives else "",
                "title": "Handle request",
                "task": message,
            }],
        }

    async def _synthesize(self, message: str, history: list,
                          steps: list[dict], all_results: list[dict]) -> str:
        """Final synthesis — Bob reports back to the user across all steps."""
        results_block = "\n\n".join(
            f"### Step {i+1}: {r['title']} ({r['executive']})\n{r['result']}"
            for i, r in enumerate(all_results)
        )
        messages = [
            {"role": "system", "content": self._build_synthesis_prompt(steps)},
            *history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": f"[All executive reports]\n\n{results_block}"},
            {"role": "user", "content": "Write your final report to the user now."},
        ]
        if self._ctx_mgr.needs_compaction(messages):
            messages = await self._ctx_mgr.compact(messages)
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=messages,
                temperature=0.5,
            )
            self._record_usage(resp, "synthesis")
            text = resp.choices[0].message.content.strip()
            # Strip JSON if model ignored plain-text instruction
            if text.startswith("{"):
                try:
                    parsed = json.loads(text[text.find("{"):text.rfind("}")+1])
                    if "reply" in parsed:
                        text = parsed["reply"]
                except Exception:
                    pass
            return text
        except Exception as e:
            logger.error(f"[Supervisor] Synthesis error: {e}")
            return results_block or "Work completed."

    def _find_executive(self, dept: str):
        for e in self._executives:
            if e.department == dept:
                return e
        return self._executives[0] if self._executives else None

    # ── Main entry point ───────────────────────────────────────────────────

    async def handle(self, message: str, session_id: str = "default") -> str:
        await self._bus.publish("executive", "agent_started", self.name,
                                {"message": message[:200]}, role=self.role)

        # ── Slash commands ────────────────────────────────────────────────
        slash_result = await self._handle_slash_command(message, session_id)
        if slash_result is not None:
            return slash_result

        # ── Load all memory ───────────────────────────────────────────────
        history    = _conv_store.get_messages(session_id)
        daily_log  = await asyncio.to_thread(self._daily_mem.read_recent, 24, 6000)
        recalled   = await self._vector_mem.recall(message, n_results=3)

        # ── Thinking pass ─────────────────────────────────────────────────
        thinking = await self._think(message, history, daily_log, recalled)
        if thinking:
            await self._bus.publish("executive", "agent_thinking", self.name,
                                    {"thinking": thinking[:600]}, role=self.role)

        # ── Planning pass ─────────────────────────────────────────────────
        plan = await self._plan(message, thinking, history, daily_log, recalled)

        # ── Direct reply ──────────────────────────────────────────────────
        if "reply" in plan:
            reply_text = plan["reply"]
            _conv_store.append(session_id, "user", message)
            _conv_store.append(session_id, "assistant", reply_text)
            await self._vector_mem.store(
                f"User: {message}\n{self.name}: {reply_text}",
                {"session": session_id, "type": "direct_reply"},
            )
            await asyncio.to_thread(self._daily_mem.log, message, "Direct reply", reply_text)
            await self._bus.publish("executive", "message_posted", self.name,
                                    {"message": reply_text}, role="supervisor")
            await self._bus.publish("executive", "agent_finished", self.name,
                                    {"result": reply_text[:400]}, role="supervisor")
            return reply_text

        # ── Acknowledge the user immediately before any work starts ───────
        acknowledgment = plan.get("acknowledgment", "On it.")
        steps          = plan.get("steps", [])

        # Publish as message_posted so it appears in chat right away,
        # before the long wait while executives run.
        await self._bus.publish("executive", "message_posted", self.name,
                                {"message": acknowledgment}, role="supervisor")
        logger.info(f"[Supervisor] Acknowledged user. Executing {len(steps)} step(s).")

        # ── Execute steps sequentially ────────────────────────────────────
        all_results: list[dict] = []
        prior_context = ""

        for i, step in enumerate(steps):
            dept    = step.get("executive", "")
            title   = step.get("title", f"Step {i+1}")
            task    = step.get("task", message)
            chosen  = self._find_executive(dept)

            if not chosen:
                logger.warning(f"[Supervisor] No executive for dept '{dept}', skipping step '{title}'")
                all_results.append({"executive": dept, "title": title,
                                    "result": f"(skipped — no executive for '{dept}')"})
                continue

            # Inject prior steps' outputs so each executive has full context
            if prior_context:
                task = task + f"\n\n---\n\n## Outputs from prior steps\n{prior_context}"

            # Safety net: always include the full original user message
            if len(message) > len(step.get("task", "")) + 50:
                task = task + (
                    f"\n\n---\n\n## Full original user request\n{message}"
                )

            # Log delegation state so heartbeats see it
            delegation_note = (
                f"TASK IN PROGRESS — step {i+1}/{len(steps)}: '{title}' "
                f"delegated to {chosen.name} ({chosen.department}).\n"
                f"User request: {message[:200]}"
            )
            await asyncio.to_thread(
                self._daily_mem.log, message,
                f"Step {i+1}/{len(steps)}: {chosen.name} ({chosen.department}) — AWAITING",
                delegation_note,
            )
            await self._vector_mem.store(
                delegation_note,
                {"session": session_id, "type": "delegation_in_progress",
                 "step": i+1, "department": chosen.department},
            )

            await self._bus.publish("executive", "message_posted", self.name,
                                    {"to": chosen.name, "step": f"{i+1}/{len(steps)}",
                                     "title": title}, role=self.role)

            logger.info(f"[Supervisor] Step {i+1}/{len(steps)}: [{chosen.department}] {title}")
            result = await chosen.handle(task)

            all_results.append({"executive": dept, "title": title, "result": result})
            prior_context += f"\n\n### {title} ({chosen.name})\n{result[:1000]}"

        # ── Synthesis ─────────────────────────────────────────────────────
        # Reload history (may have changed during long execution)
        history = _conv_store.get_messages(session_id)
        final_text = await self._synthesize(message, history, steps, all_results)

        # ── Persist ───────────────────────────────────────────────────────
        _conv_store.append(session_id, "user", message)
        _conv_store.append(session_id, "assistant", final_text)

        depts = ", ".join(r["executive"] for r in all_results)
        await self._vector_mem.store(
            f"User: {message}\nSteps ({depts}): "
            + " | ".join(f"{r['title']}: {r['result'][:200]}" for r in all_results)
            + f"\n{self.name}: {final_text}",
            {"session": session_id, "type": "delegated", "departments": depts},
        )
        await asyncio.to_thread(
            self._daily_mem.log, message,
            f"Completed {len(steps)} step(s) via {depts}",
            final_text,
        )

        await self._bus.publish("executive", "message_posted", self.name,
                                {"message": final_text}, role="supervisor")
        await self._bus.publish("executive", "agent_finished", self.name,
                                {"result": final_text[:400]}, role="supervisor")
        return final_text
