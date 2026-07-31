"""视频文件:待确认列表 + 手动管理(归位/改类型/删除/重探测)。"""
import logging
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Episode, EpisodeType, Torrent, VideoFile

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


def _safe_path(relative_path: str, *, must_exist: bool = True) -> tuple[Path, str]:
    """把网页传入的相对路径限制在媒体根内，连符号链接逃逸也拒绝。"""
    rel = (relative_path or "").replace("\\", "/").strip("/")
    parts = PurePosixPath(rel).parts if rel else ()
    if any(part in ("", ".", "..") for part in parts) or re.match(r"^[A-Za-z]:", rel):
        raise HTTPException(400, "路径必须是媒体根内的相对路径")
    root = settings.download_root_local.resolve()
    path = (root / Path(*parts)).resolve()
    if path != root and root not in path.parents:
        raise HTTPException(400, "路径超出媒体根")
    if must_exist and not path.exists():
        raise HTTPException(404, "文件或目录不存在")
    return path, "/".join(parts)


def _video_file(file_id: int, db: Session) -> tuple[VideoFile, Path]:
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404, "文件记录不存在")
    path, _ = _safe_path(vf.relative_path)
    if not path.is_file():
        raise HTTPException(404, "物理文件不存在")
    return vf, path


@router.get("/unassigned")
def unassigned(db: Session = Depends(get_db)):
    rows = db.execute(select(VideoFile).where(VideoFile.episode_id.is_(None))).scalars().all()
    return [{
        "id": f.id, "path": f.relative_path, "size": f.size,
        "torrent_id": f.torrent_id, "torrent_title": f.torrent.title_raw,
        "bangumi_id": f.torrent.subscription.bangumi_id,
        "bangumi_title": f.torrent.subscription.bangumi.title,
    } for f in rows]


@router.get("/browse")
def browse(path: str = ""):
    """网页文件管理器：只浏览容器已挂载的媒体根，不接受宿主机绝对路径。"""
    folder, rel = _safe_path(path)
    if not folder.is_dir():
        raise HTTPException(400, "目标不是目录")
    entries = []
    try:
        children = sorted(folder.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as e:
        raise HTTPException(409, f"目录不可读: {e}") from None
    for child in children[:1000]:
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        child_rel = f"{rel}/{child.name}".strip("/")
        suffix = child.suffix.lower()
        entries.append({
            "name": child.name,
            "path": child_rel,
            "is_dir": is_dir,
            "size": size,
            "is_video": not is_dir and suffix in {
                ".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov", ".ts", ".m2ts",
            },
            "content_url": None if is_dir else f"/api/files/content?path={quote(child_rel)}",
        })
    parent = "/".join(PurePosixPath(rel).parts[:-1]) if rel else None
    return {
        "path": rel,
        "parent": parent,
        "entries": entries,
        "truncated": len(children) > 1000,
        "root": str(settings.download_root_local),
    }


@router.get("/content")
def content(path: str, download: bool = False):
    """浏览/下载媒体根内任意文件；Starlette FileResponse 原生支持 HTTP Range。"""
    local, _ = _safe_path(path)
    if not local.is_file():
        raise HTTPException(400, "目标不是文件")
    media_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    return FileResponse(local, media_type=media_type, filename=local.name,
                        content_disposition_type=disposition)


@router.post("/mkdir")
def mkdir(payload: dict):
    """在媒体根内创建文件夹；不提供任意重命名，避免让下载器做种路径失效。"""
    parent, parent_rel = _safe_path(str(payload.get("parent") or ""))
    name = str(payload.get("name") or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(400, "文件夹名称非法")
    target, rel = _safe_path(f"{parent_rel}/{name}".strip("/"), must_exist=False)
    try:
        target.mkdir()
    except FileExistsError:
        raise HTTPException(409, "同名文件或目录已存在") from None
    except OSError as e:
        raise HTTPException(409, f"创建失败: {e}") from None
    return {"ok": True, "path": rel}


def _transcode(path: Path):
    """实时输出浏览器兼容的 fragmented MP4；客户端断开时立即终止 ffmpeg。"""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            chunk = proc.stdout.read(256 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


@router.get("/{file_id}/stream")
def stream(file_id: int, compatible: bool = False, db: Session = Depends(get_db)):
    """网页播放：直接模式支持 Range；兼容模式实时转为 H.264/AAC fragmented MP4。"""
    _vf, local = _video_file(file_id, db)
    if not compatible:
        media_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        return FileResponse(local, media_type=media_type, content_disposition_type="inline")
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "当前镜像未安装 ffmpeg，无法兼容转码")
    return StreamingResponse(
        _transcode(local),
        media_type="video/mp4",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
        try:
            number = float(number)
        except (TypeError, ValueError):
            raise HTTPException(400, "episode_number 必须是数字") from None
        if number < 0:
            raise HTTPException(400, "episode_number 不能小于 0")

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


@router.post("/{file_id}/activate")
def activate_version(file_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    """手动选择同集当前版本;preferred=false 时清除手选并恢复自动 BD>Web 判优。"""
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    if not vf.episode_id:
        raise HTTPException(400, "未归位文件不能设为当前版本")
    phase = bool(vf.torrent.is_preview)
    peers = db.execute(
        select(VideoFile).join(Torrent, VideoFile.torrent_id == Torrent.id)
        .where(VideoFile.episode_id == vf.episode_id,
               Torrent.is_preview.is_(phase))).scalars().all()
    for peer in peers:
        peer.is_preferred = False
    if (payload or {}).get("preferred", True):
        vf.is_preferred = True
    from app.services.postprocess import _apply_version_switch
    _apply_version_switch(db, vf.episode_id)
    db.commit()
    return {"ok": True, "active_file_id": next(
        (peer.id for peer in peers if peer.is_active), None),
        "preferred_file_id": next((peer.id for peer in peers if peer.is_preferred), None)}


@router.delete("/{file_id}")
def delete_file(file_id: int, delete_disk: bool = False, db: Session = Depends(get_db)):
    """从库里移除该文件记录;delete_disk=True 时尽力删磁盘文件(做种中的种子谨慎)。"""
    vf = db.get(VideoFile, file_id)
    if not vf:
        raise HTTPException(404)
    old_ep = vf.episode_id
    if delete_disk:
        base = settings.download_root_local.resolve()
        path = (settings.download_root_local / vf.relative_path).resolve()
        if base != path and base not in path.parents:   # 防目录穿越:解析后须仍在下载根内
            raise HTTPException(400, "文件路径非法(超出下载根)")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass   # 磁盘上本就不存在,继续清掉幽灵记录
        except OSError as e:  # 做种锁定/权限错误时保留库记录,让用户知道文件没有被删除
            log.warning("删除磁盘文件失败 %s: %s", path, e)
            raise HTTPException(409, f"删除磁盘文件失败:{e}") from None
    db.delete(vf)
    db.flush()
    if old_ep:
        from app.services.postprocess import _apply_version_switch
        _apply_version_switch(db, old_ep)
    db.commit()
    return {"ok": True}
