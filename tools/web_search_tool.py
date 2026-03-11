import os
import requests

# Default URL — overridden by models.yaml tools.web_search.url or SEARXNG_URL env var
_DEFAULT_URL = os.environ.get("SEARXNG_URL", "http://192.168.0.4:8090/")


class WebSearchTool:
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web using SearXNG. Returns titles, URLs, and content snippets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    def __init__(self, url: str = ""):
        self._base = (url or _DEFAULT_URL).rstrip("/")

    def search(self, query: str, num_results: int = 5) -> str:
        num_results = min(max(1, num_results), 10)
        try:
            resp = requests.get(
                f"{self._base}/search",
                params={"q": query, "format": "json", "language": "en"},
                timeout=15,
                headers={"User-Agent": "CorporateAI/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])[:num_results]
            if not results:
                return f"No results found for: {query}"
            lines = []
            for i, r in enumerate(results, 1):
                title   = r.get("title", "(no title)")
                url     = r.get("url", "")
                content = (r.get("content") or "")[:400].strip()
                lines.append(f"[{i}] {title}\n    {url}\n    {content}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Search error ({self._base}): {e}"
