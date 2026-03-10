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
                "path": {"type": "string", "description": "File or directory to stage, e.g. 'pomodoro-timer/app.js' or '.'"}
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
            "description": "Create or list branches. Pass name to create; omit to list.",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "Branch name to create (omit to list branches)"},
                "base": {"type": "string", "description": "Base branch to create from (default: current branch)"}
            }, "required": []}}},
        {"type": "function", "function": {
            "name": "git_checkout",
            "description": "Switch to a branch (creates it if create=true)",
            "parameters": {"type": "object", "properties": {
                "branch": {"type": "string"},
                "create": {"type": "boolean", "description": "Pass true to create the branch if it does not exist (-b flag)"}
            }, "required": ["branch"]}}},
        {"type": "function", "function": {
            "name": "git_merge",
            "description": "Merge a branch into the current branch",
            "parameters": {"type": "object", "properties": {
                "branch": {"type": "string", "description": "Branch to merge in"}
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

    def git_branch(self, name: str = "", base: str = "") -> str:
        if name:
            args = ["checkout", "-b", name]
            if base:
                args.append(base)
            return self._run(args)
        return self._run(["branch", "-a"])

    def git_checkout(self, branch: str, create: bool = False) -> str:
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(branch)
        return self._run(args)

    def git_merge(self, branch: str) -> str:
        return self._run(["merge", "--no-ff", branch, "-m", f"merge {branch}"])
