from pathlib import Path

import yaml
from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.websockets import WebSocket
from pydantic import BaseModel

from agents.supervisor_agent import _conv_store as _supervisor_conv_store

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def create_app(event_bus, agent_manager, task_queue, ws_broadcaster):
    app = FastAPI(title="Corporate AI")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class ChatRequest(BaseModel):
        message: str

    @app.post("/chat")
    async def chat(req: ChatRequest, bg: BackgroundTasks):
        async def _run():
            sup = agent_manager.supervisor
            if sup:
                await sup.handle(req.message)
        bg.add_task(_run)
        return {"ok": True}

    @app.get("/agents")
    async def get_agents():
        return agent_manager.get_agent_list()

    @app.get("/status")
    async def get_status():
        return {
            "connected": True,
            "agents": len(agent_manager.get_agent_list()),
            "tasks_queued": task_queue._queue.qsize(),
        }

    @app.get("/tasks")
    async def get_tasks():
        return task_queue.get_tasks()

    @app.get("/events")
    async def get_events():
        return event_bus.get_buffer()

    @app.post("/session/reset")
    async def session_reset(session_id: str = Query(default="default")):
        _supervisor_conv_store.clear_session(session_id)
        return {"ok": True, "session_id": session_id}

    @app.get("/config")
    async def get_config():
        agents_cfg = {}
        try:
            with open(_CONFIG_DIR / "agents.yaml") as f:
                agents_cfg = yaml.safe_load(f) or {}
        except Exception:
            pass
        return {"user": agents_cfg.get("user", {})}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_broadcaster.connect(websocket)

    return app
