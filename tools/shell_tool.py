import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent / "workspace"

_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 600


class ShellTool:
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a shell command in the workspace. Default timeout is 60s. Pass a higher timeout (up to 600s) for slow commands like package installs, scaffolding, or builds.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "Command to run"},
                        "cwd": {"type": "string", "description": "Working directory relative to workspace (optional, default '.')"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 600). Increase for npm install, pip install, build commands, scaffolding, etc."},
                    },
                    "required": ["cmd"],
                },
            },
        },
    ]

    def run_command(self, cmd: str, cwd: str = ".", timeout: int = _DEFAULT_TIMEOUT) -> str:
        work_dir = (WORKSPACE / cwd).resolve()
        if not str(work_dir).startswith(str(WORKSPACE.resolve())):
            return "Error: Path escapes workspace"
        timeout = max(1, min(int(timeout), _MAX_TIMEOUT))
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = result.stdout[:2000]
            err = result.stderr[:500]
            return out + (f"\nSTDERR: {err}" if err else "")
        except subprocess.TimeoutExpired:
            return f"Command timed out ({timeout}s)"
        except Exception as e:
            return f"Shell error: {e}"
