import os
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent / "workspace"


class FilesystemTool:
    schemas = [
        {"type": "function", "function": {"name": "read_file", "description": "Read a file from the workspace", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path within workspace"}}, "required": ["path"]}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write content to a file in the workspace", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
        {"type": "function", "function": {"name": "list_dir", "description": "List directory contents", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path within workspace, default '.'"}}, "required": []}}},
        {"type": "function", "function": {"name": "delete_file", "description": "Delete a file from the workspace", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    ]

    def _safe_path(self, rel: str) -> Path:
        # Strip any "workspace/" prefix — all paths are already relative to WORKSPACE
        rel = rel.replace("\\", "/")
        if rel.startswith("workspace/"):
            rel = rel[len("workspace/"):]
        p = (WORKSPACE / rel).resolve()
        if not str(p).startswith(str(WORKSPACE.resolve())):
            raise ValueError("Path escapes workspace")
        return p

    def read_file(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        return p.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> str:
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written: {path} ({len(content)} chars)"

    def list_dir(self, path: str = ".") -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"Directory not found: {path}"
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        return "\n".join(f"{'[D]' if e.is_dir() else '[F]'} {e.name}" for e in entries) or "(empty)"

    def delete_file(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"Not found: {path}"
        p.unlink()
        return f"Deleted: {path}"
