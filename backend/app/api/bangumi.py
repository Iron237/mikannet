"""番剧库与详情(虚拟库视图,ADR-0001:全部由数据库渲染)。"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (Bangumi, BdRelease, Episode, EpisodeType, Kind, Subscription,
                        Torrent, TorrentEpisode, TorrentStatus, VideoFile)
from app.services.local_import import LOCAL_SUBGROUP_ID

router = APIRouter(prefix="/api/bangumi", tags=["bangumi"])
log = logging.getLogger(__name__)


def _poster_url(b: Bangumi) -> str | None:
    return f"/data/{b.poster_path}" if b.poster_path else None


def _file_out(f) -> dict:
    """视频文件展示信息:分辨率/字幕组/片源/编码/色深/HDR/码率 + 音轨(含声道)/字幕轨(含外挂)
    + 原生启动 URL(本机默认播放器播放 / 资源管理器定位;未配置宿主前缀则为 None)。"""
    from pathlib import PurePosixPath

    from app.services import launch
    return {
        "id": f.id, "path": f.relative_path, "name": PurePosixPath(f.relative_path).name,
        "original_name": f.original_name,   # 整理改名前的原始文件名(保留字幕组/版本等信息)
        "size": f.size, "resolution": f.resolution, "subgroup": f.subgroup,
        "source": f.source, "codec": f.video_codec,
        "color_depth": f.color_depth, "hdr": f.hdr, "bitrate": f.bitrate,
        "audio_tracks": f.audio_tracks, "subtitle_tracks": f.subtitle_tracks,
        "play_url": launch.media_launch("play", f.relative_path),
        "reveal_url": launch.media_launch("reveal", f.relative_path),
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
        "eps_aired": _eps_aired(db, b),
        "has_bd": _has_source(db, b.id, "BD"),
        "has_web": _has_source(db, b.id, "Web"),
        # 封面墙右下角「源」角标:原盘(自购)优先,其次正片已全替为 BDrip
        "bd_owned": b.bd_owned,
        "bd_rip": _has_active_file_source(db, b.id, "BD"),
    } for b in rows]


@router.post("/from-mikan")
def ensure_from_mikan(payload: dict, db: Session = Depends(get_db)):
    """从蜜柑番剧 ID 建/取本地番剧(不建 web 订阅);供「添加BD源」先把番入库,再绑 BD 发行 + 导入正片。"""
    mid = payload.get("mikan_bangumi_id")
    if mid is None:
        raise HTTPException(400, "缺少 mikan_bangumi_id")
    b = db.execute(select(Bangumi).where(
        Bangumi.mikan_bangumi_id == int(mid))).scalar_one_or_none()
    if b is None:
        from app.services.metadata_service import enrich_bangumi
        from app.services.organize import detect_season
        b = Bangumi(mikan_bangumi_id=int(mid), title=payload.get("title") or f"bangumi {mid}")
        db.add(b)
        db.flush()
        try:
            enrich_bangumi(db, b)                  # 三级降级,失败不抛
            b.season_number = detect_season(b.title)
        except Exception:  # noqa: BLE001 — 元数据失败不阻塞入库
            pass
    db.commit()
    return {"id": b.id, "title": b.title, "mikan_bangumi_id": b.mikan_bangumi_id}


def _eps_done(db: Session, b: Bangumi, official_only: bool = False) -> int:
    """已入库集数:只数有 active 文件的**正片**集(不含 SP/菜单/NC/特典),并封顶到总集数。

    封顶规避:跨季连续编号(S2 编 13-24)/ 错误元数据 / 旧整理残留的幽灵集 等导致的「超过总集数」。
    official_only:只数正式流(先行放送内容不算,「已播」推断用)。
    """
    q = (select(Episode.id).join(VideoFile, VideoFile.episode_id == Episode.id)
         .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
                VideoFile.is_active.is_(True)))
    if official_only:
        q = q.join(Torrent, VideoFile.torrent_id == Torrent.id).where(
            Torrent.is_preview.is_(False))
    n = len(db.execute(q.distinct()).scalars().all())
    return min(n, b.eps_total) if b.eps_total else n


def _weekly_aired(air_date: str | None) -> int:
    """按首播日 + 周更推算已播出集数(无每集播出日时的兜底)。未开播 → 0。"""
    if not air_date:
        return 0
    from datetime import date
    try:
        start = date.fromisoformat(air_date[:10].replace("/", "-"))
    except ValueError:
        return 0
    days = (date.today() - start).days
    return days // 7 + 1 if days >= 0 else 0


def _eps_aired(db: Session, b: Bangumi) -> int | None:
    """已播/已发布集数(真实更新情况):取该番**所有种子**(含 SKIPPED 留痕)`parsed_json`
    解析出的最大正片集号 = 真实「种子已出到第几集」;没见过种子时按首播日 + 周更推算。封顶到总集数。

    仅 TV 且有总集数才有「已播集」概念;算不出(无种子且无首播日)→ None(前端退回只显已下载)。
    SKIPPED 种子不建 torrent_episode 映射,故必须读 parsed_json 而非 Episode 表。
    """
    if not b.eps_total or b.kind != Kind.TV:
        return None
    start = b.ep_start or 1
    seen = 0   # 已播「第几集」(数量口径,1..eps_total)
    # 只看正式流的种子:先行放送(官方开播前的网络先行)不算「已播」
    rows = db.execute(
        select(Torrent.parsed_json, Subscription.episode_offset)
        .join(Subscription, Torrent.subscription_id == Subscription.id)
        .where(Subscription.bangumi_id == b.id, Torrent.is_preview.is_(False))).all()
    for pj, offset in rows:
        for e in (pj or {}).get("episodes") or []:
            try:
                # 原始集号 → bangumi 编号(减订阅偏移)→ 数量口径(减 ep_start-1)
                seen = max(seen, int(float(e)) - (offset or 0) - (start - 1))
            except (TypeError, ValueError):
                continue
    if seen == 0:
        seen = _weekly_aired(b.air_date)
    # 已下载的正式集必然已播 → 下限(只用正式流数,先行集齐不代表官方播过)
    seen = max(seen, _eps_done(db, b, official_only=True))
    return min(seen, b.eps_total) if seen > 0 else None


def _has_source(db: Session, bangumi_id: int, source: str) -> bool:
    """该番剧是否有指定片源的 active 文件;BD 还认 BD 发行记录(BD 收藏扫描登记的)。"""
    if source == "BD" and db.execute(select(BdRelease.id).where(
            BdRelease.bangumi_id == bangumi_id).limit(1)).first():
        return True
    return _has_active_file_source(db, bangumi_id, source)


def _has_active_file_source(db: Session, bangumi_id: int, source: str) -> bool:
    """该番剧是否有指定片源的 active 视频文件(不认 BD 发行记录,用于「BDrip 已替换正片」角标)。"""
    return bool(db.execute(
        select(VideoFile.id).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == bangumi_id, VideoFile.is_active.is_(True),
               VideoFile.source == source).limit(1)).first())


def _upcoming_this_week(db: Session, b: Bangumi) -> dict | None:
    """下一个将更新的话:放送表是前瞻视角,展示「将要更新什么」。

    优先每集精确放送日(bgm.tv 章节 airdate,休播/延期天然准确);
    没有每集数据时退回「首播日 + 周更」外推。显示用 bangumi 编号。
    未开播 → None(前端显示「N月N日开播」);全部播完 → over=True。
    """
    from datetime import date, timedelta
    today = date.today()
    # 1) 每集精确放送日
    rows = db.execute(select(Episode.number, Episode.air_date).where(
        Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
        Episode.number.is_not(None), Episode.air_date.is_not(None))).all()
    dated = []
    for num, ad in rows:
        try:
            dated.append((date.fromisoformat(ad[:10].replace("/", "-")), num))
        except ValueError:
            continue
    if dated:
        future = sorted((d, n) for d, n in dated if d >= today)
        if not future:
            return {"over": True}
        d, n = future[0]
        first_num = min(n2 for _, n2 in dated)
        return {"over": False, "number": int(n) if float(n).is_integer() else n,
                "date": d.isoformat(), "premiere": n == first_num}
    # 2) 周更外推兜底
    if not b.air_date or b.air_weekday is None:
        return None
    try:
        start = date.fromisoformat(b.air_date[:10].replace("/", "-"))
    except ValueError:
        return None
    target = today + timedelta(days=(b.air_weekday - today.weekday()) % 7)
    if target < start:
        return None
    cnt = (target - start).days // 7 + 1
    if b.eps_total and cnt > b.eps_total:
        return {"over": True}
    return {"over": False, "number": (b.ep_start or 1) + cnt - 1,
            "date": target.isoformat(), "premiere": cnt == 1}


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
            "ep_start": b.ep_start or 1,   # 放送表按 bangumi 编号显示「第 N 话」
            "air_date": b.air_date,        # 未开播的显示「N月N日开播」
            "upcoming": _upcoming_this_week(db, b),   # 前瞻:下一话何时更新
            "eps_downloaded": _eps_done(db, b),
            "eps_aired": _eps_aired(db, b),
        }
        up = entry["upcoming"]
        wd = b.air_weekday
        if up and not up.get("over") and up.get("date"):
            from datetime import date as _date
            # 按下一话的真实日期归列:延期/特别编排的集落到实际播出的星期
            wd = _date.fromisoformat(up["date"]).weekday()
        if wd is not None:
            days[wd].append(entry)
        else:
            unknown.append(entry)
    return {"days": days, "unknown": unknown}


@router.post("/calendar/refresh")
def calendar_refresh():
    """手动重拉连载番剧的 bgm.tv 放送信息(右上角刷新按钮)。

    变动在响应里展示(不推送;定时任务检测到的变动才推送)。同步执行,
    连载中番剧通常十来部、每部间隔 0.4s,数秒内返回。"""
    from app.services.metadata_service import refresh_air_dates
    return {"ok": True, **refresh_air_dates(notify_changes=False)}


def _has_phase(db: Session, bangumi_id: int, is_preview: bool) -> bool:
    """该番剧是否有指定阶段(先行/正式)的非留痕种子 → 决定详情页是否显示分段切换。"""
    return bool(db.execute(
        select(Torrent.id).join(Subscription, Torrent.subscription_id == Subscription.id)
        .where(Subscription.bangumi_id == bangumi_id, Torrent.is_preview.is_(is_preview),
               Torrent.status.notin_([TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED]))
        .limit(1)).first())


@router.get("/{bangumi_id}")
def detail(bangumi_id: int, phase: str | None = None, db: Session = Depends(get_db)):
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    has_preview = _has_phase(db, b.id, True)
    has_official = _has_phase(db, b.id, False)
    # 阶段:未指定时,只有先行没正式 → 默认先行;否则默认正式。
    if phase not in ("preview", "official"):
        phase = "preview" if (has_preview and not has_official) else "official"
    want_preview = phase == "preview"

    # 正片按集号、非正片(SP/OP·ED/PV…)按类型+序号,统一排序
    _TYPE_ORDER = {EpisodeType.REGULAR: 0, EpisodeType.SPECIAL: 1, EpisodeType.CREDITS: 2,
                   EpisodeType.TRAILER: 3, EpisodeType.OTHER: 4}
    episodes = db.execute(select(Episode).where(Episode.bangumi_id == b.id)).scalars().all()
    episodes.sort(key=lambda e: (_TYPE_ORDER.get(e.type, 9),
                                 e.number if e.number is not None else 1e9))
    eps_out = []
    for ep in episodes:
        torrents = db.execute(
            select(Torrent).join(TorrentEpisode)
            .where(TorrentEpisode.episode_id == ep.id, Torrent.is_preview.is_(want_preview))
            .order_by(Torrent.version.desc())).scalars().all()
        current = next((t for t in torrents if t.status not in
                        (TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED)), None)
        # 只取映射到「这一集」且属当前阶段的文件(合集/容器种子覆盖多集,is_active 处理 v2 切换)。
        ep_files = db.execute(
            select(VideoFile).join(Torrent, VideoFile.torrent_id == Torrent.id)
            .where(VideoFile.episode_id == ep.id, VideoFile.is_active.is_(True),
                   Torrent.is_preview.is_(want_preview))
            .order_by(VideoFile.relative_path)).scalars().all()
        if current is None and not ep_files:
            continue   # 这一集在当前阶段无任何内容 → 不列(正式阶段下面按总集数补缺占位)
        eps_out.append({
            "id": ep.id, "number": ep.number, "type": ep.type.value, "title": ep.title,
            "air_date": ep.air_date,   # 每集精确放送日(bgm.tv 章节同步)
            "status": current.status.value if current else "missing",
            "version": current.version if current else None,
            "torrent_id": current.id if current else None,
            "files": [_file_out(f) for f in ep_files],
        })
    known_numbers = {e["number"] for e in eps_out if e["type"] == "regular"}
    # 缺集占位:仅正式阶段 + tv 番剧 + 已知总集数时,把没有的正片集号渲染为"未下载"(补全入口依据)。
    # 先行阶段只展示已有先行内容,不铺满占位;movie/ova 没有"正片集"概念,不补占位。
    if not want_preview and b.eps_total and b.kind == Kind.TV:
        regular = [e for e in eps_out if e["type"] == "regular"]
        others = [e for e in eps_out if e["type"] != "regular"]
        # 占位区间用 bangumi 编号(续作 ep_start=13 → 铺 13-25,而非永远等不来的 1-12)
        _start = b.ep_start or 1
        for n in range(_start, _start + b.eps_total):
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
               VideoFile.episode_id.is_(None), VideoFile.is_active.is_(True),
               Torrent.is_preview.is_(want_preview))
        .order_by(VideoFile.relative_path)).scalars().all()

    # 不展示「智能下载」内部容器订阅(它的种子已按集出现在剧集列表里;本地导入容器仍展示)
    subs = db.execute(select(Subscription).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id != "auto")).scalars().all()
    return {
        "id": b.id, "mikan_bangumi_id": b.mikan_bangumi_id,
        "mikan_url": (f"{settings.mikan_base_url.rstrip('/')}/Home/Bangumi/{b.mikan_bangumi_id}"
                      if b.mikan_bangumi_id else None),
        "title": b.title, "title_original": b.title_original,
        "year": b.year, "season": b.season_str, "studio": b.studio, "score": b.score,
        "summary": b.summary, "airing_status": b.airing_status.value, "kind": b.kind.value,
        "eps_total": b.eps_total, "poster": _poster_url(b),
        "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
        "bgmtv_subject_id": b.bgmtv_subject_id, "tmdb_id": b.tmdb_id,
        "anidb_aid": b.anidb_aid,
        "anidb_synced_at": b.anidb_synced_at.isoformat() if b.anidb_synced_at else None,
        "season_number": b.season_number or 1,
        "ep_start": b.ep_start or 1,
        "auto_best": b.auto_best, "bd_owned": b.bd_owned,
        "air_date": b.air_date,
        "phase": phase, "has_preview": has_preview, "has_official": has_official,
        "bd_releases": _bd_releases_out(db, b.id),
        "episodes": eps_out,
        "unmapped_files": [_file_out(f) for f in unmapped],
        "subscriptions": [_sub_out(s) for s in subs],
    }


def _bd_releases_out(db: Session, bangumi_id: int) -> list[dict]:
    """详情页返发行实体 + 打开目录 URL(去特典分支:特典不编目、不在网页展示)。

    跨季 BD:一套发行可横跨多季(连续编号的整盘),其正片被「分别导入」到不同季的番剧。
    除主绑定外,凡有本番剧的 BD 正片(active)落在某发行目录内的,也在本页展示该发行卡片
    —— 这样同一张碟在 S1 / S2 详情页都能看到并「打开目录」。
    """
    from app.api.bd import bd_release_out
    from app.models import BdRelease
    rows = list(db.execute(select(BdRelease).where(
        BdRelease.bangumi_id == bangumi_id)).scalars().all())
    seen = {r.id for r in rows}
    bd_paths = db.execute(
        select(VideoFile.relative_path).join(Torrent).join(Subscription).where(
            Subscription.bangumi_id == bangumi_id, VideoFile.source == "BD",
            VideoFile.is_active.is_(True))).scalars().all()
    if bd_paths:
        for r in db.execute(select(BdRelease)).scalars().all():
            if r.id in seen:
                continue
            if any(p == r.root_path or p.startswith(r.root_path + "/") for p in bd_paths):
                rows.append(r)
                seen.add(r.id)
    return [bd_release_out(r) for r in rows]


def _sub_source(s: Subscription) -> str:
    """订阅来源:rss(用户 RSS 订阅)/ local(本地导入容器)/ auto(智能下载容器)。"""
    return {"local": "local", "auto": "auto"}.get(s.mikan_subgroup_id, "rss")


def _sub_out(s: Subscription) -> dict:
    """订阅源详情(详情页订阅卡):字幕组 / 规则 / RSS 健康 / 上次检查 / 来源标记。"""
    is_local = s.mikan_subgroup_id == LOCAL_SUBGROUP_ID
    return {
        "id": s.id, "subgroup_name": s.subgroup_name, "mikan_subgroup_id": s.mikan_subgroup_id,
        "enabled": s.enabled, "is_local": is_local, "source": _sub_source(s),
        "exclude_batch": s.exclude_batch, "backfill": s.backfill,
        "include_keywords": s.include_keywords or [], "exclude_keywords": s.exclude_keywords or [],
        "pinned_guids": s.pinned_guids or [], "blocked_guids": s.blocked_guids or [],
        "episode_offset": s.episode_offset or 0,
        "last_poll_ok": s.last_poll_ok, "last_poll_error": s.last_poll_error,
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
    }


def _purge_bangumi(db: Session, b: Bangumi, delete_files: bool) -> None:
    """级联删除番剧的订阅/剧集/任务记录,下载器任务一并移除(可选删文件)。

    按外键依赖分阶段 flush(子表先落删再删父):SQLAlchemy 工作单元对无 relationship 的外键
    (如 bd_release.bangumi_id)不会自动排删除顺序,单次 commit 可能先删 bangumi → 撞 FK 约束。
    """
    import os

    from app.clients.downloader import downloader
    from app.models import VideoFile

    sub_ids = db.execute(select(Subscription.id).where(
        Subscription.bangumi_id == b.id)).scalars().all()
    torrents = db.execute(select(Torrent).where(
        Torrent.subscription_id.in_(sub_ids))).scalars().all() if sub_ids else []
    no_dl = {t.id for t in torrents if not t.info_hash}   # 本地导入/库容器:不在下载器里
    for t in torrents:
        if t.info_hash:
            try:
                downloader.delete(t.info_hash, delete_files=delete_files)
            except Exception:  # noqa: BLE001 — 下载器里可能已不存在
                pass

    t_ids = [t.id for t in torrents]
    if t_ids:
        for vf in db.execute(select(VideoFile).where(VideoFile.torrent_id.in_(t_ids))).scalars():
            # 容器(本地/库扫描)文件下载器删不到 → 勾选删文件时直接删盘(限下载根内,绝不碰已购原盘)
            if delete_files and vf.torrent_id in no_dl:
                try:
                    os.remove(settings.download_root_local / vf.relative_path)
                except OSError:
                    pass
            db.delete(vf)
        for te in db.execute(select(TorrentEpisode).where(
                TorrentEpisode.torrent_id.in_(t_ids))).scalars():
            db.delete(te)
        db.flush()                       # 文件/集关联先落删,解开对 torrent/episode 的引用
        for t in torrents:
            db.delete(t)
    for ep in db.execute(select(Episode).where(Episode.bangumi_id == b.id)).scalars():
        db.delete(ep)
    db.flush()                           # torrent/episode 落删,解开对 subscription/bangumi 的引用
    for s in db.execute(select(Subscription).where(
            Subscription.bangumi_id == b.id)).scalars():
        db.delete(s)
    # BD 发行(extras 经 relationship cascade 一并删);bd_release.bangumi_id 无 relationship,
    # 必须在删 bangumi 前显式落删并 flush,否则 FK 约束报错
    for br in db.execute(select(BdRelease).where(BdRelease.bangumi_id == b.id)).scalars():
        db.delete(br)
    db.flush()
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


@router.get("/{bangumi_id}/auto-status")
def auto_status(bangumi_id: int, db: Session = Depends(get_db)):
    """智能下载当前状态(详情页状态卡):开关/上次扫描摘要/auto 种子分布/缺集与在途。"""
    from app.services import auto_best
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    # auto 容器订阅的种子按状态分布
    counts: dict[str, int] = {}
    auto_sub_id = db.execute(select(Subscription.id).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id == auto_best.AUTO_SUBGROUP_ID)).scalar_one_or_none()
    in_flight_eps: set[float] = set()
    if auto_sub_id:
        for t in db.execute(select(Torrent).where(
                Torrent.subscription_id == auto_sub_id)).scalars():
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
            if t.status in (TorrentStatus.PENDING, TorrentStatus.DOWNLOADING,
                            TorrentStatus.COMPLETED):
                for te in db.execute(select(Episode.number).join(
                        TorrentEpisode, TorrentEpisode.episode_id == Episode.id)
                        .where(TorrentEpisode.torrent_id == t.id,
                               Episode.number.is_not(None))).scalars():
                    in_flight_eps.add(te)
    # 缺集(bangumi 编号):区间内没有 active 正片文件的集
    missing: list = []
    if b.eps_total and b.kind == Kind.TV:
        have = {n for n in db.execute(
            select(Episode.number).join(VideoFile, VideoFile.episode_id == Episode.id)
            .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
                   VideoFile.is_active.is_(True), Episode.number.is_not(None))
            .distinct()).scalars()}
        start = b.ep_start or 1
        missing = [n for n in range(start, start + b.eps_total) if float(n) not in have]
    scanning = bool(auto_best.state.get("running")) and (
        auto_best.state.get("current") == b.title or auto_best.state.get("total", 0) > 1)
    return {
        "enabled": bool(b.auto_best),
        "scanning": scanning,
        "last_scan_at": b.auto_scan_at.isoformat() + "Z" if b.auto_scan_at else None,
        "last_result": b.auto_scan_result,
        "torrents": counts,
        "missing": missing,
        "in_flight": sorted(int(n) if float(n).is_integer() else n
                            for n in in_flight_eps if float(n) in {float(m) for m in missing}),
    }


_related_cache: dict[int, tuple[float, list]] = {}   # subject_id → (ts, data),6h TTL


@router.get("/{bangumi_id}/related")
def related(bangumi_id: int, db: Session = Depends(get_db)):
    """关联条目(前作/续作/剧场版/OVA,bgm.tv):已入库的带 local_id 直接跳转。"""
    import time as _time
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if not b.bgmtv_subject_id:
        return []
    cached = _related_cache.get(b.bgmtv_subject_id)
    if cached and _time.time() - cached[0] < 6 * 3600:
        rels = cached[1]
    else:
        from app.clients.bgmtv import bgmtv_client
        try:
            rels = [r for r in bgmtv_client.related_subjects(b.bgmtv_subject_id)
                    if r.type == 2]   # 只留动画(书籍/音乐等不展示)
        except Exception as e:  # noqa: BLE001
            log.warning("关联条目获取失败 %s: %s", b.title, e)
            return []
        _related_cache[b.bgmtv_subject_id] = (_time.time(), rels)
    ids = [r.subject_id for r in rels]
    local = {row[1]: row[0] for row in db.execute(
        select(Bangumi.id, Bangumi.bgmtv_subject_id)
        .where(Bangumi.bgmtv_subject_id.in_(ids))).all()} if ids else {}
    return [{"subject_id": r.subject_id, "relation": r.relation,
             "title": r.name_cn or r.name, "image": r.image,
             "local_id": local.get(r.subject_id)} for r in rels]


@router.post("/{bangumi_id}/mark-phase")
def mark_phase(bangumi_id: int, payload: dict, db: Session = Depends(get_db)):
    """整番手动归阶段:把该番剧现有**全部**种子标为先行/正式(自动判定失手时的兜底,
    如上季度先行放送、导入时 air_date 还没同步等)。标先行且官方未开播时,顺带把被
    「下满集数」误判的已完结纠回连载中。"""
    from app.models import AiringStatus
    from app.services.phase import before_official_air
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    phase = payload.get("phase")
    if phase not in ("preview", "official"):
        raise HTTPException(400, "phase 必须是 preview 或 official")
    is_prev = phase == "preview"
    sub_ids = select(Subscription.id).where(Subscription.bangumi_id == b.id).scalar_subquery()
    rows = db.execute(select(Torrent).where(
        Torrent.subscription_id.in_(sub_ids))).scalars().all()
    n = 0
    for t in rows:
        if bool(t.is_preview) != is_prev:
            t.is_preview = is_prev
            n += 1
    fixed_airing = False
    if is_prev and before_official_air(b.air_date) and b.airing_status == AiringStatus.FINISHED:
        b.airing_status = AiringStatus.AIRING   # 先行集齐误判的完结 → 纠回
        fixed_airing = True
    db.commit()
    log.info("整番归阶段:%s → %s(%s 个种子,纠正完结=%s)", b.title, phase, n, fixed_airing)
    return {"ok": True, "updated": n, "fixed_airing": fixed_airing}


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
    if "ep_start" in payload:
        try:
            b.ep_start = max(1, int(payload["ep_start"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "ep_start 非法") from None
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


def _reorganize_bg(torrent_ids: list[int]) -> None:
    """后台:逐个重跑整理(SMB 上串行,单个失败不影响其余)。先行种子会被移入「先行版/」。"""
    from app.database import db_session
    from app.services.organize import organize_torrent
    for tid in torrent_ids:
        try:
            with db_session() as db:
                t = db.get(Torrent, tid)
                if t is not None:
                    organize_torrent(db, t)
        except Exception:  # noqa: BLE001
            log.exception("重整理 #%s 失败", tid)


@router.post("/{bangumi_id}/reorganize")
def reorganize(bangumi_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    """重新整理该番剧已归档种子的文件到统一的 Season 结构(先行→先行版、正片→Season NN)。

    托管种子走下载器改名,本地导入(无 info_hash)走文件系统 move —— 两者都纳入。后台串行执行。
    """
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    tids = list(db.execute(
        select(Torrent.id).join(Subscription, Torrent.subscription_id == Subscription.id)
        .where(Subscription.bangumi_id == bangumi_id,
               Torrent.status == TorrentStatus.ARCHIVED)).scalars().all())
    background.add_task(_reorganize_bg, tids)
    return {"started": True, "torrents": len(tids)}


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


@router.post("/refresh-metadata-all")
def refresh_all_metadata():
    """批量重拉所有番剧元数据/封面(迁移到新机、图片没带过来时一键补齐)。后台执行。"""
    from app.services.metadata_service import start_refresh_all
    if not start_refresh_all():
        raise HTTPException(409, "已有重拉任务在进行中")
    return {"started": True}


@router.get("/refresh-metadata-all/status")
def refresh_all_metadata_status():
    from app.services.metadata_service import refresh_state
    return refresh_state


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
