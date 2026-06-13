"""系统:健康检查、手动触发轮询。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.database import get_db

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    try:
        dl = downloader.healthy()
        return {"status": "ok", "downloader": downloader.name, "info": dl}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "downloader": downloader.name, "error": str(e)}


@router.post("/poll")
def poll_now(db: Session = Depends(get_db)):
    from app.services.rss_engine import poll_all
    results = poll_all(db)
    db.commit()
    return {"results": results}
