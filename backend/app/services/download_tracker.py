"""下载跟踪:轮询 qB → 状态迁移 + 快照回写 + WebSocket 广播。

自适应频率:有活动任务 2s,空闲 10s。
状态迁移:DOWNLOADING → COMPLETED(qB 完成)/ DOWNLOAD_ERROR(qB error 或任务消失)。
启动对账:DB 里 DOWNLOADING 但 qB 没有 → DOWNLOAD_ERROR;qB 已完成 → 补走完成迁移。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.clients.downloader import downloader
from app.config import settings
from app.database import db_session
from app.models import Torrent, TorrentStatus

log = logging.getLogger(__name__)

ACTIVE_INTERVAL = 2
IDLE_INTERVAL = 10
# 下载器侧「活跃下载」状态:错误任务若在 qB 恢复成这些状态 → 自愈回 DOWNLOADING
# (pausedDL/stoppedDL 不在内 → 无进度暂停的任务保持暂停,不被自愈打回)
_RECOVER_DL_STATES = {"downloading", "stalledDL", "metaDL", "forcedDL", "forcedMetaDL",
                      "queuedDL", "checkingDL", "allocating"}


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


def _check_dead(db, t, lt) -> bool:
    """坏种检测:无做种 + 0 速 持续超过阈值 → 移除 + 标记 + 触发换备选源。返回是否已处理为坏种。"""
    if not settings.dead_torrent_enabled:
        t.stalled_since = None
        return False
    if lt.dlspeed == 0 and lt.seeds == 0:
        now = datetime.utcnow()
        if t.stalled_since is None:
            t.stalled_since = now
            return False
        if now - t.stalled_since < timedelta(hours=settings.dead_torrent_hours):
            return False
        # 判定坏种
        from app.services.rss_engine import DEAD_SKIP_REASON, reevaluate_skipped
        try:
            downloader.delete(t.info_hash, delete_files=True)
        except Exception:  # noqa: BLE001
            pass
        t.status = TorrentStatus.SKIPPED
        t.error_message = DEAD_SKIP_REASON
        t.stalled_since = None
        log.info("坏种自动清理 #%s %s", t.id, t.title_raw[:50])
        _emit("on_fail", t)
        db.flush()
        try:
            reevaluate_skipped(db, t.subscription)   # 自动换一个备选源
        except Exception as e:  # noqa: BLE001
            log.warning("坏种换源失败 #%s: %s", t.id, e)
        return True
    if t.stalled_since is not None:
        t.stalled_since = None
    return False


def _check_stall(db, t, lt) -> bool:
    """无进度暂停(温和档,与坏种删除并存):进度长期不增长超阈值 → 暂停(不删,可手动恢复)。

    只要进度还在涨就持续刷新计时;停涨超过 stall_pause_hours → 暂停 + 标 DOWNLOAD_ERROR
    (复用「错误→恢复」入口,恢复时重置计时,见 api/tasks.resume)。返回是否已暂停。
    """
    if not settings.stall_pause_enabled:
        return False
    now = datetime.utcnow()
    prog = float(lt.progress)
    if t.progress_at is None or prog > (t.last_progress or 0) + 1e-6:
        t.last_progress = prog          # 有进度:刷新快照与计时
        t.progress_at = now
        return False
    if now - t.progress_at < timedelta(hours=settings.stall_pause_hours):
        return False
    try:
        downloader.pause(t.info_hash)
    except Exception:  # noqa: BLE001
        pass
    t.status = TorrentStatus.DOWNLOAD_ERROR
    t.error_message = f"长期无进度(>{settings.stall_pause_hours}h),已自动暂停,可手动恢复"
    log.info("无进度自动暂停 #%s %s", t.id, t.title_raw[:50])
    _emit("on_fail", t)
    db.flush()
    return True


def _sync_once() -> tuple[list[dict], bool]:
    """同步一轮:回写快照 + 状态迁移。返回 (广播数据, 是否有活动任务)。"""
    to_enqueue: list[int] = []          # 后处理入队延到 commit 之后(见函数尾)
    with db_session() as db:
        rows = db.execute(select(Torrent).where(Torrent.status.in_(
            [TorrentStatus.DOWNLOADING, TorrentStatus.COMPLETED,
             TorrentStatus.DOWNLOAD_ERROR]))).scalars().all()
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
                    t.stalled_since = None
                    log.info("下载完成 #%s %s", t.id, t.title_raw[:50])
                    _emit("on_complete", t)
                    to_enqueue.append(t.id)
                elif _check_stall(db, t, lt):
                    pass            # 无进度已自动暂停
                elif _check_dead(db, t, lt):
                    pass            # 坏种已自动清理/换源,本轮不再计为活动
                else:
                    active = True
            elif t.status == TorrentStatus.DOWNLOAD_ERROR and lt is not None and not lt.error:
                # 下载器侧已恢复(手动 recheck/恢复种子)→ 回写状态,别一直卡「出错」
                if lt.done or lt.progress >= 1.0:
                    t.status = TorrentStatus.COMPLETED
                    t.completed_at = datetime.now(timezone.utc)
                    t.progress = 1.0
                    log.info("错误任务在下载器已完成,恢复入库 #%s %s", t.id, t.title_raw[:40])
                    _emit("on_complete", t)
                    to_enqueue.append(t.id)
                elif lt.state in _RECOVER_DL_STATES:
                    t.status = TorrentStatus.DOWNLOADING
                    t.error_message = None
                    t.progress_at = None
                    t.stalled_since = None   # 否则坏种计时残留 → 恢复后可能立刻被删
                    log.info("错误任务在下载器已恢复下载 #%s %s", t.id, t.title_raw[:40])
                    active = True
            if lt is not None:
                t.size = lt.size
                t.progress = float(lt.progress)
                t.dlspeed = int(lt.dlspeed)
            payload.append({
                "id": t.id, "status": t.status.value, "title": t.title_raw,
                "progress": round(t.progress, 4), "dlspeed": t.dlspeed,
                "size": t.size, "eta": lt.eta if lt else None,
                "state": lt.state if lt else None,
                "upspeed": lt.upspeed if lt else 0,
                "seeds": lt.seeds if lt else 0,
                "peers": lt.peers if lt else 0,
            })
        result = (payload, active)
    # with 退出 → commit 完成,worker 再读能看到 COMPLETED(否则读到旧 DOWNLOADING 被静默跳过)
    if to_enqueue:
        from app.services.postprocess import enqueue
        for tid in to_enqueue:
            enqueue(tid)
    return result


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
