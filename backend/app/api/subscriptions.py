"""订阅 CRUD(P1:直接传 Mikan ID;P2 升级为搜索向导)。"""
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import db_session, get_db
from app.models import Bangumi, Subscription, Torrent, TorrentEpisode, VideoFile
from app.schemas import SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

_ILLEGAL_PATH = re.compile(r'[<>:"/\\|?*]')


def _safe_dirname(name: str) -> str:
    return _ILLEGAL_PATH.sub(" ", name).strip() or "未命名番剧"


def _poll_in_background(subscription_id: int) -> None:
    from app.services.rss_engine import safe_poll
    with db_session() as db:
        sub = db.get(Subscription, subscription_id)
        if sub:
            safe_poll(db, sub)


@router.post("", response_model=SubscriptionOut, status_code=201)
def create_subscription(payload: SubscriptionCreate, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    bangumi = db.execute(select(Bangumi).where(
        Bangumi.mikan_bangumi_id == payload.mikan_bangumi_id)).scalar_one_or_none()
    if bangumi is None:
        from app.services.metadata_service import enrich_bangumi
        bangumi = Bangumi(mikan_bangumi_id=payload.mikan_bangumi_id,
                          title=payload.bangumi_title or f"bangumi {payload.mikan_bangumi_id}")
        db.add(bangumi)
        db.flush()
        enrich_bangumi(db, bangumi)   # 三级降级,失败不抛、不阻塞创建
        from app.services.organize import detect_season
        bangumi.season_number = detect_season(bangumi.title)   # 续作季号自动猜,详情页可改

    existing = db.execute(select(Subscription).where(
        Subscription.bangumi_id == bangumi.id,
        Subscription.mikan_subgroup_id == payload.mikan_subgroup_id)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "该番剧+字幕组的订阅已存在")

    sub = Subscription(
        bangumi_id=bangumi.id,
        mikan_subgroup_id=payload.mikan_subgroup_id,
        subgroup_name=payload.subgroup_name,
        include_keywords=payload.include_keywords,
        exclude_keywords=payload.exclude_keywords,
        pinned_guids=payload.pinned_guids,
        blocked_guids=payload.blocked_guids,
        # 默认按连载状态推导:连载中排除合集,完结老番允许合集(场景 C 决议)
        exclude_batch=payload.exclude_batch if payload.exclude_batch is not None
        else bangumi.airing_status.value == "airing",
        backfill=payload.backfill,
        save_path=payload.save_path or f"{settings.download_root}/{_safe_dirname(bangumi.title)}",
    )
    db.add(sub)
    db.commit()

    background.add_task(_poll_in_background, sub.id)   # 创建后立即首轮轮询(补齐/建基线)
    return _to_out(sub, bangumi.title)


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(db: Session = Depends(get_db)):
    # 排除「本地导入/智能下载」容器订阅(它们只是挂文件的载体,不是用户 RSS 订阅)
    subs = db.execute(select(Subscription).where(
        Subscription.mikan_subgroup_id.notin_(["local", "auto"]))).scalars().all()
    return [_to_out(s, s.bangumi.title) for s in subs]


@router.patch("/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: int, payload: dict, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404)
    allowed = {"include_keywords", "exclude_keywords", "pinned_guids", "blocked_guids",
               "exclude_batch", "backfill", "save_path", "enabled", "subgroup_name",
               "episode_offset"}
    for k, v in payload.items():
        if k in allowed:
            setattr(sub, k, v)
    db.commit()
    background.add_task(_poll_in_background, sub.id)   # 规则变更立即重新评估
    return _to_out(sub, sub.bangumi.title)


def _purge_subscription(db: Session, sub: Subscription, delete_files: bool) -> None:
    """删除订阅及其下载任务/库记录(下载器任务一并移除,可选删文件)。番剧/剧集保留(可能被其它订阅共用)。"""
    from app.clients.downloader import downloader
    torrents = db.execute(select(Torrent).where(
        Torrent.subscription_id == sub.id)).scalars().all()
    for t in torrents:
        if t.info_hash:
            try:
                downloader.delete(t.info_hash, delete_files=delete_files)
            except Exception:  # noqa: BLE001 — 下载器里可能已不存在
                pass
    t_ids = [t.id for t in torrents]
    if t_ids:
        for vf in db.execute(select(VideoFile).where(VideoFile.torrent_id.in_(t_ids))).scalars():
            db.delete(vf)
        for te in db.execute(select(TorrentEpisode).where(
                TorrentEpisode.torrent_id.in_(t_ids))).scalars():
            db.delete(te)
        for t in torrents:
            db.delete(t)
    db.delete(sub)


@router.delete("/{sub_id}", status_code=204)
def delete_subscription(sub_id: int, delete_files: bool = False, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404)
    _purge_subscription(db, sub, delete_files)
    db.commit()


@router.post("/batch-delete")
def batch_delete(payload: dict, db: Session = Depends(get_db)):
    """批量删除订阅。payload: {ids:[...], delete_files?:bool}。"""
    ids = payload.get("ids") or []
    delete_files = bool(payload.get("delete_files"))
    done: list[int] = []
    failed: list[int] = []
    for sid in ids:
        sub = db.get(Subscription, sid)
        if not sub:
            failed.append(sid)
            continue
        _purge_subscription(db, sub, delete_files)
        done.append(sid)
    db.commit()
    return {"done": done, "failed": failed}


def _to_out(sub: Subscription, title: str) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id, bangumi_id=sub.bangumi_id,
        mikan_bangumi_id=sub.bangumi.mikan_bangumi_id, bangumi_title=title,
        mikan_subgroup_id=sub.mikan_subgroup_id, subgroup_name=sub.subgroup_name,
        include_keywords=sub.include_keywords or [], exclude_keywords=sub.exclude_keywords or [],
        pinned_guids=sub.pinned_guids or [], blocked_guids=sub.blocked_guids or [],
        bangumi_eps_total=sub.bangumi.eps_total,
        episode_offset=sub.episode_offset or 0,
        last_poll_ok=sub.last_poll_ok if sub.last_poll_ok is not None else True,
        last_poll_error=sub.last_poll_error,
        exclude_batch=sub.exclude_batch, backfill=sub.backfill, save_path=sub.save_path,
        enabled=sub.enabled, last_checked_at=sub.last_checked_at)
