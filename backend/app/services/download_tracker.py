"""下载跟踪:轮询 qB → 状态迁移 + 快照回写 + WebSocket 广播。

自适应频率:有活动任务 2s,空闲 10s。
状态迁移:DOWNLOADING → COMPLETED(qB 完成)/ DOWNLOAD_ERROR(qB error 或任务消失)。
启动对账:DB 里 DOWNLOADING 但 qB 没有 → DOWNLOAD_ERROR;qB 已完成 → 补走完成迁移。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.clients.downloader import downloader
from app.database import db_session
from app.models import Torrent, TorrentStatus

log = logging.getLogger(__name__)

ACTIVE_INTERVAL = 2
IDLE_INTERVAL = 10


class WsManager:
    def __init__(self) -> None:
        self._connections: set = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        async with self._lock:
            conns = list(self._connections)
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — 死连接直接摘除
                await self.disconnect(ws)

    @property
    def has_clients(self) -> bool:
        return bool(self._connections)


ws_manager = WsManager()


def _emit(event: str, torrent) -> None:
    from app.services.events import emit
    emit(event, torrent)


def _sync_once() -> tuple[list[dict], bool]:
    """同步一轮:回写快照 + 状态迁移。返回 (广播数据, 是否有活动任务)。"""
    with db_session() as db:
        rows = db.execute(select(Torrent).where(Torrent.status.in_(
            [TorrentStatus.DOWNLOADING, TorrentStatus.COMPLETED]))).scalars().all()
        if not rows:
            return [], False

        live = {t.hash: t for t in downloader.list_tasks()}
        payload: list[dict] = []
        active = False
        for t in rows:
            lt = live.get(t.info_hash)
            if t.status == TorrentStatus.DOWNLOADING:
                if lt is None:
                    t.status = TorrentStatus.DOWNLOAD_ERROR
                    t.error_message = "任务在下载器中不存在(可能被手动删除)"
                    _emit("on_fail", t)
                elif lt.error:
                    t.status = TorrentStatus.DOWNLOAD_ERROR
                    t.error_message = f"下载器状态: {lt.state}"
                    _emit("on_fail", t)
                elif lt.done or lt.progress >= 1.0:
                    t.status = TorrentStatus.COMPLETED
                    t.completed_at = datetime.now(timezone.utc)
                    t.progress = 1.0
                    log.info("下载完成 #%s %s", t.id, t.title_raw[:50])
                    _emit("on_complete", t)
                    from app.services.postprocess import enqueue
                    enqueue(t.id)
                else:
                    active = True
            if lt is not None:
                t.size = lt.size
                t.progress = float(lt.progress)
                t.dlspeed = int(lt.dlspeed)
            payload.append({
                "id": t.id, "status": t.status.value, "title": t.title_raw,
                "progress": round(t.progress, 4), "dlspeed": t.dlspeed,
                "size": t.size, "eta": getattr(lt, "eta", None) if lt else None,
                "state": getattr(lt, "state", None) if lt else None,
            })
        return payload, active


async def tracker_loop(stop_event: asyncio.Event) -> None:
    log.info("download tracker 启动")
    while not stop_event.is_set():
        interval = IDLE_INTERVAL
        try:
            payload, active = await asyncio.to_thread(_sync_once)
            if payload and ws_manager.has_clients:
                await ws_manager.broadcast({"type": "tasks", "tasks": payload})
            if active:
                interval = ACTIVE_INTERVAL
        except Exception as e:  # noqa: BLE001 — qB 临时不可达不退出循环
            log.warning("tracker 轮询失败: %s", e)
            interval = IDLE_INTERVAL
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("download tracker 停止")
