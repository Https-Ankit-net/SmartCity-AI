from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import websocket_user
from app.core.notification_manager import notification_manager

router = APIRouter()


@router.websocket("/ws/notifications")
async def notification_updates(websocket: WebSocket, token: str = Query(default="")) -> None:
    """City-wide alerts ("new complaint reported") for signed-in users."""
    user = await run_in_threadpool(websocket_user, token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await notification_manager.connect(websocket, user.user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect(websocket)


@router.websocket("/ws/notifications/{user_id}")
async def user_notification_updates(websocket: WebSocket, user_id: int, token: str = Query(default="")) -> None:
    # Browsers can't set headers on a WebSocket, so the JWT comes as ?token=.
    user = await run_in_threadpool(websocket_user, token)
    if user is None or user.user_id != user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await notification_manager.connect_user(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect_user(user_id, websocket)
