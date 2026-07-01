"""首次配置向导:存储(SMB/本地)→ 下载器/代理/元数据(复用 /api/config)→ 完成。

未配置时前端路由守卫把用户引到 /setup;完成后置 setup_done 并放行进主页。
"配置过"= setup_done 为真,或库里已有番剧(老实例不被向导劫持)。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Bangumi
from app.services import storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    has_data = db.execute(select(Bangumi.id).limit(1)).first() is not None
    return {
        "configured": bool(settings.setup_done) or has_data,
        "setup_done": bool(settings.setup_done),
        "has_data": has_data,
        "storage": storage.status(),
    }


@router.get("/storage")
def get_storage():
    return storage.status()


@router.post("/storage/test")
def test_storage(payload: dict):
    """仅测试,不持久化(挂临时点验证后卸载)。"""
    mode = (payload.get("mode") or "smb").strip()
    return storage.test(mode, (payload.get("smb_host_path") or "").strip(),
                        (payload.get("smb_username") or "").strip(),
                        payload.get("smb_password") or "",
                        (payload.get("smb_vers") or "3.0").strip())


@router.post("/storage")
def save_storage(payload: dict):
    """保存存储设置并立即(重新)挂载。密码留空(占位)则保留原值。返回挂载结果。"""
    mode = (payload.get("mode") or "").strip()
    if mode not in ("smb", "local"):
        raise HTTPException(400, "mode 必须是 smb 或 local")
    fields = {"storage_mode": mode}
    if mode == "smb":
        fields["smb_host_path"] = (payload.get("smb_host_path") or "").strip()
        fields["smb_username"] = (payload.get("smb_username") or "").strip()
        fields["smb_vers"] = (payload.get("smb_vers") or "3.0").strip()
        pwd = payload.get("smb_password")
        if pwd not in (None, "", "********"):   # 占位/留空 → 保留原密码
            fields["smb_password"] = pwd
    storage.save(**fields)
    result = storage.apply()
    if mode == "smb" and not result.get("mounted"):
        raise HTTPException(400, result.get("error") or "挂载失败")
    return {"ok": True, **storage.status()}


@router.post("/storage/remount")
def remount_storage():
    """按现有配置重新挂载(不改配置)。SMB 断线留下僵尸挂载导致「连不上」时,一键恢复。"""
    if settings.storage_mode != "smb":
        return {"ok": True, **storage.status()}
    result = storage.apply()
    if not result.get("mounted"):
        raise HTTPException(400, result.get("error") or "挂载失败")
    return {"ok": True, **storage.status()}


@router.post("/finish")
def finish():
    """标记首次配置完成,放行进主页。"""
    storage.save(setup_done=True)
    return {"ok": True}
