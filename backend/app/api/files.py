"""视频文件:待确认列表 + 手动归位(解析不出集数的文件)。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Episode, EpisodeType, Torrent, VideoFile

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
    """手动指定该文件属于第几话。payload: {"episode_number": 8, "type": "regular"}

    type 接受新枚举值(regular/special/credits/trailer/other);兼容旧名 EP/SP。
    """
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    number = payload.get("episode_number")
    if number is None:
        raise HTTPException(400, "缺少 episode_number")
    raw_type = str(payload.get("type") or "regular")
    _legacy = {"EP": "regular", "SP": "special", "OVA": "special", "MOVIE": "special"}
    try:
        ep_type = EpisodeType(_legacy.get(raw_type, raw_type.lower()))
    except ValueError:
        ep_type = EpisodeType.REGULAR
    t: Torrent = vf.torrent
    bangumi_id = t.subscription.bangumi_id

    ep = db.execute(select(Episode).where(
        Episode.bangumi_id == bangumi_id, Episode.type == ep_type,
        Episode.number == float(number))).scalar_one_or_none()
    if ep is None:
        ep = Episode(bangumi_id=bangumi_id, number=float(number), type=ep_type)
        db.add(ep)
        db.flush()
    vf.episode_id = ep.id

    from app.services.postprocess import _apply_version_switch
    _apply_version_switch(db, ep.id)
    db.commit()
    return {"ok": True, "episode_id": ep.id}
