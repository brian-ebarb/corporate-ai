#!/usr/bin/env python3
"""Corporate AI — Entry Point"""
import asyncio
import os
import subprocess
from pathlib import Path

import uvicorn
import yaml

from orchestrator.event_bus import EventBus
from orchestrator.task_queue import TaskQueue
from orchestrator.agent_manager import AgentManager
from api.websocket_server import WSBroadcaster
from api.rest_api import create_app

WORKSPACE = Path(__file__).parent / "workspace"
CONFIG_DIR = Path(__file__).parent / "config"


def ensure_workspace_git():
    git_dir = WORKSPACE / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init", str(WORKSPACE)], capture_output=True)
        print(f"[init] git initialized in {WORKSPACE}")


async def heartbeat_loop(event_bus: EventBus, agent_manager: AgentManager, cfg: dict):
    """
    Fires on a configurable interval.

    modes:
      "event"  — publishes a silent heartbeat pulse on the logs channel (default)
      "prompt" — triggers the supervisor with the configured prompt so it can
                 self-monitor, run scheduled checks, or log status
    """
    interval = cfg.get("interval_seconds", 60)
    mode     = cfg.get("mode", "event")
    prompt   = cfg.get("prompt", "Heartbeat check-in. Log your current status.")
    tick     = 0

    print(f"[heartbeat] started — interval={interval}s  mode={mode}")

    while True:
        await asyncio.sleep(interval)
        tick += 1

        if mode == "prompt":
            sup = agent_manager.supervisor
            if sup:
                print(f"[heartbeat] tick={tick} — triggering supervisor prompt")
                # Fire and forget — don't block the heartbeat on the response
                asyncio.create_task(sup.handle(f"[HEARTBEAT tick={tick}] {prompt}"))
        else:
            await event_bus.publish(
                "logs", "heartbeat", "system",
                {
                    "tick": tick,
                    "interval_seconds": interval,
                    "agents": len(agent_manager.get_agent_list()),
                }
            )


async def main():
    # Init workspace
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    ensure_workspace_git()

    # Core components
    event_bus     = EventBus()
    task_queue    = TaskQueue(event_bus)
    agent_manager = AgentManager(config_path=str(CONFIG_DIR))
    agent_manager.load(event_bus, task_queue)
    ws_broadcaster = WSBroadcaster(event_bus)

    # Start task worker loop
    asyncio.create_task(task_queue.start_worker_loop())

    # Start heartbeat if enabled
    agents_cfg = {}
    agents_yaml = CONFIG_DIR / "agents.yaml"
    if agents_yaml.exists():
        with open(agents_yaml) as f:
            agents_cfg = yaml.safe_load(f) or {}

    hb_cfg = agents_cfg.get("heartbeat", {})
    if hb_cfg.get("enabled", False):
        asyncio.create_task(heartbeat_loop(event_bus, agent_manager, hb_cfg))

    # Create FastAPI app
    app = create_app(event_bus, agent_manager, task_queue, ws_broadcaster)

    # Log startup event
    agent_list = agent_manager.get_agent_list()
    await event_bus.publish("logs", "log_update", "system",
                            {"message": "Corporate AI started",
                             "agents": len(agent_list),
                             "heartbeat": hb_cfg.get("enabled", False),
                             "heartbeat_interval": hb_cfg.get("interval_seconds", 60)})

    print(f"[start] Corporate AI — {len(agent_list)} agents loaded")
    if hb_cfg.get("enabled"):
        print(f"[start] Heartbeat — {hb_cfg.get('interval_seconds', 60)}s  mode={hb_cfg.get('mode', 'event')}")
    port = int(os.environ.get("PORT", "8000"))
    print(f"[start] FastAPI on http://0.0.0.0:{port}")

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
