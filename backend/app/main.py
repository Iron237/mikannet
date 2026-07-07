"""Mikannet 入口。开发:uvicorn app.main:app --reload"""
import asyncio
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import scheduler
from app._version import VERSION
from app.api import (backup, bangumi, bd, config, discover, files, import_mikan, launch,
                     logs, notifications, search, setup, subscriptions, system, tasks, ws)
from app.clients.downloader import downloader
from app.config import settings
from app.database import init_db
from app.services import logbuf
from app.services.download_tracker import tracker_loop

logbuf.setup()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from app.services import settings_service
    settings_service.load_overrides()   # DB 设置覆盖 .env(在连下载器之前)
    from app.services import storage
    storage.ensure_at_startup()          # smb 模式:把 NAS 挂到 /downloads(失败不阻塞)
    try:
        downloader.ensure_ready()
        log.info("下载器[%s]连接正常: %s", downloader.name, downloader.healthy())
    except Exception as e:  # noqa: BLE001 — 下载器暂不可达不阻塞启动
        log.warning("下载器[%s]暂不可达: %s", downloader.name, e)
    scheduler.start()
    from app.services import postprocess
    postprocess.start()
    from app.services import metadata_service
    metadata_service.start_ep_start_backfill()   # 存量番剧首话编号一次性回填(后台,幂等)
    stop_event = asyncio.Event()
    tracker = asyncio.create_task(tracker_loop(stop_event))
    yield
    stop_event.set()
    await tracker
    scheduler.stop()


app = FastAPI(title="Mikannet", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def _updating_guard(request: Request, call_next):
    """自更新应用期间挡掉新的写请求(更新端点自身放行),避免半更新状态下写库。"""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        from app.services import update_gate
        if update_gate.is_updating() and "/api/system/update/" not in request.url.path:
            return JSONResponse({"detail": "系统正在更新,请稍候…"}, status_code=503)
    return await call_next(request)


app.include_router(subscriptions.router)
app.include_router(tasks.router)
app.include_router(system.router)
app.include_router(search.router)
app.include_router(bangumi.router)
app.include_router(bd.router)
app.include_router(ws.router)
app.include_router(files.router)
app.include_router(notifications.router)
app.include_router(import_mikan.router)
app.include_router(config.router)
app.include_router(launch.router)
app.include_router(backup.router)
app.include_router(setup.router)
app.include_router(logs.router)
app.include_router(discover.router)

(settings.data_dir / "images").mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=settings.data_dir), name="data")

# 生产形态:托管打包后的前端(开发期用 vite dev server 则此目录不存在)
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # API 路径保留 404 语义:否则接口改名/拼错会被吞成 200 + index.html,
        # 前端 try/catch 感知不到错误(排查极隐蔽)
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # dist 根目录的真实静态文件(favicon.svg 等)直接返回;其余非 API 路径回退 index.html
        if path:
            candidate = (_DIST / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_DIST.resolve()):
                return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
