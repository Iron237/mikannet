"""番剧库扫描:就地识别已整理到 NAS(/downloads)里的视频,登记进库(不移动文件)。

场景:用户的成片已经按 `番剧名/…` 摆在下载根目录(本地导入移动过去的、或历史下载),
但库里没有对应的 VideoFile 记录(DB 被清过)。本扫描遍历 download_root 下每个顶层
文件夹 → 按标题匹配已有 Bangumi(匹配不到则 Mikan 搜索创建)→ 把夹内每个视频就地登记:
解析集数/分辨率/字幕组/片源 + ffprobe + 映射剧集,挂在该番剧的「本地导入」容器订阅下。

增量同步(每次扫描都与磁盘比对,保持库与磁盘一致):
- 新增:磁盘有、库没有 → 登记 + 探测 + 映射。
- 变更:同一路径但文件大小变了(换源/重压)→ 重探测更新。
- 删除:库里(容器登记的)有、磁盘已不在 → 移除记录并重算该集 is_active。
qB 种子下载的文件由后处理管,本扫描只比对「容器」(库扫描/本地导入)登记的文件。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.config import settings
from app.database import db_session
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.parsers.title_parser import detect_source, parse
from app.services import media_probe
from app.services.local_import import LOCAL_SUBGROUP_ID

log = logging.getLogger(__name__)

state = {"running": False, "phase": "", "done": 0, "total": 0,
         "current": "", "registered": 0, "updated": 0, "removed": 0,
         "matched": [], "unmatched": [], "skipped": 0, "error": None}

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_NORM = re.compile(r"[\s·:：!！?？,，.。\-—_~、]+")
# 裸盘结构(蓝光 BDMV/STREAM、DVD VIDEO_TS):整盘,不逐文件当集登记
_RAW_DISC_DIRS = {"BDMV", "STREAM", "VIDEO_TS"}


def _safe(name: str) -> str:
    return _ILLEGAL.sub(" ", name or "").strip()


def _norm(name: str) -> str:
    """归一化标题用于模糊匹配:去空白/标点,小写。"""
    return _NORM.sub("", (name or "").strip()).lower()


def _build_index(db) -> tuple[dict, dict]:
    """已有番剧的标题索引:精确(净化标题)+ 归一化。"""
    bs = db.execute(select(Bangumi)).scalars().all()
    exact, norm = {}, {}
    for b in bs:
        exact.setdefault(_safe(b.title), b)
        norm.setdefault(_norm(b.title), b)
    return exact, norm


def _match_or_create(db, folder: str) -> Bangumi | None:
    """文件夹名 → Bangumi:先精确/归一化匹配已有,匹配不到用 Mikan 搜索创建。"""
    from app.clients.mikan import mikan_client
    from app.services.metadata_service import enrich_bangumi
    from app.services.organize import detect_season

    exact, norm = _build_index(db)
    b = exact.get(_safe(folder)) or norm.get(_norm(folder))
    if b is not None:
        return b
    # 匹配不到 → Mikan 容错搜索创建(让库自动补全这部番剧)
    try:
        hit = mikan_client.search_best(folder)
    except Exception as e:  # noqa: BLE001
        log.warning("库扫描:Mikan 搜索 %s 失败: %s", folder, e)
        return None
    if not hit:
        return None
    mid = hit.mikan_bangumi_id
    b = db.execute(select(Bangumi).where(Bangumi.mikan_bangumi_id == mid)).scalar_one_or_none()
    if b is None:
        b = Bangumi(mikan_bangumi_id=mid, title=hit.title or folder)
        db.add(b)
        db.flush()
        enrich_bangumi(db, b)
        b.season_number = detect_season(b.title)
    return b


def _container_torrent(db, b: Bangumi) -> Torrent:
    """该番剧的「本地导入」容器:停用订阅 + 一个 ARCHIVED 合成 Torrent(挂载就地视频)。复用,不重复建。"""
    sub = db.execute(select(Subscription).where(
        Subscription.bangumi_id == b.id,
        Subscription.mikan_subgroup_id == LOCAL_SUBGROUP_ID)).scalar_one_or_none()
    safe = _safe(b.title) or f"bangumi {b.mikan_bangumi_id}"
    if sub is None:
        sub = Subscription(bangumi_id=b.id, mikan_subgroup_id=LOCAL_SUBGROUP_ID,
                           subgroup_name="本地导入", enabled=False, backfill=False,
                           save_path=f"{settings.download_root}/{safe}")
        db.add(sub)
        db.flush()
    guid = f"library:{b.mikan_bangumi_id}"
    t = db.execute(select(Torrent).where(Torrent.guid == guid)).scalar_one_or_none()
    if t is None:
        t = Torrent(subscription_id=sub.id, guid=guid,
                    title_raw=f"[番剧库] {b.title}", parsed_json={}, torrent_url="",
                    is_batch=True, status=TorrentStatus.ARCHIVED,
                    completed_at=datetime.now(timezone.utc))
        db.add(t)
        db.flush()
    return t


def _map_episode(db, b: Bangumi, t: Torrent, p) -> int | None:
    """解析结果 → episode_id(+ TorrentEpisode)。按 ep_type 归类,非正片不占正片集号。"""
    ep_type = (EpisodeType(p.ep_type)
               if p.ep_type in EpisodeType._value2member_map_ else EpisodeType.REGULAR)
    if ep_type == EpisodeType.REGULAR:
        if len(p.episodes) != 1 or p.episodes[0] <= 0:
            return None       # 解析不出单集 → 留作「其他文件」
        number = p.episodes[0]
    else:
        number = p.episodes[0] if len(p.episodes) == 1 else None
    q = select(Episode).where(Episode.bangumi_id == b.id, Episode.type == ep_type)
    q = q.where(Episode.number == number) if number is not None else q.where(Episode.number.is_(None))
    ep = db.execute(q).scalars().first()
    if ep is None:
        ep = Episode(bangumi_id=b.id, number=number, type=ep_type)
        db.add(ep)
        db.flush()
    if not db.get(TorrentEpisode, (t.id, ep.id)):
        db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
    return ep.id


def _probe_into(vf: VideoFile, abspath: Path, fallback_res: str | None = None) -> None:
    """ffprobe 写入 VideoFile;失败留行可重探,分辨率退化用文件名解析值。"""
    try:
        r = media_probe.probe(abspath)
        vf.resolution = r.resolution or fallback_res
        vf.video_codec = r.video_codec
        vf.color_depth = r.color_depth
        vf.hdr = r.hdr
        vf.bitrate = r.bitrate
        vf.audio_tracks = r.audio_tracks
        vf.subtitle_tracks = r.subtitle_tracks
        vf.probed_at = datetime.now(timezone.utc)
    except Exception as e:  # noqa: BLE001 — SMB 抖动
        vf.resolution = vf.resolution or fallback_res
        log.warning("库扫描 ffprobe 失败 %s: %s", abspath, e)


def _register_file(db, b: Bangumi, t: Torrent, rel: str, abspath: Path,
                   folder_source: str | None) -> str:
    """就地登记/比对一个视频文件。返回 "added" | "updated" | "unchanged"。

    片源:文件名优先,判不出则继承文件夹上下文(夹名含 BDRip → 内部成片标 BD)。
    已登记:仅本容器登记的文件,若大小变了(换源/重压)→ 重探测;qB 种子文件不动。
    """
    existing = db.execute(select(VideoFile).where(
        VideoFile.relative_path == rel)).scalar_one_or_none()
    if existing is not None:
        if existing.torrent_id == t.id:   # 本容器登记过 → 看是否变更
            try:
                cur = abspath.stat().st_size
            except OSError:
                cur = None
            if cur and existing.size and cur != existing.size:
                existing.size = cur
                _probe_into(existing, abspath, parse(abspath.name).resolution)
                db.flush()
                return "updated"
        return "unchanged"
    p = parse(abspath.name)
    vf = VideoFile(torrent_id=t.id, relative_path=rel, subgroup=p.group,
                   source=p.source or folder_source)
    try:
        vf.size = abspath.stat().st_size
    except OSError:
        pass
    db.add(vf)
    db.flush()
    vf.episode_id = _map_episode(db, b, t, p)
    _probe_into(vf, abspath, p.resolution)
    db.flush()
    return "added"


def _reconcile_removed(db) -> int:
    """反向比对:容器(库扫描/本地导入)登记过、但磁盘已不在的文件 → 移除,重算 is_active。

    安全:仅当父目录可访问、唯独该文件不在时才删(整挂载掉线时父目录也不在 → 跳过,不误删)。
    """
    from app.services.postprocess import _apply_version_switch
    root = Path(settings.download_root_local)
    rows = db.execute(select(VideoFile).join(Torrent).where(
        or_(Torrent.guid.like("library:%"), Torrent.guid.like("local:%")))).scalars().all()
    removed = 0
    touched: set[int] = set()
    for vf in rows:
        fp = root / vf.relative_path
        if not fp.exists() and fp.parent.exists():   # 文件没了、但目录还在 → 确属删除
            if vf.episode_id:
                touched.add(vf.episode_id)
            db.delete(vf)
            removed += 1
    if removed:
        db.flush()
        for ep_id in touched:
            _apply_version_switch(db, ep_id)
    return removed


def _run() -> None:
    root = Path(settings.download_root_local)
    state.update(running=True, phase="扫描下载根目录", done=0, total=0, current="",
                 registered=0, updated=0, removed=0, matched=[], unmatched=[],
                 skipped=0, error=None)
    try:
        if not root.is_dir():
            raise FileNotFoundError(f"下载根目录不可访问: {root}")
        folders = sorted([d for d in root.iterdir() if d.is_dir()], key=lambda d: d.name)
        state["total"] = len(folders)
        for idx, folder in enumerate(folders, 1):
            state.update(done=idx, current=folder.name)
            all_files = [p for p in folder.rglob("*") if p.is_file() and media_probe.is_video(p)]
            # 裸盘(BDMV/STREAM、VIDEO_TS):整盘结构,不逐 m2ts 当集登记 → 跳过
            vids, raw = [], 0
            for vp in all_files:
                parts = {seg.upper() for seg in vp.relative_to(folder).parts}
                if parts & _RAW_DISC_DIRS:
                    raw += 1
                else:
                    vids.append(vp)
            state["skipped"] += raw
            if raw:
                log.info("库扫描:%s 内跳过 %s 个裸盘视频(BDMV/VIDEO_TS)", folder.name, raw)
            if not vids:
                continue
            # 文件夹上下文片源:夹名整体看一遍(BDRip 合集夹 → 内部成片继承 BD)
            folder_source = detect_source(folder.name)
            try:
                with db_session() as db:
                    b = _match_or_create(db, folder.name)
                    if b is None:
                        state["unmatched"].append(folder.name)
                        continue
                    t = _container_torrent(db, b)
                    n = upd = 0
                    for vp in vids:
                        rel = str(vp.relative_to(root)).replace("\\", "/")
                        # 逐文件再看其所在子目录(如 .../SPs/、.../BDRip/)叠加上下文片源
                        sub_source = detect_source("/".join(vp.relative_to(folder).parts[:-1]))
                        try:
                            r = _register_file(db, b, t, rel, vp, sub_source or folder_source)
                            if r == "added":
                                n += 1
                            elif r == "updated":
                                upd += 1
                        except Exception as e:  # noqa: BLE001 — 单文件失败不拖垮整夹
                            log.warning("库扫描登记失败 %s: %s", vp, e)
                    state["registered"] += n
                    state["updated"] += upd
                    if n or upd:
                        state["matched"].append(
                            f"{b.title}: +{n}" + (f" ~{upd}" if upd else ""))
            except Exception as e:  # noqa: BLE001
                log.warning("库扫描处理 %s 失败: %s", folder.name, e)
                state["unmatched"].append(f"{folder.name}(出错)")
        # 反向比对:移除磁盘已删的容器文件,保持库与磁盘一致
        state["phase"] = "比对已删文件"
        try:
            with db_session() as db:
                state["removed"] = _reconcile_removed(db)
        except Exception as e:  # noqa: BLE001
            log.warning("库扫描反向比对失败: %s", e)
        log.info("番剧库扫描完成:新增 %s 更新 %s 移除 %s,匹配 %s 部,未匹配 %s",
                 state["registered"], state["updated"], state["removed"],
                 len(state["matched"]), len(state["unmatched"]))
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        log.exception("番剧库扫描失败")
    finally:
        state.update(running=False, phase="完成", current="")


def start() -> None:
    if state["running"]:
        return
    threading.Thread(target=_run, daemon=True).start()
