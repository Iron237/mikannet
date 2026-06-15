"""Mikanarr 入口。开发:uvicorn app.main:app --reload"""
import asyncio
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import scheduler
from app.api import (bangumi, bd, config, files, import_mikan, logs, notifications, search,
                     subscriptions, system, tasks, ws)
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
    try:
        downloader.ensure_ready()
        log.info("下载器[%s]连接正常: %s", downloader.name, downloader.healthy())
    except Exception as e:  # noqa: BLE001 — 下载器暂不可达不阻塞启动
        log.warning("下载器[%s]暂不可达: %s", downloader.name, e)
    scheduler.start()
    from app.services import postprocess
    postprocess.start()
    stop_event = asyncio.Event()
    tracker = asyncio.create_task(tracker_loop(stop_event))
    yield
    stop_event.set()
    await tracker
    scheduler.stop()


app = FastAPI(title="Mikanarr", version="0.1.0", lifespan=lifespan)
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
app.include_router(logs.router)

(settings.data_dir / "images").mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=settings.data_dir), name="data")

# 生产形态:托管打包后的前端(开发期用 vite dev server 则此目录不存在)
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # SPA 回退:非 API/静态路径一律返回 index.html
        return FileResponse(_DIST / "index.html")
