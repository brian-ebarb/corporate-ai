"""
Prompt loader — reads markdown context files from config/prompts/.
Agents call load_prompt() to build rich system prompts without hardcoding them.
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "config" / "prompts"


def load_prompt(*names: str) -> str:
    """
    Load and concatenate one or more prompt files by name.

    Usage:
        load_prompt("COMPANY", "SUPERVISOR")
        load_prompt("COMPANY", "EXECUTIVE")
        load_prompt("WORKER_CODER")

    Names are matched case-insensitively and .md extension is added automatically.
    Missing files are silently skipped (so you can add new prompt files without
    breaking agents that haven't been updated yet).

    Returns a single string with sections separated by a horizontal rule.
    """
    parts = []
    for name in names:
        fname = name.upper()
        if not fname.endswith(".MD"):
            fname += ".MD"
        path = PROMPTS_DIR / fname
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
        else:
            # Silently skip missing files — makes it easy to add optional overrides
            pass
    return "\n\n---\n\n".join(parts)


def load_prompt_or(name: str, fallback: str = "") -> str:
    """Load a single prompt file, returning fallback if it doesn't exist."""
    result = load_prompt(name)
    return result if result else fallback
