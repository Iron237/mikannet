"""视频文件:待确认列表 + 手动管理(归位/改类型/删除/重探测)。"""
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Episode, EpisodeType, Torrent, VideoFile

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/unassigned")
def unassigned(db: Session = Depends(get_db)):
    rows = db.execute(select(VideoFile).where(VideoFile.episode_id.is_(None))).scalars().all()
    return [{
        "id": f.id, "path": f.relative_path, "size": f.size,
        "torrent_id": f.torrent_id, "torrent_title": f.torrent.title_raw,
        "bangumi_id": f.torrent.subscription.bangumi_id,
        "bangumi_title": f.torrent.subscription.bangumi.title,
    } for f in rows]


@router.post("/{file_id}/assign")
def assign(file_id: int, payload: dict, db: Session = Depends(get_db)):
    """手动指定该文件属于第几话 / 改剧集类型。payload: {"episode_number": 8, "type": "regular"}

    type 接受新枚举值(regular/special/credits/trailer/other);兼容旧名 EP/SP。
    正片必须给 episode_number;特别篇/OP·ED/PV/其他 可不给(归到该类型的无号集)。
    """
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    raw_type = str(payload.get("type") or "regular")
    _legacy = {"EP": "regular", "SP": "special", "OVA": "special", "MOVIE": "special"}
    try:
        ep_type = EpisodeType(_legacy.get(raw_type, raw_type.lower()))
    except ValueError:
        ep_type = EpisodeType.REGULAR
    number = payload.get("episode_number")
    if number in ("", None):
        if ep_type == EpisodeType.REGULAR:
            raise HTTPException(400, "正片必须指定 episode_number")
        number = None
    else:
        number = float(number)

    t: Torrent = vf.torrent
    bangumi_id = t.subscription.bangumi_id
    old_ep = vf.episode_id

    q = select(Episode).where(Episode.bangumi_id == bangumi_id, Episode.type == ep_type)
    q = q.where(Episode.number == number) if number is not None else q.where(Episode.number.is_(None))
    ep = db.execute(q).scalars().first()
    if ep is None:
        ep = Episode(bangumi_id=bangumi_id, number=number, type=ep_type)
        db.add(ep)
        db.flush()
    vf.episode_id = ep.id

    from app.services.postprocess import _apply_version_switch
    _apply_version_switch(db, ep.id)
    if old_ep and old_ep != ep.id:
        _apply_version_switch(db, old_ep)   # 原集少了一个文件,重算 is_active
    db.commit()
    return {"ok": True, "episode_id": ep.id}


@router.post("/{file_id}/unassign")
def unassign(file_id: int, db: Session = Depends(get_db)):
    """取消归位:把文件移回「未匹配」(episode_id=None)。"""
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    old_ep = vf.episode_id
    vf.episode_id = None
    db.flush()
    if old_ep:
        from app.services.postprocess import _apply_version_switch
        _apply_version_switch(db, old_ep)
    db.commit()
    return {"ok": True}


@router.post("/{file_id}/reprobe")
def reprobe(file_id: int, db: Session = Depends(get_db)):
    """对该文件重跑 ffprobe,刷新分辨率/编码/色深/HDR/音轨/字幕轨。"""
    from app.services import media_probe
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    local = settings.download_root_local / vf.relative_path
    try:
        r = media_probe.probe(local)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"探测失败:{e}") from None
    vf.resolution = r.resolution
    vf.video_codec = r.video_codec
    vf.color_depth = r.color_depth
    vf.hdr = r.hdr
    vf.bitrate = r.bitrate
    vf.audio_tracks = r.audio_tracks
    vf.subtitle_tracks = r.subtitle_tracks
    vf.probed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "resolution": r.resolution}


@router.delete("/{file_id}")
def delete_file(file_id: int, delete_disk: bool = False, db: Session = Depends(get_db)):
    """从库里移除该文件记录;delete_disk=True 时尽力删磁盘文件(做种中的种子谨慎)。"""
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    old_ep = vf.episode_id
    if delete_disk:
        path = settings.download_root_local / vf.relative_path
        try:
            os.remove(path)
        except OSError as e:  # noqa: BLE001 — 文件可能已不在/做种锁定
            log.warning("删除磁盘文件失败 %s: %s", path, e)
    db.delete(vf)
    db.flush()
    if old_ep:
        from app.services.postprocess import _apply_version_switch
        _apply_version_switch(db, old_ep)
    db.commit()
    return {"ok": True}
