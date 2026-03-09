"""
Context Manager — token counting and conversation compaction.

When a message list approaches the model's context_length, compact() summarises
the oldest turns and replaces them with a single summary block, keeping the system
prompt and the most recent exchanges intact.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# How many recent messages to always keep verbatim during compaction
RECENCY_KEEP = 6


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~3 chars per token. Used as fallback."""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            # Multi-part content (e.g. tool results)
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        total += len(str(content))
    return total // 3


def count_tokens(model: str, messages: list[dict]) -> int:
    """Count tokens using LiteLLM, falling back to character estimate."""
    try:
        import litellm
        return litellm.token_counter(model=model, messages=messages)
    except Exception:
        return _estimate_tokens(messages)


class ContextManager:
    """
    Wraps a model's context window limits and handles compaction.

    Usage:
        ctx = ContextManager(model="ollama/deepseek-r1:latest",
                             context_length=32768,
                             compaction_threshold=0.75)
        if ctx.needs_compaction(messages):
            messages = await ctx.compact(messages)
    """

    def __init__(self, model: str, context_length: int = 8192,
                 compaction_threshold: float = 0.80):
        self.model = model
        self.context_length = context_length
        self.compaction_threshold = compaction_threshold

    def count(self, messages: list[dict]) -> int:
        return count_tokens(self.model, messages)

    def needs_compaction(self, messages: list[dict]) -> bool:
        if self.context_length <= 0:
            return False
        used = self.count(messages)
        threshold = int(self.context_length * self.compaction_threshold)
        if used >= threshold:
            logger.info(
                f"[Context] {used}/{self.context_length} tokens used "
                f"({used*100//self.context_length}%) — compaction triggered"
            )
            return True
        return False

    async def compact(self, messages: list[dict]) -> list[dict]:
        """
        Summarise old messages, keeping:
          [0]   system prompt (always)
          [1]   optional existing summary block (if already compacted before)
          [-N:] RECENCY_KEEP most recent messages
        """
        if len(messages) <= RECENCY_KEEP + 1:
            return messages

        # Separate system prompt from the rest
        system_msg = messages[0]
        rest = messages[1:]

        # If rest is short enough to keep verbatim, do nothing
        if len(rest) <= RECENCY_KEEP:
            return messages

        to_summarise = rest[:-RECENCY_KEEP]
        recent = rest[-RECENCY_KEEP:]

        # If there's already a summary block at position 1, include it in the
        # text to be re-summarised rather than duplicating it
        existing_summary = ""
        if (to_summarise and
                to_summarise[0].get("role") == "system" and
                to_summarise[0].get("content", "").startswith("## Conversation summary")):
            existing_summary = to_summarise[0]["content"] + "\n\n"
            to_summarise = to_summarise[1:]

        summary_text = await self._summarise(to_summarise, existing_summary)
        summary_msg = {
            "role": "system",
            "content": f"## Conversation summary (older exchanges compacted):\n{summary_text}",
        }

        compacted = [system_msg, summary_msg] + recent
        saved = self.count(messages) - self.count(compacted)
        logger.info(f"[Context] Compacted {len(to_summarise)} messages, saved ~{saved} tokens")
        return compacted

    async def _summarise(self, messages: list[dict], prefix: str = "") -> str:
        """Ask the same model to write a concise summary of the given messages."""
        if not messages:
            return prefix.strip() or "(no prior history)"

        lines = []
        for m in messages:
            role = m.get("role", "unknown").upper()
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            lines.append(f"{role}: {str(content)[:600]}")

        raw_text = prefix + "\n".join(lines)

        try:
            import litellm
            resp = await asyncio.to_thread(
                litellm.completion,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a conversation summariser. "
                            "Write a concise but complete summary of the following conversation, "
                            "preserving all key facts, decisions, file paths, and outcomes. "
                            "Use bullet points. Be factual, not narrative."
                        ),
                    },
                    {"role": "user", "content": raw_text},
                ],
                temperature=0.1,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[Context] Summarisation LLM call failed: {e} — using truncation fallback")
            # Fallback: just keep a truncated version of the text
            return f"[Compacted history — {len(messages)} turns]\n{raw_text[-1500:]}"
