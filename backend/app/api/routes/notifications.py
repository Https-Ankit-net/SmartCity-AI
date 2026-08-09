from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.notification_manager import notification_manager

router = APIRouter()


@router.websocket("/ws/notifications")
async def notification_updates(websocket: WebSocket) -> None:
    await notification_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect(websocket)
