"""运行时配置读写(设置页)。GET 返回生效值(密钥打码),PUT 部分更新并即时生效。"""
from fastapi import APIRouter

from app.services import settings_service

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    return settings_service.effective()


@router.put("")
def put_config(payload: dict):
    applied = settings_service.update(payload)
    return {"ok": True, "applied": list(applied.keys())}
