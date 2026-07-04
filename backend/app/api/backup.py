"""数据备份 / 迁移:导出整库为 JSON、导入还原(用于迁移到全新部署)。"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import backup

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/export")
def export_data(include_settings: bool = False, db: Session = Depends(get_db)):
    """导出整库 JSON(下载)。include_settings=1 时附带设置/通知(含 cookie/凭据,跨机慎用)。"""
    data = backup.export_all(db, include_settings=include_settings)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="mikannet-backup-{stamp}.json"'})


@router.post("/import")
def import_data(payload: dict, db: Session = Depends(get_db)):
    """导入备份。payload = 备份 JSON 对象。番剧库整表替换;若文件含设置/通知则一并还原
    (设置走 settings_service.update 合并 + 即时生效,无需额外重载或勾选)。"""
    try:
        counts = backup.import_all(db, payload)
        db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 — 失败回滚,保持当前库不被破坏
        db.rollback()
        log.exception("导入备份失败")
        raise HTTPException(500, f"导入失败(已回滚):{e}") from None
    # 设置在数据 commit 之后再应用(单写者下避免两会话写锁互锁)
    applied = backup.apply_settings(payload)
    counts["settings"] = applied
    log.info("导入备份完成:%s", counts)
    return {"ok": True, "imported": counts, "total": sum(counts.values()),
            "settings_applied": applied}
