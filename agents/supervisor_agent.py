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
        self._context = load_prompt("COMPANY", "SUPERVISOR")

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
                                    {"message": reply[:500]}, role="supervisor")
            await self._bus.publish("executive", "agent_finished", self.name,
                                    {"result": reply[:200]}, role="supervisor")
            return reply

        return None

    # ── System prompt ──────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        exec_list = "\n".join(
            f"- {e.name} ({e.role}, department: {e.department})"
            for e in self._executives
        )
        dept_options = ", ".join(e.department for e in self._executives)
        return f"""{self._context}

---

## Active Executives
{exec_list}

Available department keys: {dept_options}

Remember: respond to delegation decisions with ONLY the JSON object specified above.
No markdown, no explanation, just the JSON."""

    # ── Main entry point ───────────────────────────────────────────────────

    async def handle(self, message: str, session_id: str = "default") -> str:
        await self._bus.publish("executive", "agent_started", self.name,
                                {"message": message[:200]}, role=self.role)

        # ── Slash commands: handled directly, no LLM involved ─────────────
        slash_result = await self._handle_slash_command(message, session_id)
        if slash_result is not None:
            return slash_result

        system_prompt = self._build_system_prompt()

        # ── Short-term memory: load conversation history ──────────────────
        history = _conv_store.get_messages(session_id)

        # ── Mid-term memory: today's activity log ─────────────────────────
        daily_log = await asyncio.to_thread(self._daily_mem.read_recent, 24, 6000)
        daily_block = ""
        if daily_log:
            daily_block = "\n\n## Today's Activity Log:\n" + daily_log

        # ── Long-term memory: recall relevant past context ────────────────
        recalled = await self._vector_mem.recall(message, n_results=3)
        ltm_block = ""
        if recalled:
            ltm_block = "\n\n## Relevant past context:\n" + "\n---\n".join(recalled)

        # Build full message list for routing LLM call
        routing_messages = (
            [{"role": "system", "content": system_prompt + daily_block + ltm_block}]
            + history
            + [{"role": "user", "content": message}]
        )

        # ── Context compaction ────────────────────────────────────────────
        if self._ctx_mgr.needs_compaction(routing_messages):
            to_compact = routing_messages[:-1]
            compacted = await self._ctx_mgr.compact(to_compact)
            routing_messages = compacted + [{"role": "user", "content": message}]
            new_hist = [m for m in routing_messages[1:-1]
                        if m.get("role") != "system" or
                        m.get("content", "").startswith("## Conversation summary")]
            _conv_store.replace_messages(session_id, new_hist)
            history = _conv_store.get_messages(session_id)

        # ── Routing LLM call ──────────────────────────────────────────────
        try:
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=routing_messages,
                temperature=0.3,
            )
            self._record_usage(resp, "routing")
            raw = resp.choices[0].message.content.strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                decision = json.loads(raw[start:end])
            else:
                decision = {
                    "executive": self._executives[0].department if self._executives else "engineering",
                    "reason": "fallback - could not parse JSON",
                    "delegated_task": message,
                }
        except Exception as e:
            logger.error(f"[Supervisor] Routing LLM error: {e}")
            decision = {
                "executive": self._executives[0].department if self._executives else "engineering",
                "reason": f"error: {e}",
                "delegated_task": message,
            }

        # ── Direct reply — no delegation ──────────────────────────────────
        if "reply" in decision:
            reply_text = decision["reply"]
            _conv_store.append(session_id, "user", message)
            _conv_store.append(session_id, "assistant", reply_text)
            await self._vector_mem.store(
                f"User: {message}\nBob: {reply_text}",
                {"session": session_id, "type": "direct_reply"},
            )
            await asyncio.to_thread(self._daily_mem.log, message, "Direct reply", reply_text)
            await self._bus.publish("executive", "message_posted", self.name,
                                    {"message": reply_text[:500]}, role="supervisor")
            await self._bus.publish("executive", "agent_finished", self.name,
                                    {"result": reply_text[:200]}, role="supervisor")
            return reply_text

        # ── Route to chosen executive ─────────────────────────────────────
        chosen = None
        for exec_agent in self._executives:
            if exec_agent.department == decision.get("executive"):
                chosen = exec_agent
                break
        if not chosen and self._executives:
            chosen = self._executives[0]

        await self._bus.publish("executive", "message_posted", self.name,
                                {"to": chosen.name if chosen else "unknown",
                                 "reason": decision.get("reason", ""),
                                 "task": decision.get("delegated_task", message)},
                                role=self.role)

        exec_result = ""
        if chosen:
            exec_result = await chosen.handle(decision.get("delegated_task", message))

        # ── Synthesis — final response to user ────────────────────────────
        synth_system = (
            f"{self._context}\n\n"
            f"You have just received a completed executive report. "
            f"Write the final response to the user. "
            f"Be direct, professional, and concise. Lead with the result.\n"
            f"Executive who handled it: "
            f"{chosen.name if chosen else 'unknown'} "
            f"({chosen.role if chosen else ''})"
        )

        synth_messages = [
            {"role": "system", "content": synth_system},
            *history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": f"[Executive report received]\n{exec_result}"},
            {"role": "user", "content": "Now write your final response to the user based on this report."},
        ]

        if self._ctx_mgr.needs_compaction(synth_messages):
            synth_messages = await self._ctx_mgr.compact(synth_messages)

        try:
            final_resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=synth_messages,
                temperature=0.5,
            )
            self._record_usage(final_resp, "synthesis")
            final_text = final_resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[Supervisor] Synthesis LLM error: {e}")
            final_text = exec_result or f"Task completed. (synthesis error: {e})"

        # ── Persist to all three memory layers ────────────────────────────
        _conv_store.append(session_id, "user", message)
        _conv_store.append(session_id, "assistant", final_text)

        dept = chosen.department if chosen else "unknown"
        await self._vector_mem.store(
            f"User: {message}\nExecutive ({dept}): "
            f"{exec_result[:500]}\nBob: {final_text}",
            {"session": session_id, "type": "delegated", "department": dept},
        )
        await asyncio.to_thread(
            self._daily_mem.log,
            message,
            f"Delegated to {chosen.name if chosen else 'unknown'} ({dept})",
            final_text,
        )

        await self._bus.publish("executive", "message_posted", self.name,
                                {"message": final_text[:500]}, role="supervisor")
        await self._bus.publish("executive", "agent_finished", self.name,
                                {"result": final_text[:200]}, role="supervisor")
        return final_text
