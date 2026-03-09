import asyncio
import json
import logging
from typing import TYPE_CHECKING

try:
    import litellm
except ImportError:
    litellm = None

from prompts import load_prompt
from memory.context_manager import ContextManager

if TYPE_CHECKING:
    from orchestrator.task_queue import Task
    from orchestrator.event_bus import EventBus

logger = logging.getLogger(__name__)


class WorkerBase:
    tools: list = []
    tool_schemas: list = []

    # Subclasses set this to load their specific prompt file, e.g. "WORKER_CODER"
    prompt_name: str = ""

    def __init__(self, model: str, event_bus=None,
                 context_length: int = 8192, compaction_threshold: float = 0.80):
        self.model = model
        self._bus = event_bus
        self._ctx_mgr = ContextManager(model, context_length, compaction_threshold)
        # Load context once at startup
        self._context = load_prompt(self.prompt_name) if self.prompt_name else ""

        logger.info(
            f"[Worker] {self.__class__.__name__} ready — "
            f"context window: {context_length} tokens, "
            f"compaction at {int(compaction_threshold*100)}%"
        )

    def _build_system_prompt(self, task_description: str) -> str:
        base = self._context if self._context else (
            "You are a specialist AI worker. Complete the assigned task using available tools."
        )
        return f"""{base}

---

## Current Task

{task_description}

Complete this task now. Use your tools. When you are done, return a clear summary of what you did."""

    async def run(self, task: "Task") -> str:
        system = self._build_system_prompt(task.description)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": task.description},
        ]

        max_iterations = 8
        for iteration in range(max_iterations):

            # ── Compact if the tool-call history is growing too large ─────────
            if self._ctx_mgr.needs_compaction(messages):
                messages = await self._ctx_mgr.compact(messages)

            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                }
                if self.tool_schemas:
                    kwargs["tools"] = self.tool_schemas
                    kwargs["tool_choice"] = "auto"

                resp = await asyncio.to_thread(litellm.completion, **kwargs)
                msg = resp.choices[0].message

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    # Append assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    })
                    # Execute each tool call and append results
                    for tc in msg.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        result = await self._call_tool(tc.function.name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result),
                        })
                else:
                    # No more tool calls — model is done
                    return msg.content or "Task completed."

            except Exception as e:
                logger.error(f"[Worker:{self.__class__.__name__}] Error at iteration {iteration}: {e}")
                return f"Worker error (iteration {iteration}): {e}"

        return "Task completed (reached max tool-call iterations)."

    async def _call_tool(self, name: str, args: dict) -> str:
        for tool in self.tools:
            if hasattr(tool, name):
                try:
                    method = getattr(tool, name)
                    if asyncio.iscoroutinefunction(method):
                        return await method(**args)
                    return await asyncio.to_thread(method, **args)
                except Exception as e:
                    return f"Tool error ({name}): {e}"
        available = [t for obj in self.tools for t in dir(obj) if not t.startswith("_")]
        return f"Unknown tool: '{name}'. Available: {available}"
