import asyncio
from collections import defaultdict
import time


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._global_subscribers: list[asyncio.Queue] = []
        self._buffer: list[dict] = []
        self._buffer_max = 100

    async def publish(self, channel: str, event_type: str, agent: str, payload: dict, role: str = ""):
        event = {
            "type": event_type,
            "agent": agent,
            "role": role,
            "channel": channel,
            "payload": payload,
            "ts": int(time.time() * 1000),
        }
        self._buffer.append(event)
        if len(self._buffer) > self._buffer_max:
            self._buffer.pop(0)
        for q in list(self._subscribers.get(channel, [])):
            await q.put(event)
        for q in list(self._global_subscribers):
            await q.put(event)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[channel].append(q)
        return q

    def subscribe_all(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._global_subscribers.append(q)
        return q

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        try:
            self._subscribers[channel].remove(queue)
        except ValueError:
            pass

    def unsubscribe_all(self, queue: asyncio.Queue):
        try:
            self._global_subscribers.remove(queue)
        except ValueError:
            pass

    def get_buffer(self) -> list[dict]:
        return list(self._buffer)
