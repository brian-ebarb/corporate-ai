import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent / "workspace"


class ShellTool:
    schemas = [
        {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the workspace", "parameters": {"type": "object", "properties": {"cmd": {"type": "string", "description": "Command to run"}, "cwd": {"type": "string", "description": "Working directory relative to workspace (optional)"}}, "required": ["cmd"]}}},
    ]

    def run_command(self, cmd: str, cwd: str = ".") -> str:
        work_dir = (WORKSPACE / cwd).resolve()
        if not str(work_dir).startswith(str(WORKSPACE.resolve())):
            return "Error: Path escapes workspace"
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            out = result.stdout[:2000]
            err = result.stderr[:500]
            return out + (f"\nSTDERR: {err}" if err else "")
        except subprocess.TimeoutExpired:
            return "Command timed out (60s)"
        except Exception as e:
            return f"Shell error: {e}"
