import asyncio
from fastapi import WebSocket, WebSocketDisconnect
import json


class WSBroadcaster:
    def __init__(self, event_bus):
        self._bus = event_bus

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        queue = self._bus.subscribe_all()
        # Send status + buffered events
        try:
            await websocket.send_text(json.dumps({
                "type": "CA_STATUS",
                "data": {"connected": True}
            }))
            buffered = self._bus.get_buffer()[-100:]
            if buffered:
                await websocket.send_text(json.dumps({
                    "type": "CA_EVENTS_BATCH",
                    "data": buffered
                }))
            await self.broadcast_loop(websocket, queue)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._bus.unsubscribe_all(queue)

    async def broadcast_loop(self, websocket: WebSocket, queue: asyncio.Queue):
        while True:
            event = await queue.get()
            try:
                await websocket.send_text(json.dumps({"type": "CA_EVENT", "data": event}))
            except Exception:
                break
