"""
Daily Memory — mid-term markdown journal.

Writes a timestamped entry for each supervisor interaction to a per-day
markdown file (memory/daily/YYYY-MM-DD.md).  The file persists across
context-window compactions and server restarts, giving agents genuine
"what happened today" awareness without relying on the active message history.

Three-layer memory model
─────────────────────────────────────────────────────────────────────────────
  Short-term  →  memory/conversations.json   (active session turns)
  Mid-term    →  memory/daily/YYYY-MM-DD.md  (today's full activity log)
  Long-term   →  memory/.chroma/             (ChromaDB semantic vectors)
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

DAILY_DIR = Path(__file__).parent.parent / "memory" / "daily"
logger = logging.getLogger(__name__)


class DailyMemory:
    def __init__(self, namespace: str = "supervisor"):
        self._namespace = namespace
        DAILY_DIR.mkdir(parents=True, exist_ok=True)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _today_path(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return DAILY_DIR / f"{date_str}-{self._namespace}.md"

    def _read_tail(self, path: Path, max_chars: int) -> str:
        """Read a file, returning the last max_chars characters."""
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > max_chars:
                # Find the first complete entry boundary after the cut
                cut = text[-max_chars:]
                boundary = cut.find("\n## [")
                if boundary > 0:
                    cut = cut[boundary:]
                return "*(earlier entries omitted)*\n\n" + cut
            return text
        except Exception as e:
            logger.warning(f"[DailyMemory] Failed to read {path.name}: {e}")
            return ""

    # ── Write ─────────────────────────────────────────────────────────────

    def log(self, user_msg: str, action: str, result: str):
        """
        Append a timestamped entry to today's daily log.

        Args:
            user_msg:  Original user message (will be truncated to 400 chars).
            action:    What the supervisor did — e.g. "Delegated to engineering"
                       or "Direct reply".
            result:    Brief outcome — the final response or a summary of it
                       (will be truncated to 600 chars).
        """
        now = datetime.now()
        timestamp = now.strftime("%H:%M")
        path = self._today_path()

        try:
            if not path.exists():
                header = (
                    f"# {self._namespace.title()} Daily Log"
                    f" — {now.strftime('%A, %B %d %Y')}\n\n"
                )
                path.write_text(header, encoding="utf-8")

            entry = (
                f"\n## [{timestamp}]\n"
                f"**Request**: {user_msg[:400]}\n\n"
                f"**Action**: {action}\n\n"
                f"**Result**: {result[:600]}\n\n"
                f"---\n"
            )
            with path.open("a", encoding="utf-8") as f:
                f.write(entry)

        except Exception as e:
            logger.warning(f"[DailyMemory] Failed to write entry: {e}")

    # ── Read ──────────────────────────────────────────────────────────────

    def read_today(self, max_chars: int = 6000) -> str:
        """
        Return today's activity log, capped at max_chars (most recent entries
        kept when truncating).  Returns empty string if no entries yet today.
        """
        return self._read_tail(self._today_path(), max_chars)

    def read_recent(self, hours: int = 24, max_chars: int = 8000) -> str:
        """
        Return entries spanning the last `hours` hours — may include yesterday's
        file for overnight continuity.  Useful at the start of the day when
        today's log is empty but yesterday's context is still relevant.
        """
        paths: list[Path] = []
        now = datetime.now()
        for delta in range(2):                          # today + yesterday
            date = (now - timedelta(days=delta)).strftime("%Y-%m-%d")
            p = DAILY_DIR / f"{date}-{self._namespace}.md"
            if p.exists():
                paths.append(p)

        if not paths:
            return ""

        # Concatenate newest last so tail-truncation keeps the most recent
        combined = "\n\n".join(
            p.read_text(encoding="utf-8") for p in reversed(paths)
        )
        if len(combined) > max_chars:
            cut = combined[-max_chars:]
            boundary = cut.find("\n## [")
            if boundary > 0:
                cut = cut[boundary:]
            return "*(earlier entries omitted)*\n\n" + cut
        return combined

    def list_log_files(self) -> list[Path]:
        """Return all daily log files for this namespace, sorted oldest first."""
        return sorted(DAILY_DIR.glob(f"*-{self._namespace}.md"))
