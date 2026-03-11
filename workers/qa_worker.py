from workers.worker_base import WorkerBase
from tools.filesystem_tool import FilesystemTool
from tools.shell_tool import ShellTool
from tools.skill_tool import SkillTool


class QAWorker(WorkerBase):
    prompt_name = "WORKER_QA"

    def __init__(self, model: str, event_bus=None, context_length: int = 8192, compaction_threshold: float = 0.80):
        super().__init__(model, event_bus, context_length, compaction_threshold)
        fs = FilesystemTool()
        sh = ShellTool()
        sk = SkillTool(event_bus=event_bus)
        self.tools = [fs, sh, sk]
        self.tool_schemas = fs.schemas + sh.schemas + sk.schemas
