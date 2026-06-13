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
from app.parsers.title_parser import parse
from app.services import media_probe
from app.services.local_import import LOCAL_SUBGROUP_ID

log = logging.getLogger(__name__)

state = {"running": False, "phase": "", "done": 0, "total": 0,
         "current": "", "registered": 0, "matched": [], "unmatched": [], "error": None}

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_NORM = re.compile(r"[\s·:：!！?？,，.。\-—_~、]+")


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
    # 匹配不到 → Mikan 搜索创建(让库自动补全这部番剧)
    try:
        hits = mikan_client.search(folder)
    except Exception as e:  # noqa: BLE001
        log.warning("库扫描:Mikan 搜索 %s 失败: %s", folder, e)
        return None
    if not hits:
        return None
    mid = hits[0].mikan_bangumi_id
    b = db.execute(select(Bangumi).where(Bangumi.mikan_bangumi_id == mid)).scalar_one_or_none()
    if b is None:
        b = Bangumi(mikan_bangumi_id=mid, title=hits[0].title or folder)
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


def _register_file(db, b: Bangumi, t: Torrent, rel: str, abspath: Path) -> bool:
    """就地登记一个视频文件为 VideoFile(+ 剧集映射 + ffprobe)。已登记返回 False。"""
    exists = db.execute(select(VideoFile.id).where(
        VideoFile.relative_path == rel)).first()
    if exists:
        return False
    p = parse(abspath.name)
    vf = VideoFile(torrent_id=t.id, relative_path=rel, subgroup=p.group, source=p.source)
    try:
        vf.size = abspath.stat().st_size
    except OSError:
        pass
    db.add(vf)
    db.flush()
    if len(p.episodes) == 1:
        n = p.episodes[0]
        if n > 0:
            ep = db.execute(select(Episode).where(
                Episode.bangumi_id == b.id, Episode.type == EpisodeType.EP,
                Episode.number == n)).scalar_one_or_none()
            if ep is None:
                ep = Episode(bangumi_id=b.id, number=n, type=EpisodeType.EP)
                db.add(ep)
                db.flush()
            vf.episode_id = ep.id
            if not db.get(TorrentEpisode, (t.id, ep.id)):
                db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
    try:
        r = media_probe.probe(abspath)
        vf.resolution = r.resolution
        vf.video_codec = r.video_codec
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
                 registered=0, matched=[], unmatched=[], error=None)
    try:
        if not root.is_dir():
            raise FileNotFoundError(f"下载根目录不可访问: {root}")
        folders = sorted([d for d in root.iterdir() if d.is_dir()], key=lambda d: d.name)
        state["total"] = len(folders)
        for idx, folder in enumerate(folders, 1):
            state.update(done=idx, current=folder.name)
            vids = [p for p in folder.rglob("*") if p.is_file() and media_probe.is_video(p)]
            if not vids:
                continue
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
                        try:
                            if _register_file(db, b, t, rel, vp):
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
