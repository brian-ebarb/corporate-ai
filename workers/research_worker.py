from workers.worker_base import WorkerBase
from tools.web_search_tool import WebSearchTool
from tools.filesystem_tool import FilesystemTool
from tools.skill_tool import SkillTool


class ResearchWorker(WorkerBase):
    prompt_name = "WORKER_RESEARCH"

    def __init__(self, model: str, event_bus=None, context_length: int = 8192,
                 compaction_threshold: float = 0.80, search_url: str = "", brave_api_key: str = ""):
        super().__init__(model, event_bus, context_length, compaction_threshold)
        ws = WebSearchTool(brave_api_key=brave_api_key, searxng_url=search_url)
        fs = FilesystemTool()
        sk = SkillTool(event_bus=event_bus)
        self.tools = [ws, fs, sk]
        self.tool_schemas = ws.schemas + fs.schemas + sk.schemas
