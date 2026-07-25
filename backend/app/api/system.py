"""系统:健康检查、版本、自更新、手动触发轮询。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app._version import BASE_REV, VERSION
from app.clients.downloader import downloader
from app.database import SessionLocal, get_db

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/version")
def version():
    """运行中实例的版本与依赖基线(自更新比较用)。"""
    return {"version": VERSION, "base_rev": BASE_REV}


@router.get("/healthz")
def healthz():
    """轻量健康探针(PID-1 wrapper 自检用):服务起来 + DB 可开 → 200。"""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()
    return {"ok": True, "version": VERSION}


@router.get("/health")
def health():
    try:
        dl = downloader.healthy()
        return {"status": "ok", "downloader": downloader.name, "info": dl}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "downloader": downloader.name, "error": str(e)}


@router.get("/update/check")
def update_check(prerelease: bool | None = None):
    """只读检查更新:当前 vs 最新、类型(none/code/full)、changelog、大小、通道。"""
    from app.services import updater
    try:
        return updater.check(include_prerelease=prerelease)
    except Exception as e:  # noqa: BLE001 — 网络/GitHub 不可达 → 友好报错,不崩
        raise HTTPException(status_code=502, detail=f"检查更新失败:{e}") from e


@router.get("/update/status")
def update_status():
    """应用更新进度(下载/校验/应用/重启)。"""
    from app.services import updater
    return updater.get_status()


@router.post("/update/apply")
def update_apply():
    """按检查结果的类型自动应用(纯代码就地 / 完整换镜像)。后台执行,前端轮询 status + version。"""
    from app.services import update_gate, updater
    reason = update_gate.busy_reason()
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    try:
        return {"ok": True, **updater.apply_latest()}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"启动更新失败:{e}") from e


@router.post("/poll")
def poll_now(db: Session = Depends(get_db)):
    from app.services.rss_engine import poll_all
    results = poll_all(db)
    db.commit()
    return {"results": results}
