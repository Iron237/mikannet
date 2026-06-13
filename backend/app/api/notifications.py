"""通知通道配置 CRUD + 测试推送。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.notifiers  # noqa: F401  导入即注册全部通道
from app.database import get_db
from app.models import NotificationConfig
from app.notifiers.base import EVENTS, Notification, create, known_channels

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_configs(db: Session = Depends(get_db)):
    rows = {c.channel: c for c in db.execute(select(NotificationConfig)).scalars().all()}
    out = []
    for ch in known_channels():
        c = rows.get(ch)
        out.append({
            "channel": ch,
            "enabled": c.enabled if c else False,
            "credentials": c.credentials if c else {},
            "events": {e: bool((c.events or {}).get(e, False)) if c else False for e in EVENTS},
            "use_proxy": c.use_proxy if c else (ch == "telegram"),
        })
    return out


@router.put("/{channel}")
def upsert(channel: str, payload: dict, db: Session = Depends(get_db)):
    if channel not in known_channels():
        raise HTTPException(404, f"未知通道: {channel}")
    c = db.execute(select(NotificationConfig).where(
        NotificationConfig.channel == channel)).scalar_one_or_none()
    if c is None:
        c = NotificationConfig(channel=channel)
        db.add(c)
    c.enabled = bool(payload.get("enabled", False))
    c.credentials = payload.get("credentials") or {}
    c.events = {e: bool((payload.get("events") or {}).get(e, False)) for e in EVENTS}
    c.use_proxy = bool(payload.get("use_proxy", False))
    db.commit()
    return {"ok": True}


@router.post("/{channel}/test")
def test_push(channel: str, db: Session = Depends(get_db)):
    """用当前已保存的配置发一条测试消息(同步,直接返回结果)。"""
    c = db.execute(select(NotificationConfig).where(
        NotificationConfig.channel == channel)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "通道未配置,先保存")
    try:
        create(channel, c.credentials or {}, c.use_proxy).send(Notification(
            event="on_new", title="Mikanarr 测试推送",
            message="如果你看到这条消息,说明通道配置成功 🎉"))
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"发送失败: {e}") from e
