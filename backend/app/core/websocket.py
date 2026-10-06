"""WebSocket connections for the live complaint feed (/ws/complaints).

Every connection belongs to a signed-in user. Admins, and department staff for their own
department's complaints, receive the full complaint record; everyone else receives only
what the public map shows (position, category, status). Sends run concurrently with a
timeout, so one slow or stalled browser can't hold up delivery to the others.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import WebSocket

SEND_TIMEOUT_S = 2.0

# Fields anyone signed in may see about any complaint (what the GIS map needs).
PUBLIC_FIELDS = ("complaint_id", "incident_type", "status", "department_id", "latitude", "longitude")


@dataclass(frozen=True)
class Viewer:
    user_id: int
    role: str
    department_id: int | None

    def sees_full(self, complaint: dict) -> bool:
        if self.role == "admin":
            return True
        return self.role == "department" and complaint.get("department_id") == self.department_id


def public_view(complaint: dict) -> dict:
    data = {key: complaint.get(key) for key in PUBLIC_FIELDS}
    # Aliases used by the map page.
    data.update(id=data["complaint_id"], type=data["incident_type"], lat=data["latitude"], lng=data["longitude"])
    return data


async def safe_send(websocket: WebSocket, message: dict) -> bool:
    try:
        await asyncio.wait_for(websocket.send_json(message), SEND_TIMEOUT_S)
        return True
    except Exception:
        return False


async def close_quietly(websocket: WebSocket, code: int = 1008) -> None:
    try:
        await asyncio.wait_for(websocket.close(code=code), SEND_TIMEOUT_S)
    except Exception:
        pass


class ConnectionManager:
    """Tracks WebSocket clients subscribed to live complaint events."""

    def __init__(self) -> None:
        self.active_connections: dict[WebSocket, Viewer] = {}

    async def connect(self, websocket: WebSocket, viewer: Viewer) -> None:
        await websocket.accept()
        self.active_connections[websocket] = viewer

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def broadcast(self, message: dict) -> None:
        """message = {"type": ..., "data": <full complaint>}; each viewer gets what they may see."""
        complaint = message.get("data") or {}
        connections = list(self.active_connections.items())
        payloads = [
            message if viewer.sees_full(complaint) else {**message, "data": public_view(complaint)}
            for _, viewer in connections
        ]
        results = await asyncio.gather(*(safe_send(ws, p) for (ws, _), p in zip(connections, payloads)))
        for (websocket, _), ok in zip(connections, results):
            if not ok:
                self.disconnect(websocket)
                await close_quietly(websocket, code=1011)

    async def disconnect_user(self, user_id: int) -> None:
        for websocket, viewer in list(self.active_connections.items()):
            if viewer.user_id == user_id:
                self.disconnect(websocket)
                await close_quietly(websocket)


manager = ConnectionManager()
