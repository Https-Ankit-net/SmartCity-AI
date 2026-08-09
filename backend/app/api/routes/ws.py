from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.websocket import manager

router = APIRouter()


@router.websocket("/ws/complaints")
async def complaint_updates(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
