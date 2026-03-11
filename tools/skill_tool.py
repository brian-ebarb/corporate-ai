import asyncio
import logging
from datetime import datetime
from pathlib import Path

WORKSPACE  = Path(__file__).parent.parent / "workspace"
SKILLS_DIR = WORKSPACE / "skills"
REQUESTS_FILE = WORKSPACE / "tool-requests.md"

logger = logging.getLogger(__name__)


class SkillTool:
    """
    Gives agents access to the shared skills library and the ability to
    request new tools. Skills are markdown files in workspace/skills/.
    Tool requests are appended to workspace/tool-requests.md and published
    as an event on the bus.
    """

    schemas = [
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "List all available skills in the skills library.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_skill",
                "description": "Read the full content of a skill by name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (without .md extension)"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_skill",
                "description": (
                    "Save a new skill (or update an existing one) to the skills library. "
                    "The skill should be a complete markdown file following the skill format."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name in kebab-case (e.g. 'competitor-analysis')"},
                        "content": {"type": "string", "description": "Full markdown content of the skill file"},
                    },
                    "required": ["name", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "request_tool",
                "description": (
                    "Submit a request for a new tool capability. Use this when you need to do "
                    "something that none of your current tools support."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string", "description": "Short name for the requested tool (e.g. 'pdf_reader')"},
                        "description": {"type": "string", "description": "What the tool should do"},
                        "use_case": {"type": "string", "description": "Why you need it — what task were you trying to accomplish?"},
                    },
                    "required": ["tool_name", "description", "use_case"],
                },
            },
        },
    ]

    def __init__(self, event_bus=None):
        self._bus = event_bus
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> str:
        skills = sorted(SKILLS_DIR.glob("*.md"))
        if not skills:
            return "No skills available yet. Create one with create_skill."
        lines = []
        for s in skills:
            # Extract description from frontmatter if present
            try:
                text = s.read_text(encoding="utf-8")
                desc = ""
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        for line in text[3:end].splitlines():
                            if line.startswith("description:"):
                                desc = line.split(":", 1)[1].strip().lstrip(">").strip()
                                break
                lines.append(f"- **{s.stem}**: {desc}" if desc else f"- **{s.stem}**")
            except Exception:
                lines.append(f"- **{s.stem}**")
        return "Available skills:\n" + "\n".join(lines)

    def read_skill(self, name: str) -> str:
        path = SKILLS_DIR / f"{name.strip()}.md"
        if not path.exists():
            return f"Skill not found: {name}. Use list_skills to see what's available."
        return path.read_text(encoding="utf-8")

    def create_skill(self, name: str, content: str) -> str:
        # Sanitise name
        safe = name.strip().lower().replace(" ", "-").replace("_", "-")
        path = SKILLS_DIR / f"{safe}.md"
        existed = path.exists()
        path.write_text(content, encoding="utf-8")
        action = "Updated" if existed else "Created"
        logger.info(f"[SkillTool] {action} skill: {safe}")
        if self._bus:
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._bus.publish(
                        "logs", "skill_created", "agent",
                        {"name": safe, "action": action.lower(), "path": str(path)},
                    )
                )
            )
        return f"{action} skill '{safe}' → workspace/skills/{safe}.md"

    def request_tool(self, tool_name: str, description: str, use_case: str) -> str:
        REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        entry = (
            f"\n## Tool Request: `{tool_name}`\n"
            f"*Requested: {ts}*\n\n"
            f"**What it should do:** {description}\n\n"
            f"**Why I need it:** {use_case}\n\n"
            f"---"
        )
        with REQUESTS_FILE.open("a", encoding="utf-8") as f:
            f.write(entry)
        logger.info(f"[SkillTool] Tool requested: {tool_name}")
        if self._bus:
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._bus.publish(
                        "logs", "tool_requested", "agent",
                        {"tool_name": tool_name, "description": description},
                    )
                )
            )
        return f"Tool request logged: '{tool_name}'. An operator will review workspace/tool-requests.md."
