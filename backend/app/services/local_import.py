"""本地番剧归类导入:扫描已有视频 → 按作品分组 → 匹配 Mikan/bgm.tv →
移动到 {download_root}/{番剧名}/ → ffprobe → 剧集映射入库。

导入的文件不参与做种,移动不违反 ADR-0001(其针对的是 qB 下载产物)。
入库形态:每部番剧一个停用的「本地导入」订阅 + 一个合成 Torrent 行(ARCHIVED),
文件挂在其下,与正常下载共用库视图/详情页/缺集逻辑。
"""
from __future__ import annotations

import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import anitopy
from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.parsers.title_parser import parse
from app.services import media_probe

log = logging.getLogger(__name__)

LOCAL_SUBGROUP_ID = "local"
state = {"running": False, "phase": "", "done": 0, "total": 0,
         "imported": [], "errors": []}


def _guess_series(path: Path) -> str:
    """文件 → 作品名:anitopy anime_title,兜底用上级目录名。"""
    ani = anitopy.parse(path.name) or {}
    title = (ani.get("anime_title") or "").strip()
    if len(title) >= 2:
        return title
    parent = path.parent.name
    return re.sub(r"[\[\(【].*?[\]\)】]", "", parent).strip() or parent


def scan(source: str) -> list[dict]:
    """扫描目录,按作品分组并尝试匹配 Mikan。返回分组预览(不动文件)。"""
    from app.clients.mikan import mikan_client
    root = Path(source)
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {source}(需挂载进容器)")

    groups: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and media_probe.is_video(p):
            groups.setdefault(_guess_series(p), []).append(p)

    out = []
    for title, files in groups.items():
        mikan = None
        try:
            hits = mikan_client.search(title)
            if hits:
                mikan = {"mikan_bangumi_id": hits[0].mikan_bangumi_id, "title": hits[0].title}
        except Exception as e:  # noqa: BLE001
            log.warning("Mikan 匹配失败 %s: %s", title, e)
        out.append({
            "guess_title": title,
            "files": [str(f) for f in files],
            "episodes": sorted({e for f in files for e in parse(f.name).episodes}),
            "mikan": mikan,
        })
    return sorted(out, key=lambda g: -len(g["files"]))


_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


def _import_group(group: dict) -> str:
    from app.services.metadata_service import enrich_bangumi
    mid = group["mikan"]["mikan_bangumi_id"] if group.get("mikan") else None
    if not mid:
        raise ValueError(f"{group['guess_title']}: 未匹配到 Mikan 番剧,跳过")

    with db_session() as db:
        bangumi = db.execute(select(Bangumi).where(
            Bangumi.mikan_bangumi_id == mid)).scalar_one_or_none()
        if bangumi is None:
            bangumi = Bangumi(mikan_bangumi_id=mid, title=group["guess_title"])
            db.add(bangumi)
            db.flush()
            enrich_bangumi(db, bangumi)

        sub = db.execute(select(Subscription).where(
            Subscription.bangumi_id == bangumi.id,
            Subscription.mikan_subgroup_id == LOCAL_SUBGROUP_ID)).scalar_one_or_none()
        safe = _ILLEGAL.sub(" ", bangumi.title).strip() or f"bangumi {mid}"
        if sub is None:
            sub = Subscription(bangumi_id=bangumi.id, mikan_subgroup_id=LOCAL_SUBGROUP_ID,
                               subgroup_name="本地导入", enabled=False, backfill=False,
                               save_path=f"{settings.download_root}/{safe}")
            db.add(sub)
            db.flush()

        guid = f"local:{mid}:{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        torrent = Torrent(subscription_id=sub.id, guid=guid,
                          title_raw=f"[本地导入] {bangumi.title}({len(group['files'])} 个文件)",
                          parsed_json={}, torrent_url="", is_batch=True,
                          status=TorrentStatus.ARCHIVED,
                          completed_at=datetime.now(timezone.utc))
        db.add(torrent)
        db.flush()

        dest_dir = settings.download_root_local / safe
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fp in group["files"]:
            src = Path(fp)
            dest = dest_dir / src.name
            if not dest.exists():
                shutil.move(str(src), str(dest))   # 跨盘自动复制+删除
            rel = f"{safe}/{src.name}"
            vf = VideoFile(torrent_id=torrent.id, relative_path=rel,
                           size=dest.stat().st_size)
            db.add(vf)
            db.flush()
            p = parse(src.name)
            if len(p.episodes) == 1:
                n = p.episodes[0]
                ep = db.execute(select(Episode).where(
                    Episode.bangumi_id == bangumi.id, Episode.type == EpisodeType.EP,
                    Episode.number == n)).scalar_one_or_none()
                if ep is None:
                    ep = Episode(bangumi_id=bangumi.id, number=n, type=EpisodeType.EP)
                    db.add(ep)
                    db.flush()
                vf.episode_id = ep.id
                if not db.get(TorrentEpisode, (torrent.id, ep.id)):
                    db.add(TorrentEpisode(torrent_id=torrent.id, episode_id=ep.id))
            try:
                r = media_probe.probe(dest)
                vf.resolution = r.resolution
                vf.video_codec = r.video_codec
                vf.bitrate = r.bitrate
                vf.audio_tracks = r.audio_tracks
                vf.subtitle_tracks = r.subtitle_tracks
                vf.probed_at = datetime.now(timezone.utc)
            except Exception as e:  # noqa: BLE001
                log.warning("导入探测失败 %s: %s", dest, e)
            db.flush()
        return f"{bangumi.title}: {len(group['files'])} 个文件"


def run_import(groups: list[dict]) -> None:
    state.update(running=True, phase="导入中", done=0, total=len(groups),
                 imported=[], errors=[])
    try:
        for g in groups:
            state["done"] += 1
            try:
                state["imported"].append(_import_group(g))
            except Exception as e:  # noqa: BLE001
                log.exception("导入分组失败")
                state["errors"].append(str(e))
    finally:
        state.update(running=False, phase="完成")


def start_import(groups: list[dict]) -> None:
    threading.Thread(target=run_import, args=(groups,), daemon=True).start()
