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
    with db.begin():
        # sqlite3 默认的 legacy transaction control 不会为 SELECT 开启读事务。
        # 所有表使用同一快照，避免轮询在表与表之间新增子记录而生成无法还原的备份。
        conn = db.connection()
        if not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql("BEGIN")
        data = backup.export_all(db, include_settings=include_settings)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="mikannet-backup-{stamp}.json"'})


@router.post("/preview")
def preview_data(payload: dict, db: Session = Depends(get_db)):
    try:
        return backup.preview_restore(db, backup.prepare_restore(payload))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/import")
def import_data(payload: dict, db: Session = Depends(get_db)):
    try:
        counts = backup.restore(db, backup.prepare_restore(payload))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        # SQL 异常的参数可能包含备份内的 Cookie 或推送令牌。
        log.error("导入备份失败 (%s)", type(e).__name__)
        raise HTTPException(500, "恢复失败，请检查数据库是否可写并重试") from None
    log.info("导入备份完成:%s", counts)
    return {"ok": True, "imported": counts, "total": sum(counts.values()),
            "settings_applied": counts["settings"]}
