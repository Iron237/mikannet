"""日志查看(日志页):实时记录 + 历史归档下载。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services import logbuf

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def get_logs(level: str = "ALL", limit: int = 800):
    return logbuf.recent(level, limit)


@router.get("/archives")
def list_archives():
    return logbuf.archives()


@router.get("/archives/{name}")
def download_archive(name: str):
    if not (name.startswith("mikanarr-") and name.endswith(".log.gz")) or "/" in name or "\\" in name:
        raise HTTPException(400, "非法文件名")
    p = logbuf.LOG_DIR / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/gzip", filename=name)
