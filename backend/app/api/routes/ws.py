from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import websocket_user
from app.core.websocket import Viewer, manager

router = APIRouter()


@router.websocket("/ws/complaints")
async def complaint_updates(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Live complaint feed. Staff get full records for complaints they manage; others get map data only."""
    user = await run_in_threadpool(websocket_user, token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(websocket, Viewer(user.user_id, user.role, user.department_id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
