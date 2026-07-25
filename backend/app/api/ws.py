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
        pass
    finally:
        # finally 兜底:非正常断开(ConnectionReset 等)也要摘除,否则死连接堆积
        # (空闲无下载时不广播 → 永不触发 send 失败的自愈)
        await ws_manager.disconnect(ws)
