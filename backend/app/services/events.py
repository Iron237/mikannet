"""事件总线:状态迁移 → 通知,后台线程发送,失败只记日志(不阻塞主流程)。"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import NotificationConfig, Torrent
import app.notifiers  # noqa: F401  导入即注册全部通道
from app.notifiers.base import Notification, create

log = logging.getLogger(__name__)


def _send_all(n: Notification) -> None:
    with db_session() as db:
        configs = db.execute(select(NotificationConfig).where(
            NotificationConfig.enabled)).scalars().all()
        rows = [(c.channel, dict(c.credentials or {}), bool(c.use_proxy),
                 dict(c.events or {})) for c in configs]
    for channel, creds, use_proxy, events in rows:
        if not events.get(n.event, False):
            continue
        try:
            create(channel, creds, use_proxy).send(n)
            log.info("通知已发送 [%s] %s %s", channel, n.event, n.title)
        except Exception as e:  # noqa: BLE001
            log.warning("通知发送失败 [%s] %s: %s", channel, n.event, e)


def emit(event: str, torrent: Torrent) -> None:
    """从状态机各迁移点调用。读取所需字段后异步发送。"""
    try:
        sub = torrent.subscription
        bangumi = sub.bangumi
        parsed = torrent.parsed_json or {}
        eps = parsed.get("episodes") or []
        if torrent.is_batch and eps:
            ep_text = f"第 {eps[0]:g}-{eps[-1]:g} 话(合集)"
        elif eps:
            ep_text = "第 " + ", ".join(f"{e:g}" for e in eps) + " 话"
        else:
            ep_text = ""
        version = f" v{torrent.version}" if torrent.version > 1 else ""
        lines = [f"{ep_text}{version}".strip(), f"字幕组:{sub.subgroup_name or sub.mikan_subgroup_id}"]
        if event == "on_fail" and torrent.error_message:
            lines.append(f"错误:{torrent.error_message}")
        poster = None
        if bangumi.poster_path:
            p = settings.data_dir / bangumi.poster_path
            if p.exists():
                poster = str(p)
        n = Notification(event=event, title=bangumi.title,
                         message="\n".join(l for l in lines if l), poster_path=poster)
    except Exception:  # noqa: BLE001 — 通知组装失败绝不影响主流程
        log.exception("通知组装失败")
        return
    threading.Thread(target=_send_all, args=(n,), daemon=True).start()
