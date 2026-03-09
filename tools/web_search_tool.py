import requests


class WebSearchTool:
    schemas = [
        {"type": "function", "function": {"name": "search", "description": "Search the web for information", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}}},
    ]

    def search(self, query: str) -> str:
        try:
            resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                timeout=10,
                headers={"User-Agent": "CorporateAI/1.0"},
            )
            data = resp.json()
            abstract = data.get("AbstractText", "")
            related = data.get("RelatedTopics", [])[:3]
            results = []
            if abstract:
                results.append(f"Summary: {abstract}")
            for r in related:
                if isinstance(r, dict) and r.get("Text"):
                    results.append(f"- {r['Text'][:200]}")
            return "\n".join(results) if results else f"No results found for: {query}"
        except Exception as e:
            return f"Search error: {e}"
