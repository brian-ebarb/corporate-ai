import yaml
import os
from pathlib import Path


class AgentManager:
    def __init__(self, config_path: str = "config"):
        self._config_path = config_path
        self._supervisor = None
        self._executives: list = []
        self._workers: dict = {}
        self._agents_cfg: dict = {}
        self._models_cfg: dict = {}

    def load(self, event_bus, task_queue):
        agents_file = Path(self._config_path) / "agents.yaml"
        models_file = Path(self._config_path) / "models.yaml"

        with open(agents_file) as f:
            self._agents_cfg = yaml.safe_load(f)
        with open(models_file) as f:
            self._models_cfg = yaml.safe_load(f)

        # Set LiteLLM API keys from config
        api_keys = self._models_cfg.get("api_keys", {})
        if api_keys.get("anthropic"):
            os.environ.setdefault("ANTHROPIC_API_KEY", api_keys["anthropic"])
        if api_keys.get("openai"):
            os.environ.setdefault("OPENAI_API_KEY", api_keys["openai"])
        if api_keys.get("openrouter"):
            os.environ.setdefault("OPENROUTER_API_KEY", api_keys["openrouter"])

        # Import here to avoid circular deps
        from agents.supervisor_agent import SupervisorAgent
        from agents.executive_agent import ExecutiveAgent
        from workers.coder_worker import CoderWorker
        from workers.qa_worker import QAWorker
        from workers.research_worker import ResearchWorker
        from workers.docs_worker import DocsWorker

        worker_map = {
            "coder_worker": CoderWorker,
            "qa_worker": QAWorker,
            "research_worker": ResearchWorker,
            "docs_worker": DocsWorker,
        }

        # Instantiate workers
        for wname in self._agents_cfg.get("workers", []):
            wtype = wname.replace("_worker", "")
            wcfg = self._models_cfg.get("workers", {}).get(wtype, {})
            model = self._resolve_model(wcfg)
            context_length = int(wcfg.get("context_length", 8192))
            compaction_threshold = float(wcfg.get("compaction_threshold", 0.80))
            cls = worker_map.get(wname)
            if cls:
                self._workers[wtype] = cls(
                    model=model,
                    event_bus=event_bus,
                    context_length=context_length,
                    compaction_threshold=compaction_threshold,
                )

        # Instantiate executives
        exec_models = self._models_cfg.get("executives", {})
        for ecfg in self._agents_cfg.get("executives", []):
            dept = ecfg["department"]
            mcfg = exec_models.get(dept, {})
            model = self._resolve_model(mcfg)
            context_length = int(mcfg.get("context_length", 16384))
            compaction_threshold = float(mcfg.get("compaction_threshold", 0.75))
            exec_agent = ExecutiveAgent(
                name=ecfg["name"],
                role=ecfg["role"],
                department=dept,
                model=model,
                event_bus=event_bus,
                task_queue=task_queue,
                workers=self._workers,
                context_length=context_length,
                compaction_threshold=compaction_threshold,
            )
            self._executives.append(exec_agent)

        # Instantiate supervisor
        scfg = self._models_cfg.get("supervisor", {})
        smodel = self._resolve_model(scfg)
        context_length = int(scfg.get("context_length", 32768))
        compaction_threshold = float(scfg.get("compaction_threshold", 0.75))
        sagent_cfg = self._agents_cfg.get("supervisor", {})
        self._supervisor = SupervisorAgent(
            name=sagent_cfg.get("name", "Bob Sello"),
            role=sagent_cfg.get("role", "COO"),
            model=smodel,
            event_bus=event_bus,
            executives=self._executives,
            context_length=context_length,
            compaction_threshold=compaction_threshold,
        )

    def _resolve_model(self, cfg: dict) -> str:
        provider = cfg.get("provider", "ollama")
        model = cfg.get("model", "llama3.1:latest")
        if provider == "ollama":
            return f"ollama/{model}"
        elif provider == "anthropic":
            return f"anthropic/{model}"
        elif provider == "openai":
            return model
        elif provider == "openrouter":
            return f"openrouter/{model}"
        return model

    @property
    def supervisor(self):
        return self._supervisor

    @property
    def executives(self):
        return self._executives

    @property
    def workers(self):
        return self._workers

    @staticmethod
    def _make_id(name: str) -> str:
        return name.lower().replace(" ", "_").replace("-", "_")

    def get_agent_list(self) -> list[dict]:
        result = []
        if self._supervisor:
            result.append({
                "id": self._make_id(self._supervisor.name),
                "name": self._supervisor.name,
                "role": self._supervisor.role,
                "type": "supervisor",
                "department": None,
                "model": self._supervisor.model,
                "status": "active",
            })
        for e in self._executives:
            result.append({
                "id": self._make_id(e.name),
                "name": e.name,
                "role": e.role,
                "type": "executive",
                "department": e.department,
                "model": e.model,
                "status": "active",
            })
        for wtype, w in self._workers.items():
            result.append({
                "id": wtype,
                "name": wtype.replace("_", " ").title(),
                "role": "Worker",
                "type": "worker",
                "department": None,
                "model": w.model,
                "status": "active",
            })
        return result
