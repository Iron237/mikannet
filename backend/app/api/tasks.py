"""下载任务查询与控制(P1:查询+基本控制;P3 接 WebSocket 实时)。"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from app.clients.downloader import downloader
from app.database import get_db
from app.models import Subscription, Torrent, TorrentStatus
from app.schemas import TorrentOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _resubmit_in_background(task_id: int) -> None:
    """后台重新提交(提交失败的种子点重试):_submit 含 Mikan 下载 + qB 提交的网络 I/O,
    放后台跑,避免阻塞 HTTP 响应(网络故障时单次可达上百秒)。自带独立会话。"""
    from app.database import db_session
    from app.services.rss_engine import _submit
    try:
        with db_session() as db:
            t = db.get(Torrent, task_id)
            if t and t.subscription:
                _submit(db, t.subscription, t)
    except Exception:  # noqa: BLE001
        log.warning("后台重新提交失败 task=%s", task_id, exc_info=True)


class TaskSyncRequest(BaseModel):
    after_id: int | None = Field(default=None, ge=0)
    # 上次仍在队列中的 ID。任务入库、跳过或被删除后，要补发其最终状态。
    watching: list[int] = Field(default_factory=list)


def _task_query():
    return (select(Torrent)
            .options(joinedload(Torrent.subscription).joinedload(Subscription.bangumi))
            .order_by(Torrent.created_at.desc(), Torrent.id.desc()))


def _live_tasks():
    try:
        return {t.hash: t for t in downloader.list_tasks()}
    except Exception:  # noqa: BLE001 — 下载器不可达时退化为 DB 快照
        log.warning("下载器不可达,返回 DB 快照")
        return None


def _serialize_tasks(rows, live):
    out = []
    for t in rows:
        lt = live.get(t.info_hash) if t.info_hash else None
        b = t.subscription.bangumi if t.subscription else None
        out.append(TorrentOut(
            id=t.id, subscription_id=t.subscription_id, title_raw=t.title_raw,
            status=t.status.value, is_batch=t.is_batch, version=t.version,
            episodes=(t.parsed_json or {}).get("episodes") or [],
            size=lt.size if lt else t.size,
            progress=lt.progress if lt else t.progress,
            dlspeed=lt.dlspeed if lt else t.dlspeed,
            upspeed=lt.upspeed if lt else 0,
            seeds=lt.seeds if lt else 0,
            peers=lt.peers if lt else 0,
            eta=lt.eta if lt else None,
            state=lt.state if lt else None,
            paused=bool(lt.paused) if lt else False,
            bangumi_title=b.title if b else None,
            season_number=(b.season_number or 1) if b else 1,
            error_message=t.error_message, published_at=t.published_at,
            created_at=t.created_at))
    return out


@router.get("", response_model=list[TorrentOut])
def list_tasks(status: TorrentStatus | None = None, db: Session = Depends(get_db)):
    q = _task_query()
    if status:
        q = q.where(Torrent.status == status)
    return _serialize_tasks(db.scalars(q).all(), _live_tasks() or {})


@router.post("/sync")
def sync_tasks(request: TaskSyncRequest, db: Session = Depends(get_db)):
    """首次读取可见历史；增量只核对队列、刚离开队列的任务和新建记录。"""
    # 先取 ID 水位；过程中并发插入的行在下一轮仍可按 ID 补齐。
    newest = db.scalar(select(func.max(Torrent.id))) or 0
    full = request.after_id is None or request.after_id > newest
    q = _task_query().where(Torrent.status != TorrentStatus.SKIPPED)
    if not full:
        queue = (TorrentStatus.PENDING, TorrentStatus.DOWNLOADING, TorrentStatus.COMPLETED,
                 TorrentStatus.SUBMIT_FAILED, TorrentStatus.DOWNLOAD_ERROR)
        # 一个 JSON 参数可容纳整批 ID，不受 SQLite 绑定参数个数限制。
        watched = text("SELECT value FROM json_each(:watch_ids)").bindparams(
            watch_ids=json.dumps(request.watching))
        q = q.where(or_(Torrent.status.in_(queue), Torrent.id > request.after_id,
                        Torrent.id.in_(watched)))
    rows = db.scalars(q).all()
    check_live = full and any(t.info_hash and t.status != TorrentStatus.ARCHIVED for t in rows)
    live = _live_tasks() if check_live else None
    present = {t.id for t in rows}
    return {"cursor": newest, "full": full,
            "live_available": (live is not None) if check_live else None,
            "tasks": _serialize_tasks(rows, live or {}),
            "removed": [] if full else sorted(set(request.watching) - present)}


def _get_with_hash(db: Session, task_id: int) -> Torrent:
    t = db.get(Torrent, task_id)
    if not t:
        raise HTTPException(404)
    if not t.info_hash:
        raise HTTPException(409, "任务未提交到 qB(无 info_hash)")
    return t


@router.post("/{task_id}/postprocess", status_code=202)
def retry_postprocess(task_id: int, db: Session = Depends(get_db)):
    """探测失败后手动重试后处理。"""
    from app.services.postprocess import enqueue
    t = db.get(Torrent, task_id)
    if not t:
        raise HTTPException(404)
    enqueue(t.id)
    return {"queued": t.id}


@router.post("/batch")
def batch(payload: dict, background: BackgroundTasks, db: Session = Depends(get_db)):
    """批量操作下载任务。payload: {ids:[...], action:'pause'|'resume'|'retry'|'delete', delete_files?:bool}。
    delete 对无 info_hash 的任务也能处理(仅标记 DB SKIPPED),不像单条接口会 409。"""
    ids = payload.get("ids") or []
    action = payload.get("action")
    delete_files = bool(payload.get("delete_files"))
    if action not in ("pause", "resume", "retry", "delete"):
        raise HTTPException(400, "未知操作")
    done: list[int] = []
    failed: list[int] = []
    for tid in ids:
        t = db.get(Torrent, tid)
        if not t:
            failed.append(tid)
            continue
        try:
            if action == "delete":
                if t.info_hash:
                    downloader.delete(t.info_hash, delete_files=delete_files)
                t.status = TorrentStatus.SKIPPED
                t.error_message = "手动删除"
            elif action == "pause":
                if not t.info_hash or t.status != TorrentStatus.DOWNLOADING:
                    failed.append(tid)
                    continue
                downloader.pause(t.info_hash)
                # 暂停/恢复都清计时:暂停期间不积累坏种/无进度判定,恢复后重新计
                t.stalled_since = None
                t.progress_at = None
            elif action in ("resume", "retry") and t.info_hash and t.status in (
                    TorrentStatus.DOWNLOADING, TorrentStatus.DOWNLOAD_ERROR):
                downloader.resume(t.info_hash)
                t.stalled_since = None
                t.progress_at = None
                if t.status == TorrentStatus.DOWNLOAD_ERROR:
                    t.status = TorrentStatus.DOWNLOADING   # 恢复无进度暂停/错误 → 重新跟踪
                    t.error_message = None
            elif action in ("resume", "retry") and t.status == TorrentStatus.SUBMIT_FAILED \
                    and t.subscription:
                background.add_task(_resubmit_in_background, t.id)   # 重试提交失败=异步重新提交
            elif action == "retry" and t.status == TorrentStatus.COMPLETED:
                from app.services.postprocess import enqueue
                background.add_task(enqueue, t.id)
            else:
                failed.append(tid)
                continue
            done.append(tid)
        except Exception:  # noqa: BLE001 — 单条失败不阻断其余
            log.warning("批量 %s 失败 task=%s", action, tid, exc_info=True)
            failed.append(tid)
    db.commit()
    return {"done": done, "failed": failed}


@router.post("/{task_id}/pause", status_code=204)
def pause(task_id: int, db: Session = Depends(get_db)):
    t = _get_with_hash(db, task_id)
    downloader.pause(t.info_hash)
    t.stalled_since = None   # 暂停期间不积累坏种/无进度判定,否则会被自动清理删文件
    t.progress_at = None
    db.commit()


@router.post("/{task_id}/resume", status_code=204)
def resume(task_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    t = db.get(Torrent, task_id)
    if not t:
        raise HTTPException(404)
    # 提交失败的种子从未进 qB(无 info_hash)→ 「重试」= 重新提交,而非 qB resume
    # (前端对 submit_failed / download_error 共用 resume 按钮;不修则 submit_failed 必 409)
    if not t.info_hash:
        if t.status == TorrentStatus.SUBMIT_FAILED and t.subscription:
            background.add_task(_resubmit_in_background, task_id)   # 异步,避免阻塞响应
            return
        raise HTTPException(409, "任务未提交到 qB(无 info_hash)")
    downloader.resume(t.info_hash)
    t.stalled_since = None   # 恢复后重新计时,避免残留计时导致刚恢复又被判坏种
    t.progress_at = None
    if t.status == TorrentStatus.DOWNLOAD_ERROR:
        t.status = TorrentStatus.DOWNLOADING   # 恢复无进度暂停/错误 → 重新跟踪
        t.error_message = None
    db.commit()


@router.delete("/{task_id}", status_code=204)
def delete(task_id: int, delete_files: bool = False, db: Session = Depends(get_db)):
    t = _get_with_hash(db, task_id)
    downloader.delete(t.info_hash, delete_files=delete_files)
    t.status = TorrentStatus.SKIPPED
    t.error_message = "手动删除"
    db.commit()
