"""完成后处理:文件枚举 → 逐文件定集 → ffprobe → 文件↔剧集映射 → v2 切换。

串行 worker 线程消费队列(SMB 上禁止并发探测);
探测失败停在 COMPLETED 留错误标记,可重试 — 不阻塞通知与库展示(计划 §四)。
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.config import settings
from app.database import db_session
from app.models import (Episode, EpisodeType, Torrent, TorrentEpisode, TorrentStatus,
                        VideoFile)
from app.parsers.title_parser import parse
from app.services import media_probe

log = logging.getLogger(__name__)

_queue: "queue.Queue[int]" = queue.Queue()


def enqueue(torrent_id: int) -> None:
    _queue.put(torrent_id)


def _match_episode(db: Session, t: Torrent, filename: str) -> int | None:
    """文件名 → episode_id。单集种子直接用其关联集;合集按文件名解析定集。"""
    linked = db.execute(select(TorrentEpisode.episode_id).where(
        TorrentEpisode.torrent_id == t.id)).scalars().all()
    if len(linked) == 1 and not t.is_batch:
        return linked[0]

    parsed = parse(filename)
    if len(parsed.episodes) != 1:
        return None
    sub = t.subscription
    number = parsed.episodes[0] - (sub.episode_offset or 0)
    if number <= 0:
        return None
    ep = db.execute(select(Episode).where(
        Episode.bangumi_id == sub.bangumi_id, Episode.type == EpisodeType.EP,
        Episode.number == number)).scalar_one_or_none()
    if ep is None:
        ep = Episode(bangumi_id=sub.bangumi_id, number=number, type=EpisodeType.EP)
        db.add(ep)
        db.flush()
    if ep.id not in linked:
        db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
        db.flush()
    return ep.id


def _apply_version_switch(db: Session, episode_id: int) -> None:
    """同一剧集多版本文件:只保留最高版本种子的文件为 is_active(v2 决议)。"""
    files = db.execute(select(VideoFile).join(Torrent).where(
        VideoFile.episode_id == episode_id)).scalars().all()
    if not files:
        return
    best = max(f.torrent.version for f in files)
    for f in files:
        f.is_active = f.torrent.version == best
    db.flush()


def process_torrent(db: Session, torrent_id: int) -> None:
    t = db.get(Torrent, torrent_id)
    if t is None or t.status not in (TorrentStatus.COMPLETED, TorrentStatus.ARCHIVED):
        return
    try:
        dl_files = downloader.files(t.info_hash)
    except Exception as e:  # noqa: BLE001
        t.error_message = f"获取文件列表失败: {e}"
        db.flush()
        return

    failures = 0
    touched_eps: set[int] = set()
    for qf in dl_files:
        # name 已是相对 download_root 的路径(qB/BitComet 后端统一)
        rel_path = qf["name"].replace("\\", "/").lstrip("/")
        if not media_probe.is_video(rel_path):
            continue
        vf = db.execute(select(VideoFile).where(
            VideoFile.torrent_id == t.id,
            VideoFile.relative_path == rel_path)).scalar_one_or_none()
        if vf is None:
            vf = VideoFile(torrent_id=t.id, relative_path=rel_path, size=qf.get("size"))
            db.add(vf)
            db.flush()

        if vf.episode_id is None:
            vf.episode_id = _match_episode(db, t, PurePosixPath(rel_path).name)
        if vf.episode_id:
            touched_eps.add(vf.episode_id)

        if vf.subgroup is None and vf.source is None:   # 字幕组/片源:从文件名解析
            pp = parse(PurePosixPath(rel_path).name)
            vf.subgroup = pp.group or (t.subscription.subgroup_name if t.subscription else None)
            vf.source = pp.source

        if vf.probed_at is None:
            local = settings.download_root_local / rel_path
            try:
                r = media_probe.probe(local)
                vf.resolution = r.resolution
                vf.video_codec = r.video_codec
                vf.bitrate = r.bitrate
                vf.audio_tracks = r.audio_tracks
                vf.subtitle_tracks = r.subtitle_tracks
                vf.probed_at = datetime.now(timezone.utc)
            except Exception as e:  # noqa: BLE001 — SMB 抖动等,保留行可重试
                failures += 1
                log.warning("ffprobe 失败 %s: %s", local, e)
        db.flush()

    for ep_id in touched_eps:
        _apply_version_switch(db, ep_id)

    if failures:
        t.error_message = f"{failures} 个文件探测失败,可重试"
    else:
        # 整理:qB 原地重命名成 Jellyfin 结构 + 写 NFO/封面(可开关,改 ADR-0001)
        try:
            from app.services.organize import organize_torrent
            organize_torrent(db, t)
        except Exception as e:  # noqa: BLE001 — 整理失败不阻断入库
            log.warning("整理 #%s 异常: %s", t.id, e)
        t.status = TorrentStatus.ARCHIVED
        t.error_message = None
    db.flush()
    log.info("后处理完成 #%s → %s(失败 %s)", t.id, t.status.value, failures)


def _worker() -> None:
    log.info("postprocess worker 启动")
    while True:
        tid = _queue.get()
        if tid < 0:
            break
        try:
            with db_session() as db:
                process_torrent(db, tid)
        except Exception:  # noqa: BLE001
            log.exception("后处理 #%s 异常", tid)


def start() -> None:
    threading.Thread(target=_worker, daemon=True, name="postprocess").start()
    # 启动对账:把停在 COMPLETED 的任务重新入队
    with db_session() as db:
        ids = db.execute(select(Torrent.id).where(
            Torrent.status == TorrentStatus.COMPLETED)).scalars().all()
    for tid in ids:
        enqueue(tid)
    if ids:
        log.info("启动对账:%s 个 COMPLETED 任务重新入队后处理", len(ids))
