"""番剧库与详情(虚拟库视图,ADR-0001:全部由数据库渲染)。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Bangumi, Episode, Subscription, Torrent, TorrentEpisode, TorrentStatus

router = APIRouter(prefix="/api/bangumi", tags=["bangumi"])


def _poster_url(b: Bangumi) -> str | None:
    return f"/data/{b.poster_path}" if b.poster_path else None


def _file_out(f) -> dict:
    """视频文件展示信息:分辨率 / 字幕组 / 片源(web/BD)/ 字幕轨 / 编码 / 码率。"""
    from pathlib import PurePosixPath
    return {
        "id": f.id, "path": f.relative_path, "name": PurePosixPath(f.relative_path).name,
        "size": f.size, "resolution": f.resolution, "subgroup": f.subgroup,
        "source": f.source, "codec": f.video_codec, "bitrate": f.bitrate,
        "audio_tracks": f.audio_tracks, "subtitle_tracks": f.subtitle_tracks,
    }


@router.get("")
def library(db: Session = Depends(get_db)):
    rows = db.execute(select(Bangumi).order_by(Bangumi.created_at.desc())).scalars().all()
    return [{
        "id": b.id, "title": b.title, "year": b.year, "season": b.season_str,
        "studio": b.studio, "score": b.score, "airing_status": b.airing_status.value,
        "poster": _poster_url(b),
        "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
        "eps_total": b.eps_total,
        "eps_downloaded": db.execute(
            select(Episode.id).join(TorrentEpisode).join(Torrent)
            .where(Episode.bangumi_id == b.id,
                   Torrent.status.in_([TorrentStatus.COMPLETED, TorrentStatus.ARCHIVED,
                                       TorrentStatus.DOWNLOADING]))
            .distinct()).scalars().all().__len__(),
    } for b in rows]


@router.get("/calendar/week")
def calendar(db: Session = Depends(get_db)):
    """放送日历:连载中番剧按星期分组(0=周一 … 6=周日)。"""
    from app.models import AiringStatus
    rows = db.execute(select(Bangumi).where(
        Bangumi.airing_status == AiringStatus.AIRING)).scalars().all()
    days: list[list] = [[] for _ in range(7)]
    unknown = []
    for b in rows:
        entry = {
            "id": b.id, "title": b.title, "poster": _poster_url(b),
            "score": b.score, "eps_total": b.eps_total,
            "eps_downloaded": db.execute(
                select(Episode.id).join(TorrentEpisode).join(Torrent)
                .where(Episode.bangumi_id == b.id,
                       Torrent.status.in_([TorrentStatus.COMPLETED, TorrentStatus.ARCHIVED]))
                .distinct()).scalars().all().__len__(),
        }
        if b.air_weekday is not None:
            days[b.air_weekday].append(entry)
        else:
            unknown.append(entry)
    return {"days": days, "unknown": unknown}


@router.get("/{bangumi_id}")
def detail(bangumi_id: int, db: Session = Depends(get_db)):
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    episodes = db.execute(select(Episode).where(Episode.bangumi_id == b.id)
                          .order_by(Episode.number)).scalars().all()
    eps_out = []
    known_numbers = {ep.number for ep in episodes if ep.type.value == "EP"}
    for ep in episodes:
        torrents = db.execute(
            select(Torrent).join(TorrentEpisode)
            .where(TorrentEpisode.episode_id == ep.id)
            .order_by(Torrent.version.desc())).scalars().all()
        current = next((t for t in torrents if t.status not in
                        (TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED)), None)
        eps_out.append({
            "id": ep.id, "number": ep.number, "type": ep.type.value,
            "status": current.status.value if current else "missing",
            "version": current.version if current else None,
            "torrent_id": current.id if current else None,
            "files": [_file_out(f) for f in (current.files if current else []) if f.is_active],
        })
    # 缺集占位:已知总集数时,把库里没有的集号渲染为"未下载"(详情页补全入口的依据)
    if b.eps_total:
        for n in range(1, b.eps_total + 1):
            if float(n) not in known_numbers:
                eps_out.append({"id": None, "number": float(n), "type": "EP",
                                "status": "missing", "version": None,
                                "torrent_id": None, "files": []})
        eps_out.sort(key=lambda e: (e["number"] is None, e["number"]))

    # 未匹配文件:登记进库但没解析到单集的视频(剧场版/合集/解析失败),也要展示,别让它隐身
    from app.models import VideoFile
    unmapped = db.execute(
        select(VideoFile).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == b.id,
               VideoFile.episode_id.is_(None), VideoFile.is_active.is_(True))
        .order_by(VideoFile.relative_path)).scalars().all()

    subs = db.execute(select(Subscription).where(Subscription.bangumi_id == b.id)).scalars().all()
    return {
        "id": b.id, "mikan_bangumi_id": b.mikan_bangumi_id,
        "title": b.title, "title_original": b.title_original,
        "year": b.year, "season": b.season_str, "studio": b.studio, "score": b.score,
        "summary": b.summary, "airing_status": b.airing_status.value,
        "eps_total": b.eps_total, "poster": _poster_url(b),
        "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
        "bgmtv_subject_id": b.bgmtv_subject_id, "tmdb_id": b.tmdb_id,
        "season_number": b.season_number or 1,
        "episodes": eps_out,
        "unmapped_files": [_file_out(f) for f in unmapped],
        "subscriptions": [{
            "id": s.id, "subgroup_name": s.subgroup_name, "enabled": s.enabled,
            "exclude_batch": s.exclude_batch, "include_keywords": s.include_keywords,
            "exclude_keywords": s.exclude_keywords,
        } for s in subs],
    }


def _purge_bangumi(db: Session, b: Bangumi, delete_files: bool) -> None:
    """级联删除番剧的订阅/剧集/任务记录,下载器任务一并移除(可选删文件)。"""
    from app.clients.downloader import downloader
    from app.models import VideoFile

    sub_ids = db.execute(select(Subscription.id).where(
        Subscription.bangumi_id == b.id)).scalars().all()
    torrents = db.execute(select(Torrent).where(
        Torrent.subscription_id.in_(sub_ids))).scalars().all() if sub_ids else []
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
    for ep in db.execute(select(Episode).where(Episode.bangumi_id == b.id)).scalars():
        db.delete(ep)
    for s in db.execute(select(Subscription).where(
            Subscription.bangumi_id == b.id)).scalars():
        db.delete(s)
    db.delete(b)


@router.delete("/{bangumi_id}", status_code=204)
def remove(bangumi_id: int, delete_files: bool = False, db: Session = Depends(get_db)):
    """移除番剧:级联删除订阅/剧集/任务记录,下载器任务一并移除(可选删文件)。"""
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    _purge_bangumi(db, b, delete_files)
    db.commit()


@router.post("/batch-delete")
def batch_delete(payload: dict, db: Session = Depends(get_db)):
    """批量移除番剧。payload: {ids:[...], delete_files?:bool}。"""
    ids = payload.get("ids") or []
    delete_files = bool(payload.get("delete_files"))
    done: list[int] = []
    failed: list[int] = []
    for bid in ids:
        b = db.get(Bangumi, bid)
        if not b:
            failed.append(bid)
            continue
        _purge_bangumi(db, b, delete_files)
        done.append(bid)
    db.commit()
    return {"done": done, "failed": failed}


@router.patch("/{bangumi_id}")
def update_bangumi(bangumi_id: int, payload: dict, db: Session = Depends(get_db)):
    """编辑番剧元数据(目前:season_number 续作季号,整理重命名用)。"""
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if "season_number" in payload:
        try:
            b.season_number = max(0, int(payload["season_number"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "season_number 非法") from None
    db.commit()
    return {"ok": True, "season_number": b.season_number}


@router.post("/{bangumi_id}/rebind")
def rebind(bangumi_id: int, payload: dict, db: Session = Depends(get_db)):
    """bgm.tv 自动关联失败/错误时手动绑定 subject。"""
    from app.services.metadata_service import enrich_bangumi
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    subject_id = payload.get("bgmtv_subject_id")
    if not subject_id:
        raise HTTPException(400, "缺少 bgmtv_subject_id")
    enrich_bangumi(db, b, bgmtv_subject_id=int(subject_id))
    db.commit()
    return {"ok": True, "title": b.title, "studio": b.studio, "year": b.year}


@router.post("/{bangumi_id}/refresh-metadata")
def refresh(bangumi_id: int, db: Session = Depends(get_db)):
    from app.services.metadata_service import enrich_bangumi
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    enrich_bangumi(db, b)
    db.commit()
    return {"ok": True, "title": b.title}
