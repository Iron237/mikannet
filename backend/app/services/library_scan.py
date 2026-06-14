"""番剧库扫描:就地识别已整理到 NAS(/downloads)里的视频,登记进库(不移动文件)。

场景:用户的成片已经按 `番剧名/…` 摆在下载根目录(本地导入移动过去的、或历史下载),
但库里没有对应的 VideoFile 记录(DB 被清过)。本扫描遍历 download_root 下每个顶层
文件夹 → 按标题匹配已有 Bangumi(匹配不到则 Mikan 搜索创建)→ 把夹内每个视频就地登记:
解析集数/分辨率/字幕组/片源 + ffprobe + 映射剧集,挂在该番剧的「本地导入」容器订阅下。
幂等:已登记(按 relative_path)的文件跳过。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.parsers.title_parser import detect_source, parse
from app.services import media_probe
from app.services.local_import import LOCAL_SUBGROUP_ID

log = logging.getLogger(__name__)

state = {"running": False, "phase": "", "done": 0, "total": 0,
         "current": "", "registered": 0, "matched": [], "unmatched": [], "skipped": 0,
         "error": None}

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


def _register_file(db, b: Bangumi, t: Torrent, rel: str, abspath: Path,
                   folder_source: str | None) -> bool:
    """就地登记一个视频文件为 VideoFile(+ 剧集映射 + ffprobe)。已登记返回 False。

    片源:文件名优先,判不出则继承文件夹上下文(夹名含 BDRip → 内部成片标 BD)。
    """
    exists = db.execute(select(VideoFile.id).where(
        VideoFile.relative_path == rel)).first()
    if exists:
        return False
    p = parse(abspath.name)
    source = p.source or folder_source
    vf = VideoFile(torrent_id=t.id, relative_path=rel, subgroup=p.group, source=source)
    try:
        vf.size = abspath.stat().st_size
    except OSError:
        pass
    db.add(vf)
    db.flush()
    vf.episode_id = _map_episode(db, b, t, p)
    try:
        r = media_probe.probe(abspath)
        vf.resolution = r.resolution
        vf.video_codec = r.video_codec
        vf.color_depth = r.color_depth
        vf.hdr = r.hdr
        vf.bitrate = r.bitrate
        vf.audio_tracks = r.audio_tracks
        vf.subtitle_tracks = r.subtitle_tracks
        if not vf.resolution and p.resolution:
            vf.resolution = p.resolution
        vf.probed_at = datetime.now(timezone.utc)
    except Exception as e:  # noqa: BLE001 — SMB 抖动:留行可重探,分辨率退化用文件名解析
        vf.resolution = vf.resolution or p.resolution
        log.warning("库扫描 ffprobe 失败 %s: %s", abspath, e)
    db.flush()
    return True


def _run() -> None:
    root = Path(settings.download_root_local)
    state.update(running=True, phase="扫描下载根目录", done=0, total=0, current="",
                 registered=0, matched=[], unmatched=[], skipped=0, error=None)
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
                    n = 0
                    for vp in vids:
                        rel = str(vp.relative_to(root)).replace("\\", "/")
                        # 逐文件再看其所在子目录(如 .../SPs/、.../BDRip/)叠加上下文片源
                        sub_source = detect_source("/".join(vp.relative_to(folder).parts[:-1]))
                        try:
                            if _register_file(db, b, t, rel, vp, sub_source or folder_source):
                                n += 1
                        except Exception as e:  # noqa: BLE001 — 单文件失败不拖垮整夹
                            log.warning("库扫描登记失败 %s: %s", vp, e)
                    state["registered"] += n
                    state["matched"].append(f"{b.title}: +{n}/{len(vids)}")
            except Exception as e:  # noqa: BLE001
                log.warning("库扫描处理 %s 失败: %s", folder.name, e)
                state["unmatched"].append(f"{folder.name}(出错)")
        log.info("番剧库扫描完成:登记 %s 个文件,匹配 %s 部,未匹配 %s",
                 state["registered"], len(state["matched"]), len(state["unmatched"]))
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        log.exception("番剧库扫描失败")
    finally:
        state.update(running=False, phase="完成", current="")


def start() -> None:
    if state["running"]:
        return
    threading.Thread(target=_run, daemon=True).start()
