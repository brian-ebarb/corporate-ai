import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.event_bus import EventBus


@dataclass
class Task:
    title: str
    description: str
    worker_type: str
    assigned_to: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: str = "queued"
    result: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    worker: object = field(default=None, repr=False)
    channel: str = "tasks"          # department channel for dashboard visibility


class TaskQueue:
    def __init__(self, event_bus: "EventBus"):
        self._bus = event_bus
        self._queue: asyncio.Queue = asyncio.Queue()
        self._history: list[Task] = []

    async def enqueue(self, task: Task) -> str:
        await self._queue.put(task)
        self._history.append(task)
        await self._bus.publish(
            "tasks", "task_created", task.assigned_to,
            {"task_id": task.id, "title": task.title, "worker_type": task.worker_type}
        )
        return task.id

    async def start_worker_loop(self):
        while True:
            task = await self._queue.get()
            await self._execute(task)
            self._queue.task_done()

    async def _execute(self, task: Task):
        task.status = "running"
        worker_label = task.worker_type or task.assigned_to

        # Publish to tasks channel (task management)
        await self._bus.publish(
            "tasks", "task_started", task.assigned_to,
            {"task_id": task.id, "title": task.title, "worker": worker_label}
        )
        # Also publish to department channel so the dashboard shows worker activity
        if task.channel and task.channel != "tasks":
            await self._bus.publish(
                task.channel, "agent_started", worker_label,
                {"task_id": task.id, "task": task.title},
                role="worker",
            )

        try:
            if task.worker:
                task.result = await task.worker.run(task)
            else:
                task.result = "No worker assigned"
            task.status = "completed"
        except Exception as e:
            task.result = f"Error: {e}"
            task.status = "failed"

        result_preview = (task.result or "")[:500]
        await self._bus.publish(
            "tasks", "task_completed", task.assigned_to,
            {"task_id": task.id, "title": task.title, "status": task.status, "result": result_preview}
        )
        if task.channel and task.channel != "tasks":
            await self._bus.publish(
                task.channel, "agent_finished", worker_label,
                {"task_id": task.id, "task": task.title, "status": task.status, "result": result_preview},
                role="worker",
            )

    def get_tasks(self) -> list[dict]:
        return [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "worker_type": t.worker_type,
                "assigned_to": t.assigned_to,
                "status": t.status,
                "created_at": t.created_at,
            }
            for t in self._history[-50:]
        ]
