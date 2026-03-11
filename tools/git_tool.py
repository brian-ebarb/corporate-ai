import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent / "workspace"


class GitTool:
    schemas = [
        {"type": "function", "function": {
            "name": "git_status",
            "description": "Get git status of workspace",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "git_add",
            "description": "Stage file(s) for commit. Use path='.' to stage everything.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "File or directory to stage, e.g. 'projects/my-app/app.js' or '.'"}
            }, "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "git_commit",
            "description": "Stage all changes and commit. Equivalent to git add -A && git commit.",
            "parameters": {"type": "object", "properties": {
                "message": {"type": "string"}
            }, "required": ["message"]}}},
        {"type": "function", "function": {
            "name": "git_diff",
            "description": "Show unstaged changes",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "git_log",
            "description": "Show recent git log",
            "parameters": {"type": "object", "properties": {
                "n": {"type": "integer", "default": 5}
            }, "required": []}}},
        {"type": "function", "function": {
            "name": "git_branch",
            "description": "List branches (no args) or create a new branch. Use for feature branches within a project — always commit current work before switching branches.",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "Branch name to create (omit to list branches)"}
            }, "required": []}}},
        {"type": "function", "function": {
            "name": "git_checkout",
            "description": "Switch to an existing branch. Always commit or stash current work first to avoid losing changes.",
            "parameters": {"type": "object", "properties": {
                "branch": {"type": "string"}
            }, "required": ["branch"]}}},
        {"type": "function", "function": {
            "name": "git_merge",
            "description": "Merge a branch into the current branch.",
            "parameters": {"type": "object", "properties": {
                "branch": {"type": "string", "description": "Branch to merge into current"}
            }, "required": ["branch"]}}},
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
            out = (result.stdout + result.stderr).strip()
            return out or "(no output)"
        except Exception as e:
            return f"Git error: {e}"

    def git_status(self) -> str:
        return self._run(["status"])

    def git_add(self, path: str = ".") -> str:
        return self._run(["add", path])

    def git_commit(self, message: str) -> str:
        self._run(["add", "-A"])
        return self._run(["commit", "-m", message])

    def git_diff(self) -> str:
        return self._run(["diff"])

    def git_log(self, n: int = 5) -> str:
        return self._run(["log", f"-{n}", "--oneline"])

    def git_branch(self, name: str = "") -> str:
        if name:
            return self._run(["branch", name])
        return self._run(["branch"])

    def git_checkout(self, branch: str) -> str:
        return self._run(["checkout", branch])

    def git_merge(self, branch: str) -> str:
        return self._run(["merge", branch])
