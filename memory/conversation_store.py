"""
Conversation Store — persistent short-term memory for agent conversations.

Stores the full message history (role + content) for each session in a JSON
file so conversations survive server restarts.

The supervisor uses this to maintain multi-turn dialogue with the user.
Each chat session gets a session_id (defaults to "default").
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

STORE_FILE = Path(__file__).parent.parent / "memory" / "conversations.json"

logger = logging.getLogger(__name__)


class ConversationStore:
    def __init__(self):
        self._sessions: dict[str, list[dict]] = {}
        self._load()

    def _load(self):
        if STORE_FILE.exists():
            try:
                data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
                self._sessions = data if isinstance(data, dict) else {}
                total = sum(len(v) for v in self._sessions.values())
                logger.info(
                    f"[ConvStore] Loaded {len(self._sessions)} sessions, "
                    f"{total} total messages"
                )
            except Exception as e:
                logger.warning(f"[ConvStore] Failed to load conversations: {e}")
                self._sessions = {}

    def _save(self):
        try:
            STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STORE_FILE.write_text(
                json.dumps(self._sessions, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ConvStore] Failed to save conversations: {e}")

    def get_messages(self, session_id: str = "default") -> list[dict]:
        """Return stored messages as LLM-ready dicts (role + content only)."""
        raw = self._sessions.get(session_id, [])
        return [{"role": m["role"], "content": m["content"]} for m in raw]

    def append(self, session_id: str, role: str, content: str):
        """Append a single message and persist."""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append({
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self._save()

    def replace_messages(self, session_id: str, messages: list[dict]):
        """
        Replace history after compaction. Messages are plain {role, content} dicts;
        we re-attach timestamps (using now) before saving.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._sessions[session_id] = [
            {"role": m["role"], "content": m["content"], "ts": now}
            for m in messages
        ]
        self._save()
        logger.info(
            f"[ConvStore] Replaced session '{session_id}' with "
            f"{len(messages)} messages after compaction"
        )

    def clear_session(self, session_id: str = "default"):
        self._sessions.pop(session_id, None)
        self._save()
        logger.info(f"[ConvStore] Cleared session '{session_id}'")

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def session_length(self, session_id: str = "default") -> int:
        return len(self._sessions.get(session_id, []))
