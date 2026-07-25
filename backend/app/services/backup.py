"""数据备份 / 迁移:把番剧库、订阅、剧集、下载记录、本地文件路径、BD 等整库导出为 JSON,
在另一实例(如全新发行部署)导入还原。

设计:
- 按 ORM 表列通用序列化(datetime→ISO、Enum→value、JSON 列原样),覆盖全部字段,加列也不漏。
- 保留原主键(目标库为空时无冲突;SQLite INTEGER PK = rowid,后续插入用 max+1,不会撞)。
- 导入 = 整表替换(先按外键逆序清空,再按顺序插回),适合「迁移到全新实例」。导入前校验格式,
  避免坏文件误清库。设置/通知默认不含(含 cookie/凭据/机器相关路径,跨机器慎用)。
- 文件本身不动:导入的是「虚拟库」记录;只要 NAS 文件仍在 download_root 下相同相对路径即可复现。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (Bangumi, BdExtra, BdRelease, Episode, NotificationConfig,
                        Subscription, Torrent, TorrentEpisode, VideoFile)

FORMAT = "mikannet-backup"
VERSION = 2

# 外键安全的插入顺序(父先于子);清空用逆序
DATA_MODELS = [Bangumi, Subscription, Episode, Torrent, TorrentEpisode,
               VideoFile, BdRelease, BdExtra]

# 设置迁移:导出 EDITABLE 的「生效值」(含 .env 配的,如 tmdb_api_key 也能带走),
# 但排除「本机/连接相关、由首次向导各机自配」的项,避免导入把新机的配置覆盖回旧机的。
_SETTING_DENYLIST = {
    "qb_host", "qb_port", "qb_username", "qb_password", "download_root", "proxy_url",
    "media_host_root", "bd_owned_host_root", "data_host_root", "powerdvd_path",
}


def _ser(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, enum.Enum):
        return v.value
    return v   # int/float/str/bool/None/list/dict(JSON 列)


def _row(obj) -> dict:
    return {c.name: _ser(getattr(obj, c.name)) for c in obj.__table__.columns}


def export_all(db: Session, include_settings: bool = False) -> dict:
    tables = {M.__tablename__: [_row(o) for o in db.execute(select(M)).scalars()]
              for M in DATA_MODELS}
    out: dict = {"format": FORMAT, "version": VERSION,
                 "exported_at": datetime.now(timezone.utc).isoformat(), "tables": tables}
    if include_settings:
        from app.config import settings
        from app.services import settings_service
        # 生效值(含 env 配的;含密钥原文,故备份文件本身要妥善保管)
        out["settings"] = {k: getattr(settings, k, None) for k in settings_service.EDITABLE
                           if k not in _SETTING_DENYLIST}
        tables["notification_config"] = [_row(o)
                                         for o in db.execute(select(NotificationConfig)).scalars()]
    out["counts"] = {**{t: len(r) for t, r in tables.items()},
                     "settings": len(out.get("settings") or {})}
    return out


def _deser(col, v):
    if v is None:
        return None
    t = col.type
    if isinstance(t, DateTime) and isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    if isinstance(t, SAEnum):
        ec = getattr(t, "enum_class", None)
        if ec is not None:
            try:
                return ec(v)            # 按 value 还原成员(存库时 SQLAlchemy 仍写成员名)
            except ValueError:
                try:
                    return ec[v]        # 兼容旧备份直接存了成员名
                except KeyError:
                    return None
    return v


def _insert_rows(db: Session, M, rows: list) -> int:
    cols = {c.name: c for c in M.__table__.columns}
    for r in rows:
        db.add(M(**{k: _deser(cols[k], v) for k, v in r.items() if k in cols}))
    db.flush()
    return len(rows)


def import_all(db: Session, data: dict) -> dict:
    """导入番剧库 + 通知配置(都在传入的 db 会话内,调用方 commit)。
    设置不在此处理:settings_service.update 会另开会话写库,与本会话未提交的写事务会在
    SQLite(单写者)上互锁死;故设置由调用方在 commit 之后用 apply_settings 应用。"""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("不是有效的 Mikannet 备份文件(format 不匹配)")
    tables = data.get("tables") or {}

    # 1) DATA:外键逆序清空 → 顺序插回(保留主键)
    for M in reversed(DATA_MODELS):
        db.execute(delete(M))
    db.flush()
    counts: dict[str, int] = {}
    for M in DATA_MODELS:
        counts[M.__tablename__] = _insert_rows(db, M, tables.get(M.__tablename__) or [])

    # 2) 通知配置(备份含才替换;不动 Setting 表)
    if "notification_config" in tables:
        db.execute(delete(NotificationConfig))
        db.flush()
        counts["notification_config"] = _insert_rows(db, NotificationConfig,
                                                      tables["notification_config"])
    return counts


def apply_settings(data: dict) -> int:
    """应用备份里的设置(合并 + 即时生效;不覆盖新机向导配的存储/连接项)。
    必须在 import_all 的 db.commit() 之后调用,避免与数据写事务在 SQLite 上互锁。返回应用条数。"""
    sett = data.get("settings") if isinstance(data, dict) else None
    if not sett:
        return 0
    from app.services import settings_service
    return len(settings_service.update(sett))
