import asyncio
import json
import logging
import re
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


def _extract_tool_calls(msg) -> list[dict] | None:
    """
    Normalise tool calls from whatever format the model returned.

    LiteLLM maps most providers to the OpenAI tool_calls format, but some
    Ollama models fall back to:
      1. msg.function_call  — deprecated single-call OpenAI format
      2. <tool_call>...</tool_call> XML blocks embedded in msg.content
      3. Bare JSON objects/arrays in msg.content

    Returns a list of dicts with keys: id, name, arguments (dict).
    Returns None if nothing is found.
    """
    # ── 1. Standard tool_calls list ──────────────────────────────────────────
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        return [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": _safe_parse(tc.function.arguments),
            }
            for tc in tool_calls
        ]

    # ── 2. Deprecated function_call field ────────────────────────────────────
    fc = getattr(msg, "function_call", None)
    if fc:
        return [{
            "id": "fc-0",
            "name": fc.name,
            "arguments": _safe_parse(getattr(fc, "arguments", "{}")),
        }]

    # ── 3. <tool_call>JSON</tool_call> XML blocks in content ─────────────────
    content = msg.content or ""
    xml_matches = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL)
    if xml_matches:
        calls = []
        for i, raw in enumerate(xml_matches):
            parsed = _safe_parse(raw)
            if parsed and isinstance(parsed, dict) and "name" in parsed:
                calls.append({
                    "id": f"xml-{i}",
                    "name": parsed["name"],
                    "arguments": parsed.get("arguments") or parsed.get("parameters") or {},
                })
        if calls:
            return calls

    # ── 4. Bare JSON object/array at the top level of content ────────────────
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        parsed = _safe_parse(stripped)
        if isinstance(parsed, dict) and "name" in parsed:
            return [{
                "id": "json-0",
                "name": parsed["name"],
                "arguments": parsed.get("arguments") or parsed.get("parameters") or {},
            }]
        if isinstance(parsed, list):
            calls = []
            for i, item in enumerate(parsed):
                if isinstance(item, dict) and "name" in item:
                    calls.append({
                        "id": f"json-{i}",
                        "name": item["name"],
                        "arguments": item.get("arguments") or item.get("parameters") or {},
                    })
            if calls:
                return calls

    # ── 5. JSON inside a markdown code fence ```json ... ``` ─────────────────
    fence_matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    for i, raw in enumerate(fence_matches):
        parsed = _safe_parse(raw.strip())
        if isinstance(parsed, dict) and "name" in parsed:
            return [{
                "id": f"fence-{i}",
                "name": parsed["name"],
                "arguments": parsed.get("arguments") or parsed.get("parameters") or {},
            }]
        if isinstance(parsed, list):
            calls = []
            for j, item in enumerate(parsed):
                if isinstance(item, dict) and "name" in item:
                    calls.append({
                        "id": f"fence-{i}-{j}",
                        "name": item["name"],
                        "arguments": item.get("arguments") or item.get("parameters") or {},
                    })
            if calls:
                return calls

    return None


def _safe_parse(raw) -> dict | list | None:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


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
        # Load context once at startup — USER gives workers business context
        self._context = load_prompt("USER", self.prompt_name) if self.prompt_name else load_prompt("USER")

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

---

## Tool Use Rules

- You have tools available. Use them — do not just describe what you would do.
- **Writing content in your response text does NOT create files on disk.** Call `write_file` to actually save a file.
- Other workers can only read files you have written to disk via `write_file`. Text in your response is invisible to them.
- When your task is done, return a concise summary: what you did, what files were created or modified, and their paths."""

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

                calls = _extract_tool_calls(msg)

                if calls:
                    # Build the assistant message for the conversation history.
                    # Include tool_calls in the standard format if the original had them,
                    # otherwise just record the content so the history stays coherent.
                    raw_tool_calls = getattr(msg, "tool_calls", None) or []
                    if raw_tool_calls:
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
                                for tc in raw_tool_calls
                            ],
                        })
                    else:
                        # Tool call was parsed from content/function_call — record as assistant text
                        messages.append({"role": "assistant", "content": msg.content or ""})

                    # Execute each extracted call
                    for call in calls:
                        tool_result = await self._call_tool(call["name"], call["arguments"])
                        logger.info(
                            f"[Worker:{self.__class__.__name__}] "
                            f"tool={call['name']} args={str(call['arguments'])[:120]} "
                            f"→ {str(tool_result)[:120]}"
                        )
                        if raw_tool_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": str(tool_result),
                            })
                        else:
                            messages.append({
                                "role": "user",
                                "content": f"Tool result for {call['name']}: {tool_result}",
                            })

                elif iteration == 0 and self.tool_schemas:
                    # Model returned plain text with no tool call on the first turn.
                    # Nudge it to actually call a tool.
                    logger.warning(
                        f"[Worker:{self.__class__.__name__}] No tool call on iteration 0 "
                        f"— nudging. Response was: {(msg.content or '')[:200]}"
                    )
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You did not call any tools. Text in your response is NOT saved to disk. "
                            "Call write_file now to save the file. Do not describe what you will do — just call the tool."
                        ),
                    })

                else:
                    # No tool calls — model signals it is done
                    logger.info(
                        f"[Worker:{self.__class__.__name__}] Done after {iteration} iterations."
                    )
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
        available = [
            attr for obj in self.tools
            for attr in dir(obj)
            if not attr.startswith("_")
        ]
        return f"Unknown tool: '{name}'. Available: {available}"
