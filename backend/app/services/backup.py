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
                        Setting, Subscription, Torrent, TorrentEpisode, VideoFile)

FORMAT = "mikanarr-backup"
VERSION = 1

# 外键安全的插入顺序(父先于子);清空用逆序
DATA_MODELS = [Bangumi, Subscription, Episode, Torrent, TorrentEpisode,
               VideoFile, BdRelease, BdExtra]
CONFIG_MODELS = [NotificationConfig, Setting]   # 含 cookie/凭据/路径前缀,可选


def _ser(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, enum.Enum):
        return v.value
    return v   # int/float/str/bool/None/list/dict(JSON 列)


def _row(obj) -> dict:
    return {c.name: _ser(getattr(obj, c.name)) for c in obj.__table__.columns}


def export_all(db: Session, include_settings: bool = False) -> dict:
    models = DATA_MODELS + (CONFIG_MODELS if include_settings else [])
    tables = {M.__tablename__: [_row(o) for o in db.execute(select(M)).scalars()]
              for M in models}
    counts = {t: len(rows) for t, rows in tables.items()}
    return {"format": FORMAT, "version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts, "tables": tables}


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


def import_all(db: Session, data: dict, include_settings: bool = False) -> dict:
    """整表替换式导入。返回各表写入条数。调用方负责 commit。"""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("不是有效的 Mikanarr 备份文件(format 不匹配)")
    tables = data.get("tables") or {}
    models = DATA_MODELS + (CONFIG_MODELS if include_settings else [])

    # 1) 清空(外键逆序)。注意:include_settings=False 时不动设置/通知。
    for M in reversed(models):
        db.execute(delete(M))
    db.flush()

    # 2) 插回(外键顺序,保留主键)
    counts: dict[str, int] = {}
    for M in models:
        cols = {c.name: c for c in M.__table__.columns}
        rows = tables.get(M.__tablename__) or []
        n = 0
        for r in rows:
            kwargs = {k: _deser(cols[k], v) for k, v in r.items() if k in cols}
            db.add(M(**kwargs))
            n += 1
        db.flush()       # 逐表 flush:外键顺序落库,且尽早暴露问题
        counts[M.__tablename__] = n
    return counts
