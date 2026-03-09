import asyncio
import logging
from pathlib import Path

# Path relative to this file — works on Windows and Unix
MEMORY_DIR = Path(__file__).parent.parent / ".memory"

logger = logging.getLogger(__name__)


class VectorMemory:
    def __init__(self, agent_id: str):
        self._agent_id = agent_id
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            try:
                import chromadb
                MEMORY_DIR.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(MEMORY_DIR))
                self._collection = client.get_or_create_collection(f"agent_{self._agent_id}")
                logger.debug(f"[Memory] ChromaDB collection ready: agent_{self._agent_id}")
            except ImportError:
                logger.warning("[Memory] chromadb not installed — vector memory disabled. Run: pip install chromadb")
            except Exception as e:
                logger.warning(f"[Memory] ChromaDB init failed for {self._agent_id}: {e}")
        return self._collection

    async def store(self, content: str, metadata: dict):
        def _store():
            col = self._get_collection()
            if col is None:
                return
            import uuid
            col.add(
                documents=[content],
                metadatas=[{k: str(v) for k, v in metadata.items()}],
                ids=[str(uuid.uuid4())],
            )
            logger.debug(f"[Memory] Stored {len(content)} chars for {self._agent_id}")
        try:
            await asyncio.to_thread(_store)
        except Exception as e:
            logger.warning(f"[Memory] Store failed for {self._agent_id}: {e}")

    async def recall(self, query: str, n_results: int = 5) -> list[str]:
        def _recall():
            col = self._get_collection()
            if col is None:
                return []
            count = col.count()
            if count == 0:
                return []
            results = col.query(query_texts=[query], n_results=min(n_results, count))
            docs = results.get("documents", [[]])[0]
            logger.debug(f"[Memory] Recalled {len(docs)} results for {self._agent_id}")
            return docs
        try:
            return await asyncio.to_thread(_recall)
        except Exception as e:
            logger.warning(f"[Memory] Recall failed for {self._agent_id}: {e}")
            return []
