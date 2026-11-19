"""单集 XML 旁挂文件，手动下载与自动下载共用原子保存。"""
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException

from app.clients import dandanplay
from app.config import settings
from app.services.file_publish import rename_new

_locks = [Lock() for _ in range(32)]


def publish(xml: Path, data: bytes) -> None:
    staged = xml.parent / f".mikannet-danmaku-{uuid4().hex}.part"
    try:
        output = staged.open("xb")
        try:
            with output:
                output.write(data)
            rename_new(staged, xml)
        finally:
            staged.unlink(missing_ok=True)
    except FileExistsError as exc:
        raise HTTPException(409, "同名 XML 已存在，请重新打开弹幕窗口查看") from exc
    except OSError as exc:
        raise HTTPException(409, "媒体目录写入失败，可选择下载到浏览器") from exc


def fetch_available(video: Path, original_name: str | None) -> dict:
    if not settings.danmaku_auto_download:
        return {"status": "disabled"}
    video = video.resolve()
    if not video.is_relative_to(settings.download_root_local.resolve()) or not video.is_file():
        raise HTTPException(404, "媒体文件不可用")
    xml = video.with_suffix(".xml")
    if xml.is_symlink() or (xml.exists() and not xml.is_file()):
        raise HTTPException(409, "同名 XML 路径不是普通文件")
    if xml.is_file():
        return {"status": "existing"}
    if not dandanplay.configured():
        return {"status": "unconfigured"}
    lock = _locks[hash(video) % len(_locks)]
    with lock:
        if xml.is_file():
            return {"status": "existing"}
        before = video.stat()
        match = dandanplay.match(video, original_name)
        if not match["exact"] or len(match["choices"]) != 1:
            return {"status": "needs_selection"}
        choice = match["choices"][0]
        try:
            data, count = dandanplay.download(choice["episode_id"], choice["shift"])
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"status": "empty"}
            raise
        after = video.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise HTTPException(409, "视频已变更，请重新匹配")
        publish(xml, data)
        return {"status": "saved", "count": count}
