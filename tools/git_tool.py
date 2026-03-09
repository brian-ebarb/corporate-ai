import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent / "workspace"


class GitTool:
    schemas = [
        {"type": "function", "function": {"name": "git_status", "description": "Get git status of workspace", "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "git_commit", "description": "Commit staged changes", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
        {"type": "function", "function": {"name": "git_diff", "description": "Show git diff", "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "git_log", "description": "Show recent git log", "parameters": {"type": "object", "properties": {"n": {"type": "integer", "default": 5}}, "required": []}}},
    ]

    def _run(self, args: list) -> str:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(WORKSPACE),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Git error: {e}"

    def git_status(self) -> str:
        return self._run(["status"])

    def git_commit(self, message: str) -> str:
        self._run(["add", "-A"])
        return self._run(["commit", "-m", message])

    def git_diff(self) -> str:
        return self._run(["diff"])

    def git_log(self, n: int = 5) -> str:
        return self._run(["log", f"-{n}", "--oneline"])
