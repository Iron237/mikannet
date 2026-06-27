"""下载任务查询与控制(P1:查询+基本控制;P3 接 WebSocket 实时)。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.database import get_db
from app.models import Torrent, TorrentStatus
from app.schemas import TorrentOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TorrentOut])
def list_tasks(status: TorrentStatus | None = None, db: Session = Depends(get_db)):
    q = select(Torrent).order_by(Torrent.created_at.desc())
    if status:
        q = q.where(Torrent.status == status)   # 非法值由 FastAPI 校验为 422,不再 500
    rows = db.execute(q).scalars().all()

    # 合并 qB 实时快照
    live: dict[str, dict] = {}
    try:
        live = {t.hash: t for t in downloader.list_tasks()}
    except Exception:  # noqa: BLE001 — 下载器不可达时退化为 DB 快照
        log.warning("下载器不可达,返回 DB 快照")

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
            bangumi_title=b.title if b else None,
            season_number=(b.season_number or 1) if b else 1,
            error_message=t.error_message, published_at=t.published_at,
            created_at=t.created_at))
    return out


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
def batch(payload: dict, db: Session = Depends(get_db)):
    """批量操作下载任务。payload: {ids:[...], action:'pause'|'resume'|'delete', delete_files?:bool}。
    delete 对无 info_hash 的任务也能处理(仅标记 DB SKIPPED),不像单条接口会 409。"""
    ids = payload.get("ids") or []
    action = payload.get("action")
    delete_files = bool(payload.get("delete_files"))
    if action not in ("pause", "resume", "delete"):
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
            elif t.info_hash:               # pause/resume 仅对已提交任务有意义
                (downloader.pause if action == "pause" else downloader.resume)(t.info_hash)
                # 暂停/恢复都清计时:暂停期间不积累坏种/无进度判定,恢复后重新计
                t.stalled_since = None
                t.progress_at = None
                if action == "resume" and t.status == TorrentStatus.DOWNLOAD_ERROR:
                    t.status = TorrentStatus.DOWNLOADING   # 恢复无进度暂停/错误 → 重新跟踪
                    t.error_message = None
            elif action == "resume" and t.status == TorrentStatus.SUBMIT_FAILED and t.subscription:
                from app.services.rss_engine import _submit   # 重试提交失败的种子 = 重新提交
                _submit(db, t.subscription, t)
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
def resume(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Torrent, task_id)
    if not t:
        raise HTTPException(404)
    # 提交失败的种子从未进 qB(无 info_hash)→ 「重试」= 重新提交,而非 qB resume
    # (前端对 submit_failed / download_error 共用 resume 按钮;不修则 submit_failed 必 409)
    if not t.info_hash:
        if t.status == TorrentStatus.SUBMIT_FAILED and t.subscription:
            from app.services.rss_engine import _submit
            _submit(db, t.subscription, t)
            db.commit()
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
