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
        headers={"Content-Disposition": f'attachment; filename="mikanarr-backup-{stamp}.json"'})


@router.post("/import")
def import_data(payload: dict, include_settings: bool = False, db: Session = Depends(get_db)):
    """导入备份(整表替换,覆盖当前番剧库/订阅/文件记录)。payload = 备份 JSON 对象。"""
    try:
        counts = backup.import_all(db, payload, include_settings=include_settings)
        db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 — 失败回滚,保持当前库不被破坏
        db.rollback()
        log.exception("导入备份失败")
        raise HTTPException(500, f"导入失败(已回滚):{e}") from None

    # 导入了设置/通知 → 即时把 DB 覆盖灌进运行时 settings(否则要等重启才生效)
    if include_settings:
        try:
            from app.services import settings_service
            settings_service.load_overrides()
            from app.services import launch
            launch._token_cache = None   # 令牌可能随设置一起换了,清缓存
        except Exception as e:  # noqa: BLE001
            log.warning("导入后重载设置失败(重启可生效): %s", e)
    log.info("导入备份完成:%s", counts)
    return {"ok": True, "imported": counts, "total": sum(counts.values())}
