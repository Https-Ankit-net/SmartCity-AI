import asyncio
from collections import defaultdict

from fastapi import WebSocket

from app.core.websocket import close_quietly, safe_send


class NotificationManager:
    """Signed-in clients subscribed to notification alerts: a broadcast feed and per-user feeds."""

    def __init__(self) -> None:
        # Broadcast feed: socket -> user_id.
        self.active_connections: dict[WebSocket, int] = {}
        # Per-user sockets (one user may have several tabs open).
        self.user_connections: dict[int, list[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        await websocket.accept()
        self.active_connections[websocket] = user_id

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def connect_user(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.user_connections[user_id].append(websocket)

    def disconnect_user(self, user_id: int, websocket: WebSocket) -> None:
        connections = self.user_connections.get(user_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.user_connections.pop(user_id, None)

    async def broadcast(self, message: dict) -> None:
        connections = list(self.active_connections)
        results = await asyncio.gather(*(safe_send(ws, message) for ws in connections))
        for websocket, ok in zip(connections, results):
            if not ok:
                self.disconnect(websocket)
                await close_quietly(websocket, code=1011)

    async def send_to_user(self, user_id: int, message: dict) -> None:
        connections = list(self.user_connections.get(user_id, []))
        results = await asyncio.gather(*(safe_send(ws, message) for ws in connections))
        for websocket, ok in zip(connections, results):
            if not ok:
                self.disconnect_user(user_id, websocket)
                await close_quietly(websocket, code=1011)

    async def close_user(self, user_id: int) -> None:
        """Close every notification socket a user holds (e.g. after their access is revoked)."""
        for websocket in list(self.user_connections.get(user_id, [])):
            self.disconnect_user(user_id, websocket)
            await close_quietly(websocket)
        for websocket, owner in list(self.active_connections.items()):
            if owner == user_id:
                self.disconnect(websocket)
                await close_quietly(websocket)


notification_manager = NotificationManager()
