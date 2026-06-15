"""番剧库与详情(虚拟库视图,ADR-0001:全部由数据库渲染)。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (Bangumi, BdRelease, Episode, EpisodeType, Kind, Subscription,
                        Torrent, TorrentEpisode, TorrentStatus, VideoFile)
from app.services.local_import import LOCAL_SUBGROUP_ID

router = APIRouter(prefix="/api/bangumi", tags=["bangumi"])


def _poster_url(b: Bangumi) -> str | None:
    return f"/data/{b.poster_path}" if b.poster_path else None


def _file_out(f) -> dict:
    """视频文件展示信息:分辨率/字幕组/片源/编码/色深/HDR/码率 + 音轨(含声道)/字幕轨(含外挂)。"""
    from pathlib import PurePosixPath
    return {
        "id": f.id, "path": f.relative_path, "name": PurePosixPath(f.relative_path).name,
        "size": f.size, "resolution": f.resolution, "subgroup": f.subgroup,
        "source": f.source, "codec": f.video_codec,
        "color_depth": f.color_depth, "hdr": f.hdr, "bitrate": f.bitrate,
        "audio_tracks": f.audio_tracks, "subtitle_tracks": f.subtitle_tracks,
    }


@router.get("")
def library(db: Session = Depends(get_db)):
    rows = db.execute(select(Bangumi).order_by(Bangumi.created_at.desc())).scalars().all()
    return [{
        "id": b.id, "title": b.title, "year": b.year, "season": b.season_str,
        "studio": b.studio, "score": b.score, "airing_status": b.airing_status.value,
        "kind": b.kind.value, "auto_best": b.auto_best,
        "has_mikan": b.mikan_bangumi_id is not None,
        "poster": _poster_url(b),
        "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
        "eps_total": b.eps_total,
        # 影片/OVA 没有"正片集"概念,用是否有入库文件表达"已入库"(剧场版文件常未映射到集)
        "has_resource": bool(db.execute(
            select(VideoFile.id).join(Torrent).join(Subscription)
            .where(Subscription.bangumi_id == b.id, VideoFile.is_active.is_(True))
            .limit(1)).first()),
        "eps_downloaded": _eps_done(db, b),
        "has_bd": _has_source(db, b.id, "BD"),
        "has_web": _has_source(db, b.id, "Web"),
    } for b in rows]


def _eps_done(db: Session, b: Bangumi) -> int:
    """已入库集数:只数有 active 文件的**正片**集(不含 SP/菜单/NC/特典),并封顶到总集数。

    封顶规避:跨季连续编号(S2 编 13-24)/ 错误元数据 / 旧整理残留的幽灵集 等导致的「超过总集数」。
    """
    n = db.execute(
        select(Episode.id).join(VideoFile, VideoFile.episode_id == Episode.id)
        .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
               VideoFile.is_active.is_(True)).distinct()).scalars().all().__len__()
    return min(n, b.eps_total) if b.eps_total else n


def _has_source(db: Session, bangumi_id: int, source: str) -> bool:
    """该番剧是否有指定片源的 active 文件;BD 还认 BD 发行记录(BD 收藏扫描登记的)。"""
    if source == "BD" and db.execute(select(BdRelease.id).where(
            BdRelease.bangumi_id == bangumi_id).limit(1)).first():
        return True
    return bool(db.execute(
        select(VideoFile.id).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == bangumi_id, VideoFile.is_active.is_(True),
               VideoFile.source == source).limit(1)).first())


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
            "eps_downloaded": _eps_done(db, b),
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
    # 正片按集号、非正片(SP/OP·ED/PV…)按类型+序号,统一排序
    _TYPE_ORDER = {EpisodeType.REGULAR: 0, EpisodeType.SPECIAL: 1, EpisodeType.CREDITS: 2,
                   EpisodeType.TRAILER: 3, EpisodeType.OTHER: 4}
    episodes = db.execute(select(Episode).where(Episode.bangumi_id == b.id)).scalars().all()
    episodes.sort(key=lambda e: (_TYPE_ORDER.get(e.type, 9),
                                 e.number if e.number is not None else 1e9))
    eps_out = []
    known_numbers = {ep.number for ep in episodes if ep.type == EpisodeType.REGULAR}
    for ep in episodes:
        torrents = db.execute(
            select(Torrent).join(TorrentEpisode)
            .where(TorrentEpisode.episode_id == ep.id)
            .order_by(Torrent.version.desc())).scalars().all()
        current = next((t for t in torrents if t.status not in
                        (TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED)), None)
        # 只取映射到「这一集」的文件,不是整个种子(合集/番剧库容器种子覆盖多集,
        # 否则每集都会列出整包的全部文件)。is_active 处理 v2 切换。
        ep_files = db.execute(
            select(VideoFile).where(VideoFile.episode_id == ep.id, VideoFile.is_active.is_(True))
            .order_by(VideoFile.relative_path)).scalars().all()
        eps_out.append({
            "id": ep.id, "number": ep.number, "type": ep.type.value, "title": ep.title,
            "status": current.status.value if current else "missing",
            "version": current.version if current else None,
            "torrent_id": current.id if current else None,
            "files": [_file_out(f) for f in ep_files],
        })
    # 缺集占位:仅 tv 番剧 + 已知总集数时,把库里没有的正片集号渲染为"未下载"(补全入口依据)。
    # movie/ova 没有"正片集"概念,不补占位。
    if b.eps_total and b.kind == Kind.TV:
        regular = [e for e in eps_out if e["type"] == "regular"]
        others = [e for e in eps_out if e["type"] != "regular"]
        for n in range(1, b.eps_total + 1):
            if float(n) not in known_numbers:
                regular.append({"id": None, "number": float(n), "type": "regular", "title": None,
                                "status": "missing", "version": None,
                                "torrent_id": None, "files": []})
        regular.sort(key=lambda e: (e["number"] is None, e["number"]))
        eps_out = regular + others

    # 未匹配文件:登记进库但没解析到单集的视频(剧场版/合集/解析失败),也要展示,别让它隐身
    unmapped = db.execute(
        select(VideoFile).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == b.id,
               VideoFile.episode_id.is_(None), VideoFile.is_active.is_(True))
        .order_by(VideoFile.relative_path)).scalars().all()

    # 不展示「智能下载」内部容器订阅(它的种子已按集出现在剧集列表里;本地导入容器仍展示)
    subs = db.execute(select(Subscription).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id != "auto")).scalars().all()
    return {
        "id": b.id, "mikan_bangumi_id": b.mikan_bangumi_id,
        "title": b.title, "title_original": b.title_original,
        "year": b.year, "season": b.season_str, "studio": b.studio, "score": b.score,
        "summary": b.summary, "airing_status": b.airing_status.value, "kind": b.kind.value,
        "eps_total": b.eps_total, "poster": _poster_url(b),
        "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
        "bgmtv_subject_id": b.bgmtv_subject_id, "tmdb_id": b.tmdb_id,
        "anidb_aid": b.anidb_aid,
        "anidb_synced_at": b.anidb_synced_at.isoformat() if b.anidb_synced_at else None,
        "season_number": b.season_number or 1,
        "auto_best": b.auto_best, "bd_owned": b.bd_owned,
        "bd_releases": _bd_releases_out(db, b.id),
        "episodes": eps_out,
        "unmapped_files": [_file_out(f) for f in unmapped],
        "subscriptions": [_sub_out(s) for s in subs],
    }


def _bd_releases_out(db: Session, bangumi_id: int) -> list[dict]:
    from app.api.bd import bd_release_out
    from app.models import BdRelease
    rows = db.execute(select(BdRelease).where(
        BdRelease.bangumi_id == bangumi_id)).scalars().all()
    return [bd_release_out(r) for r in rows]


def _sub_out(s: Subscription) -> dict:
    """订阅源详情(详情页订阅卡):字幕组 / 规则 / RSS 健康 / 上次检查 / 本地容器标记。"""
    is_local = s.mikan_subgroup_id == LOCAL_SUBGROUP_ID
    return {
        "id": s.id, "subgroup_name": s.subgroup_name, "mikan_subgroup_id": s.mikan_subgroup_id,
        "enabled": s.enabled, "is_local": is_local,
        "exclude_batch": s.exclude_batch, "backfill": s.backfill,
        "include_keywords": s.include_keywords or [], "exclude_keywords": s.exclude_keywords or [],
        "pinned_guids": s.pinned_guids or [], "blocked_guids": s.blocked_guids or [],
        "episode_offset": s.episode_offset or 0,
        "last_poll_ok": s.last_poll_ok, "last_poll_error": s.last_poll_error,
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
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
    """编辑番剧元数据:season_number(续作季号)/ kind(形态,手动覆盖始终优先)。"""
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if "season_number" in payload:
        try:
            b.season_number = max(0, int(payload["season_number"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "season_number 非法") from None
    if "kind" in payload:
        try:
            b.kind = Kind(str(payload["kind"]).lower())
        except ValueError:
            raise HTTPException(400, "kind 非法(tv/movie/ova)") from None
    if "auto_best" in payload:
        b.auto_best = bool(payload["auto_best"])
    if "bd_owned" in payload:
        b.bd_owned = bool(payload["bd_owned"])
    db.commit()
    return {"ok": True, "season_number": b.season_number, "kind": b.kind.value,
            "auto_best": b.auto_best, "bd_owned": b.bd_owned}


@router.post("/{bangumi_id}/sync-anidb")
def sync_anidb(bangumi_id: int, db: Session = Depends(get_db)):
    """按需同步 AniDB 剧集表(剧集类型/标题/形态)。需在设置里启用 AniDB。"""
    from app.services.anidb_service import sync_episodes
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    result = sync_episodes(db, b, force=True)
    db.commit()
    if not result.get("ok") and result.get("reason") == "disabled":
        raise HTTPException(400, "AniDB 未启用(在设置里开启并填 client 名)")
    return result


@router.get("/{bangumi_id}/anidb-candidates")
def anidb_candidates(bangumi_id: int, query: str = "", db: Session = Depends(get_db)):
    """AniDB 候选(手动绑 aid 用)。不传 query 用番剧原名/中文名搜。"""
    from app.services.anidb_service import search_candidates
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    q = query.strip() or b.title_original or b.title
    return {"query": q, "candidates": search_candidates(q)}


@router.post("/{bangumi_id}/bind-anidb")
def bind_anidb(bangumi_id: int, payload: dict, db: Session = Depends(get_db)):
    """手动绑定 AniDB aid,并立即同步。"""
    from app.services.anidb_service import sync_episodes
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    aid = payload.get("aid")
    if not aid:
        raise HTTPException(400, "缺少 aid")
    b.anidb_aid = int(aid)
    b.anidb_synced_at = None   # 强制重新同步
    result = sync_episodes(db, b, force=True)
    db.commit()
    return {"ok": True, "anidb_aid": b.anidb_aid, **result}


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


# ---- 智能下载(扫所有字幕组挑最佳源:BD>Web、严格分辨率/简中)--------------------

@router.get("/auto-scan/status")
def auto_scan_status():
    from app.services.auto_best import state
    return state


@router.post("/auto-scan")
def auto_scan(payload: dict, db: Session = Depends(get_db)):
    """批量智能扫描。payload: {ids:[...], enable_auto?:bool}。
    enable_auto=True 时同时把这些番剧设为常驻智能下载(定期扫描升级)。"""
    from app.services import auto_best
    ids = [int(i) for i in (payload.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "没有选择番剧")
    if payload.get("enable_auto"):
        for bid in ids:
            b = db.get(Bangumi, bid)
            if b:
                b.auto_best = True
        db.commit()
    if not auto_best.start_scan(ids):
        raise HTTPException(409, "已有智能扫描在进行中")
    return {"started": True, "total": len(ids)}


@router.post("/{bangumi_id}/auto-scan")
def auto_scan_one(bangumi_id: int, db: Session = Depends(get_db)):
    """单部立即智能扫描(详情页按钮)。补全缺集 + 升级现有源。"""
    from app.services import auto_best
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if not b.mikan_bangumi_id:
        raise HTTPException(400, "该番剧无蜜柑 ID(本地导入),无法扫描线上源")
    if not auto_best.start_scan([bangumi_id]):
        raise HTTPException(409, "已有智能扫描在进行中")
    return {"started": True}
