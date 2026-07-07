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
from app.models import (Episode, EpisodeType, Kind, Torrent, TorrentEpisode,
                        TorrentStatus, VideoFile)
from app.parsers.title_parser import parse
from app.services import media_probe

log = logging.getLogger(__name__)

_queue: "queue.Queue[int]" = queue.Queue()


def enqueue(torrent_id: int) -> None:
    _queue.put(torrent_id)


def _match_episode(db: Session, t: Torrent, filename: str) -> int | None:
    """文件名 → episode_id。单集种子直接用其关联集;合集按文件名解析定集。

    剧集类型(正片/OP·ED/SP/PV…)由解析器判定:非正片不占用正片集号(独立 type)。
    """
    sub = t.subscription
    # 影片/OVA:不归正片集,文件留作「影片本体」(避免剧场版被当成第 1 话)
    if sub and sub.bangumi and sub.bangumi.kind != Kind.TV:
        return None
    linked = db.execute(select(TorrentEpisode.episode_id).where(
        TorrentEpisode.torrent_id == t.id)).scalars().all()
    if len(linked) == 1 and not t.is_batch:
        return linked[0]

    parsed = parse(filename)
    ep_type = (EpisodeType(parsed.ep_type)
               if parsed.ep_type in EpisodeType._value2member_map_ else EpisodeType.REGULAR)

    if ep_type == EpisodeType.REGULAR:
        if len(parsed.episodes) != 1:
            return None
        number = parsed.episodes[0] - (sub.episode_offset or 0)
        if number <= 0:
            return None
    else:
        # 非正片:用自身小序号(SP1/OVA03)或留空;不减集数偏移
        number = parsed.episodes[0] if len(parsed.episodes) == 1 else None

    q = select(Episode).where(Episode.bangumi_id == sub.bangumi_id, Episode.type == ep_type)
    q = q.where(Episode.number == number) if number is not None else q.where(Episode.number.is_(None))
    ep = db.execute(q).scalars().first()
    if ep is None:
        ep = Episode(bangumi_id=sub.bangumi_id, number=number, type=ep_type)
        db.add(ep)
        db.flush()
    if ep.id not in linked:
        db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
        db.flush()
    return ep.id


# 片源优先级:BD > Web > 未知。决定同集多文件谁 is_active(Web→BD 升级靠它切换)
_SOURCE_RANK = {"BD": 0, "Web": 1}


def _file_quality(f: VideoFile) -> tuple:
    """画质优先序(越小越优):片源 BD>Web > 未知,再版本号高,再体积大,最后 id 稳定。

    体积兜底很关键:同集挂了多个 BD 文件(真正片 + 被误映射的菜单/NC 小片)时,
    最大的那个才是正片 → 选它,其余置灰隐藏(用户说的「同一集多个源」只留一个)。
    """
    return (_SOURCE_RANK.get(f.source, 2), -(f.torrent.version or 1), -(f.size or 0), f.id)


def _apply_version_switch(db: Session, episode_id: int) -> None:
    """同一剧集多文件:**每个阶段(先行/正式)各**保留唯一画质最优的为 is_active,其余置 0。

    覆盖 v2 决议 + 跨字幕组 Web→BD 升级(BD 完成后顶替 Web)+ 同集多源去重。
    先行与正式分开各留一个:官方开播后正式集入库,先行版仍保留一个 active 供详情页先行分段展示。
    """
    files = db.execute(select(VideoFile).join(Torrent).where(
        VideoFile.episode_id == episode_id)).scalars().all()
    if not files:
        return
    for phase in (False, True):
        group = [f for f in files if bool(f.torrent.is_preview) is phase]
        if not group:
            continue
        best = min(group, key=_file_quality)
        for f in group:
            f.is_active = f is best
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
        # BD 特典(带描述标签的非正片:NCOP/Menu/短剧/TALK/Lyric…)不按 web 集号当正片,不在剧集
        # 网格登记;留在发行目录里经「打开目录」浏览。仅 BD 源生效,Web 走原逻辑不变。
        from app.services.bd_scan import bd_is_extra_video
        base = PurePosixPath(rel_path).name
        if parse(base).source == "BD" and bd_is_extra_video(base):
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
                vf.color_depth = r.color_depth
                vf.hdr = r.hdr
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
    if touched_eps:
        try:
            # bgm.tv 收视进度回写(可选,后台线程):追番下载入库 → 标「在看」+集「看过」。
            # 只挂 RSS/智能下载的后处理;本地导入旧库不回写(下载≠看过)。
            from app.services.bgm_sync import report_progress
            report_progress(t.subscription.bangumi_id, list(touched_eps))
        except Exception:  # noqa: BLE001
            pass

    if failures:
        t.error_message = f"{failures} 个文件探测失败,可重试"
        db.flush()
        log.info("后处理 #%s 停在 COMPLETED(%s 个探测失败,待重试)", t.id, failures)
        return
    # 文件映射 + ARCHIVED 先落库:整理(qB 改名)即便出错,也不回滚文件记录、
    # 不让种子卡回 COMPLETED(否则会被 drain 反复重入队 → organize 反复改名 → 搅乱 qB)
    t.status = TorrentStatus.ARCHIVED
    t.error_message = None
    db.commit()
    try:
        from app.services.organize import organize_torrent
        organize_torrent(db, t)
        db.commit()
    except Exception as e:  # noqa: BLE001 — 整理失败:回滚整理改动,文件/状态已入库
        db.rollback()
        log.warning("整理 #%s 异常(文件已入库,跳过整理): %s", t.id, e)
    log.info("后处理完成 #%s → archived", t.id)
    # 生命周期:本次完成可能令该番完结(下满)或全 BD(补全完成)→ 即时转补全/收尾
    try:
        from app.services.lifecycle import on_torrent_processed
        on_torrent_processed(db, t.subscription.bangumi_id if t.subscription else None)
        db.commit()
    except Exception:  # noqa: BLE001 — 生命周期失败不影响后处理结果
        db.rollback()
        log.exception("后处理生命周期处理失败 #%s", t.id)


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
