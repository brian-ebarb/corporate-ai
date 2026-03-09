from workers.worker_base import WorkerBase
from tools.filesystem_tool import FilesystemTool
from tools.shell_tool import ShellTool


class QAWorker(WorkerBase):
    prompt_name = "WORKER_QA"

    def __init__(self, model: str, event_bus=None, context_length: int = 8192, compaction_threshold: float = 0.80):
        super().__init__(model, event_bus, context_length, compaction_threshold)
        fs = FilesystemTool()
        sh = ShellTool()
        self.tools = [fs, sh]
        self.tool_schemas = fs.schemas + sh.schemas
