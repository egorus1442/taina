import asyncio
from collections.abc import Awaitable, Callable

from fastapi import WebSocket


class EventBroker:
    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._subscribers: list[Callable[[dict], Awaitable[None]]] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._sockets.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._sockets.discard(websocket)

    def subscribe(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def publish(self, payload: dict) -> None:
        for subscriber in self._subscribers:
            asyncio.create_task(subscriber(payload))

        async with self._lock:
            sockets = list(self._sockets)

        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                await self.disconnect(websocket)


event_broker = EventBroker()

