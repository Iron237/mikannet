"""WebSocket:/ws/progress 实时进度广播(只播不收,指令走 REST)。"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.download_tracker import ws_manager

router = APIRouter()


@router.websocket("/ws/progress")
async def progress(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # 心跳/忽略客户端消息
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
