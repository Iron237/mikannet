"""日志查看(日志页):实时记录 + 历史归档 + 打开 log 目录(协议头)。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.services import launch, logbuf

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def get_logs(level: str = "ALL", limit: int = 800):
    return logbuf.recent(level, limit)


@router.get("/dir")
def logs_dir():
    """「打开 log 目录」:mikannet://reveal 到 data 目录下 logs 文件夹(历史日志重启时压缩存这里)。
    需配 data_host_root + 装协议处理器;未配置 → reveal_url=None,前端给提示。"""
    return {
        "reveal_url": launch.launch_url("reveal", launch.data_host_path("logs")),
        "configured": bool(settings.data_host_root),
        "archive_count": len(logbuf.archives()),
    }


@router.get("/archives")
def list_archives():
    return logbuf.archives()


@router.get("/archives/{name}")
def download_archive(name: str):
    if not (name.startswith("mikannet-") and name.endswith(".log.gz")) or "/" in name or "\\" in name:
        raise HTTPException(400, "非法文件名")
    p = logbuf.LOG_DIR / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/gzip", filename=name)
