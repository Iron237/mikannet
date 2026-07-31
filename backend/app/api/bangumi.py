"""番剧库与详情(虚拟库视图,ADR-0001:全部由数据库渲染)。"""
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (AutoScanLog, Bangumi, BdRelease, Episode, EpisodeType, Kind,
                        Subscription, Torrent, TorrentEpisode, TorrentStatus, VideoFile)
from app.services.local_import import LOCAL_SUBGROUP_ID

router = APIRouter(prefix="/api/bangumi", tags=["bangumi"])
log = logging.getLogger(__name__)
_FILE_EXIST_CACHE: dict[str, tuple[float, bool]] = {}
_FILE_EXIST_TTL = 3.0


def _file_exists(relative_path: str) -> bool:
    path = settings.download_root_local / relative_path
    key = str(path)
    now = time.monotonic()
    cached = _FILE_EXIST_CACHE.get(key)
    if cached and now - cached[0] < _FILE_EXIST_TTL:
        return cached[1]
    try:
        exists = path.is_file()
    except OSError:
        exists = False
    if len(_FILE_EXIST_CACHE) > 10000:
        _FILE_EXIST_CACHE.clear()
    _FILE_EXIST_CACHE[key] = (now, exists)
    return exists


def _poster_url(b: Bangumi) -> str | None:
    return f"/data/{b.poster_path}" if b.poster_path else None


def _file_out(f) -> dict:
    """视频文件展示信息:分辨率/字幕组/片源/编码/色深/HDR/码率 + 音轨(含声道)/字幕轨(含外挂)
    + 原生启动 URL(本机默认播放器播放 / 资源管理器定位;未配置宿主前缀则为 None)。"""
    from pathlib import PurePosixPath

    from app.services import launch
    exists = _file_exists(f.relative_path)
    return {
        "id": f.id, "path": f.relative_path, "name": PurePosixPath(f.relative_path).name,
        "original_name": f.original_name,   # 整理改名前的原始文件名(保留字幕组/版本等信息)
        "size": f.size, "resolution": f.resolution, "subgroup": f.subgroup,
        "source": f.source, "codec": f.video_codec,
        "color_depth": f.color_depth, "hdr": f.hdr, "bitrate": f.bitrate,
        "audio_tracks": f.audio_tracks, "subtitle_tracks": f.subtitle_tracks,
        "preferred": bool(f.is_preferred),
        "exists": exists,
        "play_url": launch.media_launch("play", f.relative_path) if exists else None,
        "reveal_url": launch.media_launch("reveal", f.relative_path) if exists else None,
        "stream_url": f"/api/files/{f.id}/stream" if exists else None,
        "compatible_stream_url": f"/api/files/{f.id}/stream?compatible=true" if exists else None,
    }


@router.get("")
def library(db: Session = Depends(get_db), verify_files: bool = True):
    """番剧库封面墙。

    列表所需的文件、剧集、种子与 BD 原盘统计一次性批量读取，避免每部番剧重复发起
    8 次左右 SQL 查询。verify_files=False 供前端首屏快速展示数据库快照；首屏完成后
    再异步做实盘核对，详情页和播放入口仍始终验证真实文件。
    """
    rows = db.execute(select(Bangumi).order_by(Bangumi.created_at.desc())).scalars().all()

    file_rows = db.execute(
        select(
            Subscription.bangumi_id.label("bangumi_id"),
            Episode.id.label("episode_id"),
            Episode.number.label("episode_number"),
            Episode.type.label("episode_type"),
            VideoFile.source.label("source"),
            VideoFile.is_active.label("is_active"),
            VideoFile.relative_path.label("relative_path"),
            Torrent.is_preview.label("is_preview"),
        )
        .select_from(VideoFile)
        .join(Torrent, VideoFile.torrent_id == Torrent.id)
        .join(Subscription, Torrent.subscription_id == Subscription.id)
        .outerjoin(Episode, VideoFile.episode_id == Episode.id)
    ).all()
    files_by_bangumi: dict[int, list[dict]] = defaultdict(list)
    exists_by_path: dict[str, bool] = {}
    for row in file_rows:
        path = row.relative_path
        if path not in exists_by_path:
            exists_by_path[path] = _file_exists(path) if verify_files else True
        files_by_bangumi[row.bangumi_id].append({
            "episode_id": row.episode_id,
            "episode_number": row.episode_number,
            "episode_type": row.episode_type,
            "source": row.source or "未知",
            "is_active": bool(row.is_active),
            "exists": exists_by_path[path],
            "is_preview": bool(row.is_preview),
        })

    torrent_rows = db.execute(
        select(Subscription.bangumi_id, Torrent.parsed_json,
               Subscription.episode_offset)
        .join(Torrent, Torrent.subscription_id == Subscription.id)
        .where(Torrent.is_preview.is_(False))
    ).all()
    torrents_by_bangumi: dict[int, list[tuple[dict, int]]] = defaultdict(list)
    for bangumi_id, parsed_json, episode_offset in torrent_rows:
        torrents_by_bangumi[bangumi_id].append(
            (parsed_json or {}, episode_offset or 0))

    release_counts = dict(db.execute(
        select(BdRelease.bangumi_id, func.count(BdRelease.id))
        .where(BdRelease.bangumi_id.is_not(None))
        .group_by(BdRelease.bangumi_id)
    ).all())

    out = []
    for b in rows:
        files = files_by_bangumi.get(b.id, [])
        active_files = [f for f in files if f["is_active"] and f["exists"]]
        regular_files = [
            f for f in files
            if f["episode_type"] == EpisodeType.REGULAR
            and f["episode_number"] is not None
        ]
        official_regular = [f for f in regular_files if not f["is_preview"]]

        source_rank = {"BD": 0, "Web": 1}
        active_source: dict[float, str] = {}
        for f in official_regular:
            if not f["is_active"] or not f["exists"]:
                continue
            number = float(f["episode_number"])
            source = f["source"]
            if (number not in active_source
                    or source_rank.get(source, 9)
                    < source_rank.get(active_source[number], 9)):
                active_source[number] = source

        expected = (
            [float(n) for n in range(b.ep_start or 1,
                                     (b.ep_start or 1) + b.eps_total)]
            if b.kind == Kind.TV and b.eps_total else []
        )
        bd_numbers = [n for n, source in active_source.items() if source == "BD"]
        web_numbers = [n for n, source in active_source.items() if source == "Web"]
        release_count = int(release_counts.get(b.id, 0))
        bd_active_files = sum(1 for f in active_files if f["source"] == "BD")
        if b.kind == Kind.TV:
            if not bd_numbers:
                bd_status = "release_only" if release_count else "none"
            elif expected and all(active_source.get(n) == "BD" for n in expected):
                bd_status = "complete"
            else:
                bd_status = "partial"
        elif not bd_active_files:
            bd_status = "release_only" if release_count else "none"
        else:
            bd_status = "active"

        downloaded_ids = {
            f["episode_id"] for f in regular_files
            if f["episode_id"] is not None and f["is_active"] and f["exists"]
        }
        eps_downloaded = len(downloaded_ids)
        if b.eps_total:
            eps_downloaded = min(eps_downloaded, b.eps_total)

        eps_aired = None
        if b.kind == Kind.TV and b.eps_total:
            start = b.ep_start or 1
            seen = 0
            for parsed_json, episode_offset in torrents_by_bangumi.get(b.id, []):
                for episode in parsed_json.get("episodes") or []:
                    try:
                        seen = max(
                            seen,
                            int(float(episode)) - episode_offset - (start - 1))
                    except (TypeError, ValueError):
                        continue
            if seen == 0:
                seen = _weekly_aired(b.air_date)
            official_downloaded = len({
                f["episode_id"] for f in official_regular
                if f["episode_id"] is not None and f["is_active"] and f["exists"]
            })
            seen = max(seen, official_downloaded)
            eps_aired = min(seen, b.eps_total) if seen > 0 else None

        out.append({
            "id": b.id, "title": b.title, "year": b.year, "season": b.season_str,
            "studio": b.studio, "score": b.score, "airing_status": b.airing_status.value,
            "kind": b.kind.value, "auto_best": b.auto_best, "auto_mode": b.auto_mode,
            "auto_download_disabled": b.auto_download_disabled,
            "has_mikan": b.mikan_bangumi_id is not None,
            "poster": _poster_url(b),
            "backdrop": f"/data/{b.backdrop_path}" if b.backdrop_path else None,
            "eps_total": b.eps_total,
            # 影片/OVA 没有"正片集"概念,用是否有入库文件表达"已入库"。
            "has_resource": bool(active_files),
            "eps_downloaded": eps_downloaded,
            "eps_aired": eps_aired,
            "has_bd": bd_status != "none",
            "has_web": any(f["source"] == "Web" for f in active_files),
            "bd_owned": b.bd_owned,
            # 兼容旧前端;新前端使用 bd_status + 精确覆盖数。
            "bd_rip": bool(bd_numbers or bd_active_files),
            "bd_status": bd_status,
            "bd_active_eps": len(bd_numbers),
            "web_active_eps": len(web_numbers),
            "bd_release_count": release_count,
            "files_verified": verify_files,
        })
    return out


def _resource_coverage(db: Session, b: Bangumi, include_cleanup: bool = False,
                       verify_files: bool = False) -> dict:
    """正式正片的资源覆盖矩阵。

    active 表示当前实际播放版本;inactive 作为可恢复的备用版本保留。BD 发行记录与
    可播放 BD 文件分开表达,避免“扫到发行”被误写成“全番已替换”。
    """
    rows = db.execute(
        select(Episode.number, VideoFile.id, VideoFile.source, VideoFile.is_active,
               VideoFile.is_preferred, VideoFile.relative_path, VideoFile.size)
        .join(VideoFile, VideoFile.episode_id == Episode.id)
        .join(Torrent, VideoFile.torrent_id == Torrent.id)
        .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
               Episode.number.is_not(None), Torrent.is_preview.is_(False))).all()
    rank = {"BD": 0, "Web": 1}
    active: dict[float, str] = {}
    fallback: dict[float, set[str]] = {}
    versions: dict[float, list[dict]] = {}
    for number, file_id, source, is_active, is_preferred, relative_path, size in rows:
        num = float(number)
        src = source or "未知"
        exists = _file_exists(relative_path) if verify_files else True
        versions.setdefault(num, []).append({
            "id": file_id, "source": src, "active": bool(is_active and exists),
            "recorded_active": bool(is_active), "exists": exists,
            "preferred": bool(is_preferred),
            "name": relative_path.rsplit("/", 1)[-1], "size": size,
        })
        if is_active and exists:
            if num not in active or rank.get(src, 9) < rank.get(active[num], 9):
                active[num] = src
        elif exists:
            fallback.setdefault(num, set()).add(src)

    start = b.ep_start or 1
    expected = ([float(n) for n in range(start, start + b.eps_total)]
                if b.kind == Kind.TV and b.eps_total else [])
    missing = [n for n in expected if n not in active]
    bd = sorted(n for n, src in active.items() if src == "BD")
    web = sorted(n for n, src in active.items() if src == "Web")
    unknown = sorted(n for n, src in active.items() if src not in ("BD", "Web"))
    active_file_sources = db.execute(
        select(VideoFile.source, VideoFile.relative_path).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == b.id, VideoFile.is_active.is_(True),
               Torrent.is_preview.is_(False))).all()
    bd_active_files = sum(
        1 for src, path in active_file_sources
        if src == "BD" and (not verify_files or _file_exists(path)))
    release_count = db.execute(select(BdRelease.id).where(
        BdRelease.bangumi_id == b.id)).scalars().all()
    if b.kind == Kind.TV:
        if not bd:
            bd_status = "release_only" if release_count else "none"
        elif expected and all(active.get(n) == "BD" for n in expected):
            bd_status = "complete"
        else:
            bd_status = "partial"
    elif not bd_active_files:
        bd_status = "release_only" if release_count else "none"
    else:
        bd_status = "active"

    episodes = []
    numbers = expected or sorted(set(active) | set(fallback))
    for n in numbers:
        episodes.append({
            "number": int(n) if float(n).is_integer() else n,
            "active_source": active.get(n),
            "fallback_sources": sorted(fallback.get(n, set())),
            "versions": sorted(versions.get(n, []),
                               key=lambda v: (not v["active"], v["source"], v["id"])),
        })

    # 合集里同时存在被替换 Web 和仍生效文件时不能安全整包清理,显式告诉用户原因。
    cleanup_blocked = 0
    if include_cleanup:
        torrents = db.execute(select(Torrent).join(Subscription).where(
            Subscription.bangumi_id == b.id)).scalars().all()
        for torrent in torrents:
            files = list(torrent.files)
            if (any(f.source == "Web" and not f.is_active for f in files)
                    and any(f.is_active for f in files)):
                cleanup_blocked += 1

    return {
        "total": len(expected) or None,
        "bd": [int(n) if n.is_integer() else n for n in bd],
        "web": [int(n) if n.is_integer() else n for n in web],
        "unknown": [int(n) if n.is_integer() else n for n in unknown],
        "missing": [int(n) if n.is_integer() else n for n in missing],
        "fallback_count": len(fallback),
        "episodes": episodes,
        "bd_status": bd_status,
        "bd_active_files": bd_active_files,
        "bd_release_count": len(release_count),
        "cleanup_blocked_torrents": cleanup_blocked,
    }


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


def _eps_done(db: Session, b: Bangumi, official_only: bool = False,
              verify_files: bool = False) -> int:
    """已入库集数:只数有 active 文件的**正片**集(不含 SP/菜单/NC/特典),并封顶到总集数。

    封顶规避:跨季连续编号(S2 编 13-24)/ 错误元数据 / 旧整理残留的幽灵集 等导致的「超过总集数」。
    official_only:只数正式流(先行放送内容不算,「已播」推断用)。
    """
    q = (select(Episode.id, VideoFile.relative_path)
         .join(VideoFile, VideoFile.episode_id == Episode.id)
         .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
                VideoFile.is_active.is_(True)))
    if official_only:
        q = q.join(Torrent, VideoFile.torrent_id == Torrent.id).where(
            Torrent.is_preview.is_(False))
    rows = db.execute(q.distinct()).all()
    n = len({episode_id for episode_id, path in rows
             if not verify_files or _file_exists(path)})
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


def _eps_aired(db: Session, b: Bangumi, verify_files: bool = False) -> int | None:
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
    seen = max(seen, _eps_done(db, b, official_only=True, verify_files=verify_files))
    return min(seen, b.eps_total) if seen > 0 else None


def _has_source(db: Session, bangumi_id: int, source: str) -> bool:
    """该番剧是否有指定片源的 active 文件;BD 还认 BD 发行记录(BD 收藏扫描登记的)。"""
    if source == "BD" and db.execute(select(BdRelease.id).where(
            BdRelease.bangumi_id == bangumi_id).limit(1)).first():
        return True
    return _has_active_file_source(db, bangumi_id, source)


def _has_any_active_file(db: Session, bangumi_id: int,
                         verify_files: bool = False) -> bool:
    rows = db.execute(
        select(VideoFile.relative_path).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == bangumi_id,
               VideoFile.is_active.is_(True))).scalars().all()
    return any(not verify_files or _file_exists(path) for path in rows)


def _has_active_file_source(db: Session, bangumi_id: int, source: str,
                            verify_files: bool = False) -> bool:
    """该番剧是否有指定片源的 active 视频文件(不认 BD 发行记录,用于「BDrip 已替换正片」角标)。"""
    rows = db.execute(
        select(VideoFile.relative_path).join(Torrent).join(Subscription)
        .where(Subscription.bangumi_id == bangumi_id, VideoFile.is_active.is_(True),
               VideoFile.source == source)).scalars().all()
    return any(not verify_files or _file_exists(path) for path in rows)


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


@router.get("/resource-issues")
def resource_issues(verify_files: bool = True, db: Session = Depends(get_db)):
    """全局消息中心：按需核对资源覆盖、订阅健康、失败任务与物理文件漂移。"""
    from datetime import datetime, timezone

    groups = [
        {"key": "missing_files", "label": "文件记录失效", "severity": "error", "items": []},
        {"key": "failed_tasks", "label": "下载任务失败", "severity": "error", "items": []},
        {"key": "missing_episodes", "label": "已播但缺集", "severity": "warning", "items": []},
        {"key": "subscription_errors", "label": "订阅源异常", "severity": "warning", "items": []},
        {"key": "bd_release_only", "label": "BD原盘尚未覆盖正片", "severity": "warning", "items": []},
        {"key": "cleanup_blocked", "label": "合集阻止清理", "severity": "info", "items": []},
        {"key": "auto_never_scanned", "label": "自动策略尚未运行", "severity": "info", "items": []},
        {"key": "unbound_bd", "label": "BD原盘未绑定", "severity": "warning", "items": []},
    ]
    by_key = {g["key"]: g for g in groups}

    def add(key: str, b: Bangumi | None, detail: str, **extra) -> None:
        by_key[key]["items"].append({
            "bangumi_id": b.id if b else None,
            "title": b.title if b else extra.pop("title", "未绑定原盘"),
            "path": f"/bangumi/{b.id}" if b else "/bd",
            "detail": detail,
            **extra,
        })

    rows = db.execute(select(Bangumi).order_by(Bangumi.updated_at.desc())).scalars().all()
    failed_statuses = (TorrentStatus.SUBMIT_FAILED, TorrentStatus.DOWNLOAD_ERROR)
    for b in rows:
        coverage = _resource_coverage(
            db, b, include_cleanup=True, verify_files=verify_files)
        aired = _eps_aired(db, b, verify_files=verify_files)
        if aired and coverage["missing"]:
            start = b.ep_start or 1
            aired_numbers = set(range(start, start + aired))
            due = [n for n in coverage["missing"] if n in aired_numbers]
            if due:
                add("missing_episodes", b, f"缺第 {', '.join(map(str, due[:12]))} 话",
                    count=len(due), episodes=due)

        failed = db.execute(
            select(Torrent).join(Subscription)
            .where(Subscription.bangumi_id == b.id,
                   Torrent.status.in_(failed_statuses))).scalars().all()
        if failed:
            add("failed_tasks", b, f"{len(failed)} 个任务提交或下载失败",
                count=len(failed))

        bad_subs = db.execute(
            select(Subscription).where(
                Subscription.bangumi_id == b.id,
                Subscription.enabled.is_(True),
                Subscription.mikan_subgroup_id.notin_(("local", "auto")),
                Subscription.last_poll_ok.is_(False))).scalars().all()
        if bad_subs:
            names = [s.subgroup_name or s.mikan_subgroup_id for s in bad_subs]
            add("subscription_errors", b, f"{len(bad_subs)} 个订阅源检查失败",
                count=len(bad_subs), sources=names)

        if coverage["bd_status"] == "release_only":
            add("bd_release_only", b,
                f"检测到 {coverage['bd_release_count']} 套 BD 原盘，但没有生效 BD 正片",
                count=coverage["bd_release_count"])
        if coverage["cleanup_blocked_torrents"]:
            add("cleanup_blocked", b,
                f"{coverage['cleanup_blocked_torrents']} 个合集同时含生效与备用文件",
                count=coverage["cleanup_blocked_torrents"])
        if b.auto_best and not b.auto_scan_at and not b.auto_download_disabled:
            add("auto_never_scanned", b, "常驻策略已开启，但还没有扫描记录")

        active_files = db.execute(
            select(VideoFile).join(Torrent).join(Subscription)
            .where(Subscription.bangumi_id == b.id,
                   VideoFile.is_active.is_(True))).scalars().all()
        missing_paths: list[str] = []
        if verify_files:
            for vf in active_files:
                try:
                    exists = _file_exists(vf.relative_path)
                except OSError:
                    exists = False
                if not exists:
                    missing_paths.append(vf.relative_path)
        archived_without_file = db.execute(
            select(Torrent).join(Subscription)
            .where(Subscription.bangumi_id == b.id,
                   Torrent.status == TorrentStatus.ARCHIVED)).scalars().all()
        stale_archives = []
        for torrent in archived_without_file:
            if torrent.files or not torrent.episodes:
                continue
            # 已清理的旧版本任务可以没有自己的文件；只有其映射集也没有其它实盘版本时，
            # 才会造成“任务已入库、详情却无文件”的真实漂移。
            uncovered = False
            for episode in torrent.episodes:
                alternatives = db.execute(
                    select(VideoFile.relative_path).where(
                        VideoFile.episode_id == episode.id,
                        VideoFile.is_active.is_(True))).scalars().all()
                if not any(_file_exists(path) for path in alternatives):
                    uncovered = True
                    break
            if uncovered:
                stale_archives.append(torrent)
        stale_count = len(missing_paths) + len(stale_archives)
        if stale_count:
            detail = (f"{len(missing_paths)} 个生效文件在磁盘上不存在"
                      if missing_paths else f"{len(stale_archives)} 个已入库任务没有生效文件")
            add("missing_files", b, detail, count=stale_count,
                sample_paths=missing_paths[:3])

    for release in db.execute(select(BdRelease).where(
            BdRelease.bangumi_id.is_(None))).scalars().all():
        add("unbound_bd", None, "扫描到 BD 原盘，但尚未关联番剧",
            release_id=release.id, title=release.title)

    groups = [g for g in groups if g["items"]]
    summary = {
        "total": sum(len(g["items"]) for g in groups),
        "error": sum(len(g["items"]) for g in groups if g["severity"] == "error"),
        "warning": sum(len(g["items"]) for g in groups if g["severity"] == "warning"),
        "info": sum(len(g["items"]) for g in groups if g["severity"] == "info"),
    }
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "verified_files": verify_files, "summary": summary, "groups": groups}


@router.post("/refresh-metadata-all")
def refresh_all_metadata():
    """批量重拉所有番剧元数据/封面。静态路由须声明在 /{bangumi_id} 之前。"""
    from app.services.metadata_service import start_refresh_all
    if not start_refresh_all():
        raise HTTPException(409, "已有重拉任务在进行中")
    return {"started": True}


@router.post("/auto-scan")
def auto_scan(payload: dict, db: Session = Depends(get_db)):
    """批量智能扫描。payload: {ids:[...], mode, enable_auto?:bool}。
    mode 可选补全升级、仅补缺、只建议；enable_auto=True 时保存为常驻策略。"""
    from app.services import auto_best
    ids = [int(i) for i in (payload.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "没有选择番剧")
    mode = str(payload.get("mode") or "fill_upgrade").strip().lower()
    if mode not in auto_best.AUTO_MODES:
        raise HTTPException(400, "mode 非法(fill_upgrade/fill_only/review)")
    if payload.get("enable_auto"):
        for bid in ids:
            b = db.get(Bangumi, bid)
            if b:
                b.auto_best = True
                b.auto_mode = mode
        db.commit()
    if not auto_best.start_scan(ids, mode=mode, trigger="manual"):
        raise HTTPException(409, "已有智能扫描在进行中")
    return {"started": True, "total": len(ids), "mode": mode}


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
            "eps_downloaded": _eps_done(db, b, verify_files=True),
            "eps_aired": _eps_aired(db, b, verify_files=True),
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
        ep_files = [f for f in ep_files if _file_exists(f.relative_path)]
        if current is None and not ep_files:
            continue   # 这一集在当前阶段无任何内容 → 不列(正式阶段下面按总集数补缺占位)
        # 历史任务可能仍是 ARCHIVED,但文件记录已被删除/迁移扫描清掉。详情状态必须以实际
        # active 文件为准,否则会显示「已入库」却没有播放/打开目录按钮。
        archived_without_file = (
            current is not None
            and current.status == TorrentStatus.ARCHIVED
            and not ep_files
        )
        eps_out.append({
            "id": ep.id, "number": ep.number, "type": ep.type.value, "title": ep.title,
            "air_date": ep.air_date,   # 每集精确放送日(bgm.tv 章节同步)
            "status": "missing" if archived_without_file
                      else (current.status.value if current else
                            ("archived" if ep_files else "missing")),
            "version": None if archived_without_file else (current.version if current else None),
            "torrent_id": None if archived_without_file else (current.id if current else None),
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
        "auto_download_disabled": b.auto_download_disabled,
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
    for scan_log in db.execute(select(AutoScanLog).where(
            AutoScanLog.bangumi_id == b.id)).scalars():
        db.delete(scan_log)
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
    """自动补全与升级状态:开关、审计、内部任务、缺集与在途。"""
    from app.services import auto_best
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    # auto 容器订阅的种子按状态分布
    counts: dict[str, int] = {}
    last_activity = None
    auto_sub_id = db.execute(select(Subscription.id).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id == auto_best.AUTO_SUBGROUP_ID)).scalar_one_or_none()
    in_flight_eps: set[float] = set()
    if auto_sub_id:
        for t in db.execute(select(Torrent).where(
                Torrent.subscription_id == auto_sub_id)).scalars():
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
            if t.created_at and (last_activity is None or t.created_at > last_activity):
                last_activity = t.created_at
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
    next_run = None
    try:
        from app import scheduler as scheduler_module
        job = scheduler_module.scheduler.get_job("auto_best")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    except Exception:  # noqa: BLE001 — 测试环境/调度器尚未启动
        pass
    latest_review = db.execute(
        select(AutoScanLog).where(
            AutoScanLog.bangumi_id == b.id,
            AutoScanLog.mode == "review",
            AutoScanLog.approved_at.is_(None))
        .order_by(AutoScanLog.created_at.desc()).limit(1)).scalar_one_or_none()
    pending_review = len((latest_review.result_json or {}).get("proposals") or []) if latest_review else 0
    return {
        "enabled": bool(b.auto_best),
        "mode": auto_best.normalize_mode(b.auto_mode),
        "blocked": bool(b.auto_download_disabled),
        "scanning": scanning,
        "last_scan_at": b.auto_scan_at.isoformat() + "Z" if b.auto_scan_at else None,
        "last_activity_at": last_activity.isoformat() + "Z" if last_activity else None,
        "next_run_at": next_run,
        "last_result": b.auto_scan_result,
        "pending_review": pending_review,
        "pending_review_log_id": latest_review.id if pending_review else None,
        "torrents": counts,
        "missing": missing,
        "in_flight": sorted(int(n) if float(n).is_integer() else n
                            for n in in_flight_eps if float(n) in {float(m) for m in missing}),
    }


@router.get("/{bangumi_id}/auto-history")
def auto_history(bangumi_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """最近智能扫描审计；候选下载 URL 只保存在库内，不回传前端。"""
    if not db.get(Bangumi, bangumi_id):
        raise HTTPException(404)
    rows = db.execute(
        select(AutoScanLog).where(AutoScanLog.bangumi_id == bangumi_id)
        .order_by(AutoScanLog.created_at.desc())
        .limit(max(1, min(limit, 100)))).scalars().all()
    out = []
    for row in rows:
        result = dict(row.result_json or {})
        proposals = result.pop("proposals", [])
        out.append({
            "id": row.id, "mode": row.mode, "trigger": row.trigger,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "approved_at": row.approved_at.isoformat() + "Z" if row.approved_at else None,
            "pending": len(proposals) if not row.approved_at else 0,
            "result": result,
        })
    return out


@router.post("/{bangumi_id}/auto-history/{log_id}/approve")
def approve_auto_review(bangumi_id: int, log_id: int, db: Session = Depends(get_db)):
    """批准 review 模式保存的精确候选；幂等去重仍由 auto 下载容器负责。"""
    from datetime import datetime, timezone
    from app.services import auto_best

    b = db.get(Bangumi, bangumi_id)
    row = db.get(AutoScanLog, log_id)
    if not b or not row or row.bangumi_id != bangumi_id:
        raise HTTPException(404)
    if b.auto_download_disabled:
        raise HTTPException(409, "该番剧已设置停止自动获取")
    result = dict(row.result_json or {})
    proposals = result.get("proposals") or []
    if row.mode != "review" or not proposals:
        raise HTTPException(409, "这条扫描记录没有待确认候选")
    if row.approved_at:
        raise HTTPException(409, "这条扫描记录已经处理")
    sub = auto_best._auto_sub(db, b)
    submitted = sum(1 for candidate in proposals
                    if auto_best._submit_candidate(db, sub, candidate))
    row.approved_at = datetime.now(timezone.utc)
    result.update(submitted=submitted, pending=0,
                  note=f"已确认并提交 {submitted} 个种子")
    result.pop("proposals", None)
    row.result_json = result
    b.auto_scan_result = {k: v for k, v in result.items() if k != "proposals"}
    db.commit()
    return {"ok": True, "submitted": submitted, "log_id": row.id}


@router.get("/{bangumi_id}/resource-strategy")
def resource_strategy(bangumi_id: int, db: Session = Depends(get_db)):
    """P1 统一资源策略:覆盖矩阵 + 自动策略 + 真实订阅源。

    前端以此为唯一状态入口,不再分别猜 BD 发行、active 文件、内部 auto 容器的含义。
    """
    from app.services import auto_best
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    real_subs = db.execute(select(Subscription).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id.notin_(("local", "auto")))).scalars().all()
    return {
        "bangumi_id": b.id,
        "coverage": _resource_coverage(
            db, b, include_cleanup=True, verify_files=True),
        "subscriptions": [_sub_out(s) for s in real_subs],
        "auto": auto_status(b.id, db),
        "policy": {
            "owned": bool(b.bd_owned),
            "stop_automatic": bool(b.auto_download_disabled),
            "resolution": settings.auto_dl_resolution,
            "subtitle_language": settings.auto_dl_sub_lang,
            "upgrade_to_bd": bool(settings.auto_dl_prefer_bd),
            "auto_mode": auto_best.normalize_mode(b.auto_mode),
            "interval_minutes": settings.auto_dl_interval_min,
        },
    }


@router.post("/{bangumi_id}/sync-resources")
def sync_resources(bangumi_id: int, db: Session = Depends(get_db)):
    """统一“立即同步”:检查本番真实订阅,并启动一次跨字幕组补缺/升 BD。

    已停止自动获取时不偷偷绕过策略;本地/BD 文件扫描仍由对应显式按钮触发。
    """
    from app.services import auto_best
    from app.services.rss_engine import safe_poll
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if b.auto_download_disabled:
        raise HTTPException(409, "该番剧已设置停止自动获取")
    subs = db.execute(select(Subscription).where(
        Subscription.bangumi_id == b.id,
        Subscription.enabled.is_(True),
        Subscription.mikan_subgroup_id.notin_(("local", "auto")))).scalars().all()
    rss_results = [safe_poll(db, sub) for sub in subs]
    db.commit()
    auto_started = False
    auto_note = None
    if b.mikan_bangumi_id:
        auto_started = auto_best.start_scan([b.id], mode=None, trigger="sync")
        if not auto_started:
            auto_note = "已有自动补全扫描在进行中"
    else:
        auto_note = "无蜜柑 ID,仅检查本地订阅"
    return {
        "ok": True,
        "rss_checked": len(subs),
        "rss_results": rss_results,
        "auto_started": auto_started,
        "auto_note": auto_note,
    }


_series_cache: dict[int, tuple[float, list]] = {}   # subject_id → (ts, 系列链),6h TTL


@router.get("/{bangumi_id}/series")
def series(bangumi_id: int, db: Session = Depends(get_db)):
    """系列导航条:沿 前传/续集/番外篇/主线故事/衍生 BFS 出整个系列,按放送日期排序。

    只有一部(无系列)返回 []。每项带短标签(去系列公共前缀)、local_id、current。"""
    import time as _time
    from app.services.bgm_sync import build_series, series_labels
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if not b.bgmtv_subject_id:
        return []
    cached = _series_cache.get(b.bgmtv_subject_id)
    if cached and _time.time() - cached[0] < 6 * 3600:
        chain = cached[1]
    else:
        chain = build_series(b.bgmtv_subject_id)
        _series_cache[b.bgmtv_subject_id] = (_time.time(), chain)
        for x in chain:   # 同系列各部共享同一条链(从任意一部打开都免重建)
            _series_cache.setdefault(x["subject_id"], (_time.time(), chain))
    if len(chain) < 2:
        return []
    ids = [x["subject_id"] for x in chain]
    local = {row[1]: row[0] for row in db.execute(
        select(Bangumi.id, Bangumi.bgmtv_subject_id)
        .where(Bangumi.bgmtv_subject_id.in_(ids))).all()}
    labels = series_labels([x["title"] for x in chain])
    return [{**x, "label": labels[i], "local_id": local.get(x["subject_id"]),
             "current": x["subject_id"] == b.bgmtv_subject_id}
            for i, x in enumerate(chain)]


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
    if "auto_mode" in payload:
        from app.services.auto_best import AUTO_MODES
        mode = str(payload["auto_mode"]).strip().lower()
        if mode not in AUTO_MODES:
            raise HTTPException(400, "auto_mode 非法(fill_upgrade/fill_only/review)")
        b.auto_mode = mode
    if "bd_owned" in payload:
        b.bd_owned = bool(payload["bd_owned"])
    if "auto_download_disabled" in payload:
        b.auto_download_disabled = bool(payload["auto_download_disabled"])
    db.commit()
    return {"ok": True, "season_number": b.season_number, "kind": b.kind.value,
            "auto_best": b.auto_best, "auto_mode": b.auto_mode, "bd_owned": b.bd_owned,
            "auto_download_disabled": b.auto_download_disabled}


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


@router.get("/refresh-metadata-all/status")
def refresh_all_metadata_status():
    from app.services.metadata_service import refresh_state
    return refresh_state


# ---- 智能下载(扫所有字幕组挑最佳源:BD>Web、严格分辨率/简中)--------------------

@router.get("/auto-scan/status")
def auto_scan_status():
    from app.services.auto_best import state
    return state


@router.post("/{bangumi_id}/auto-scan")
def auto_scan_one(bangumi_id: int, payload: dict | None = None,
                  db: Session = Depends(get_db)):
    """单部立即智能扫描；默认使用该番剧保存的常驻模式。"""
    from app.services import auto_best
    b = db.get(Bangumi, bangumi_id)
    if not b:
        raise HTTPException(404)
    if not b.mikan_bangumi_id:
        raise HTTPException(400, "该番剧无蜜柑 ID(本地导入),无法扫描线上源")
    mode = str((payload or {}).get("mode") or b.auto_mode or "fill_upgrade").strip().lower()
    if mode not in auto_best.AUTO_MODES:
        raise HTTPException(400, "mode 非法(fill_upgrade/fill_only/review)")
    if not auto_best.start_scan([bangumi_id], mode=mode, trigger="manual"):
        raise HTTPException(409, "已有智能扫描在进行中")
    return {"started": True, "mode": mode}
