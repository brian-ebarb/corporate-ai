import os
import requests
import logging

logger = logging.getLogger(__name__)

_BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
_DEFAULT_SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.0.4:8090/")


class WebSearchTool:
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web. Returns titles, URLs, and content snippets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num_results": {"type": "integer", "description": "Number of results (default 5, max 10)"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    def __init__(self, brave_api_key: str = "", searxng_url: str = "", url: str = ""):
        self._brave_key = brave_api_key or os.environ.get("BRAVE_API_KEY", "")
        # Accept legacy `url` kwarg as searxng_url
        self._searxng = (searxng_url or url or _DEFAULT_SEARXNG).rstrip("/")

    def search(self, query: str, num_results: int = 5) -> str:
        num_results = min(max(1, num_results), 10)

        if self._brave_key:
            result = self._brave_search(query, num_results)
            if result:
                return result
            logger.warning("[WebSearch] Brave returned no results — falling back to SearXNG")

        return self._searxng_search(query, num_results)

    # ── Brave Search API ───────────────────────────────────────────────────

    def _brave_search(self, query: str, num_results: int) -> str:
        try:
            resp = requests.get(
                _BRAVE_API_URL,
                params={"q": query, "count": num_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self._brave_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = (data.get("web", {}) or {}).get("results", [])[:num_results]
            if not items:
                return ""
            lines = []
            for i, r in enumerate(items, 1):
                title   = r.get("title", "(no title)")
                url     = r.get("url", "")
                snippet = (r.get("description") or "")[:400].strip()
                lines.append(f"[{i}] {title}\n    {url}\n    {snippet}")
            logger.info(f"[WebSearch] Brave: {len(items)} results for '{query}'")
            return "\n\n".join(lines)
        except Exception as e:
            logger.warning(f"[WebSearch] Brave error: {e}")
            return ""

    # ── SearXNG fallback ───────────────────────────────────────────────────

    def _searxng_search(self, query: str, num_results: int) -> str:
        try:
            resp = requests.get(
                f"{self._searxng}/search",
                params={"q": query, "format": "json", "language": "en"},
                timeout=15,
                headers={"User-Agent": "CorporateAI/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

            unresponsive = data.get("unresponsive_engines", [])
            if unresponsive:
                names = [e[0] for e in unresponsive]
                logger.warning(f"[WebSearch] SearXNG unresponsive engines: {names}")

            results = data.get("results", [])[:num_results]
            if not results:
                return f"No results found for: {query}"
            lines = []
            for i, r in enumerate(results, 1):
                title   = r.get("title", "(no title)")
                url     = r.get("url", "")
                content = (r.get("content") or "")[:400].strip()
                lines.append(f"[{i}] {title}\n    {url}\n    {content}")
            logger.info(f"[WebSearch] SearXNG: {len(results)} results for '{query}'")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Search error: {e}"
