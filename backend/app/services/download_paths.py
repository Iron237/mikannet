"""下载根路径换址与项目改名(mikanarr → mikannet)迁移。

路径来自 Windows qB、Linux 容器和历史数据库，不能用当前运行平台的 ``Path``
做从属判断；这里统一按斜杠比较，并保留目标根原本的分隔符风格。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import settings

log = logging.getLogger(__name__)


def _norm(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if value == "/":
        return value
    return value.rstrip("/")


def relative_under(path: str, root: str) -> str | None:
    """返回 path 相对 root 的部分；Windows/UNC 路径比较不区分大小写。"""
    value = _norm(path)
    base = _norm(root)
    if not value or not base:
        return None
    if value.casefold() == base.casefold():
        return ""
    prefix = base + "/"
    if value.casefold().startswith(prefix.casefold()):
        return value[len(prefix):]
    return None


def _join(root: str, relative: str) -> str:
    base = str(root or "").rstrip("/\\")
    if not relative:
        return base
    sep = "\\" if "\\" in base else "/"
    suffix = relative.replace("\\", sep).replace("/", sep).lstrip(sep)
    return base + sep + suffix


def rebase_path(path: str, old_root: str, new_root: str) -> str | None:
    """path 位于 old_root 下时换到 new_root；不属于旧根则返回 None。"""
    relative = relative_under(path, old_root)
    return _join(new_root, relative) if relative is not None else None


def legacy_download_root(current_root: str) -> str | None:
    """当前根名为 mikannet 时，返回同级旧根 mikanarr。"""
    base = str(current_root or "").rstrip("/\\")
    split = max(base.rfind("/"), base.rfind("\\"))
    name = base[split + 1:]
    if name.casefold() != "mikannet":
        return None
    return base[:split + 1] + "mikanarr"


def rebase_subscription_paths(db, old_root: str, new_root: str) -> int:
    """把位于旧下载根下的所有订阅保存路径换到新根，保留番剧子目录。"""
    from app.models import Subscription

    changed = 0
    for sub in db.execute(select(Subscription)).scalars():
        new_path = rebase_path(sub.save_path, old_root, new_root)
        if new_path is not None and _norm(new_path).casefold() != _norm(sub.save_path).casefold():
            sub.save_path = new_path
            changed += 1
    if changed:
        db.flush()
    return changed


def migrate_legacy_subscription_paths() -> int:
    """启动迁移：项目改名后，把存量 mikanarr 订阅路径改到当前 mikannet 根。"""
    old_root = legacy_download_root(settings.download_root)
    if not old_root:
        return 0
    from app.database import db_session

    with db_session() as db:
        changed = rebase_subscription_paths(db, old_root, settings.download_root)
    if changed:
        log.info("下载根改名迁移：%s 个订阅 %s → %s",
                 changed, old_root, settings.download_root)
    return changed
