from workers.worker_base import WorkerBase
from tools.filesystem_tool import FilesystemTool
from tools.git_tool import GitTool
from tools.shell_tool import ShellTool


class CoderWorker(WorkerBase):
    prompt_name = "WORKER_CODER"

    def __init__(self, model: str, event_bus=None, context_length: int = 8192, compaction_threshold: float = 0.80):
        super().__init__(model, event_bus, context_length, compaction_threshold)
        fs = FilesystemTool()
        gt = GitTool()
        sh = ShellTool()
        self.tools = [fs, gt, sh]
        self.tool_schemas = fs.schemas + gt.schemas + sh.schemas
