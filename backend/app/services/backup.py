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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import get_args, get_type_hints

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.database import Base
from app.services import settings_service
from app.models import (AutoScanLog, Bangumi, BdExtra, BdRelease, Episode, EpisodeType, NotificationConfig,
                        Subscription, Torrent, TorrentEpisode, VideoFile)

FORMAT = "mikannet-backup"
VERSION = 2

# 外键安全的插入顺序(父先于子);清空用逆序
DATA_MODELS = [Bangumi, AutoScanLog, Subscription, Episode, Torrent, TorrentEpisode,
               VideoFile, BdRelease, BdExtra]

# 设置迁移:导出 EDITABLE 的「生效值」(含 .env 配的,如 tmdb_api_key 也能带走),
# 但排除「本机/连接相关、由首次向导各机自配」的项,避免导入把新机的配置覆盖回旧机的。
_SETTING_DENYLIST = {
    "qb_host", "qb_port", "qb_username", "qb_password", "download_root", "proxy_url",
    "bitcomet_host", "bitcomet_port", "bitcomet_username", "bitcomet_password",
    "bitcomet_download_root",
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
        # 生效值(含 env 配的;含密钥原文,故备份文件本身要妥善保管)
        out["settings"] = {k: getattr(settings, k, None) for k in settings_service.EDITABLE
                           if k not in _SETTING_DENYLIST}
        tables["notification_config"] = [_row(o)
                                         for o in db.execute(select(NotificationConfig)).scalars()]
    out["counts"] = {**{t: len(r) for t, r in tables.items()},
                     "settings": len(out.get("settings") or {})}
    return out


def _deser(col, v, json_types):
    if v is None:
        return None
    t = col.type
    if isinstance(t, DateTime):
        return datetime.fromisoformat(v)
    if isinstance(t, SAEnum):
        ec = getattr(t, "enum_class", None)
        if ec is not None:
            try:
                return ec(v)            # 按 value 还原成员(存库时 SQLAlchemy 仍写成员名)
            except ValueError:
                try:
                    return ec[v]        # 兼容旧备份直接存了成员名
                except KeyError:
                    if ec is EpisodeType:
                        return {"EP": ec.REGULAR, "SP": ec.SPECIAL, "MOVIE": ec.SPECIAL,
                                "OVA": ec.SPECIAL}[str(v).upper()]
                    raise
    if isinstance(t, Boolean) and (type(v) not in (int, bool) or v not in (0, 1)):
        raise ValueError("无效布尔值")
    if isinstance(t, Integer) and (type(v) is not int or not -(2**63) <= v < 2**63):
        raise ValueError("无效整数")
    if isinstance(t, Float) and type(v) not in (int, float):
        raise ValueError("无效数值")
    if isinstance(t, String) and not isinstance(v, str):
        raise ValueError("无效文本")
    if col.name in json_types and not isinstance(v, json_types[col.name]):
        raise ValueError("JSON 字段结构无效")
    return v


@dataclass
class RestorePlan:
    tables: dict
    settings: dict
    version: int

    @property
    def models(self):
        return [*DATA_MODELS, NotificationConfig] if "notification_config" in self.tables else DATA_MODELS


def prepare_restore(data: dict) -> RestorePlan:
    if data.get("format") not in (FORMAT, "mikanarr-backup"):
        raise ValueError("请选择 Mikannet 或 MikanArr 导出的备份文件")
    version = data.get("version")
    if type(version) is not int or version not in (1, VERSION):
        raise ValueError("无法识别备份版本，请使用兼容的 Mikannet 版本恢复")
    tables = data.get("tables")
    # v0.1.22 加入扫描历史，之前导出的 v2 与 v1 均无此表。
    required = {model.__tablename__ for model in DATA_MODELS if model is not AutoScanLog}
    if not isinstance(tables, dict) or required.difference(tables):
        raise ValueError("备份缺少必要的数据表，无法恢复完整番剧库")
    known = required | {"auto_scan_log", "notification_config"}
    if version == 1:
        known.add("setting")
    if tables.keys() - known:
        raise ValueError("备份包含无法识别的数据表，请核对导出版本")
    if any(not isinstance(rows, list) for rows in tables.values()):
        raise ValueError("备份中的数据表必须是记录列表")

    raw_settings = data.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise ValueError("备份设置格式无效")
    declared = data.get("counts", {})
    actual = {name: len(rows) for name, rows in tables.items()}
    actual["settings"] = len(raw_settings)
    if not isinstance(declared, dict) or any(
            type(n) is not int or actual.get(name) != n for name, n in declared.items()):
        raise ValueError("备份的记录数量和清单不一致，请重新选择完整文件")
    if "counts" in data and tables.keys() - declared.keys():
        raise ValueError("备份记录数量清单不完整")
    if version == 1 and "setting" in tables:
        try:
            raw_settings = {r["key"]: r["value"]["v"] for r in tables["setting"]}
        except (KeyError, TypeError):
            raise ValueError("旧版备份的设置记录无效") from None
    allowed = {k: v for k, v in raw_settings.items()
               if k in settings_service.EDITABLE and k not in _SETTING_DENYLIST}
    for key, value in allowed.items():
        _, typ, secret = settings_service.EDITABLE[key]
        if type(value) is not typ and not (secret and value is None):
            raise ValueError(f"备份设置 {key} 类型无效")
    return RestorePlan(tables, settings_service.normalize_changes(allowed), version)


def _load_rows(db: Session, plan: RestorePlan) -> dict:
    counts = {}
    for model in plan.models:
        name = model.__tablename__
        cols = model.__table__.columns
        # JSON 列在 SQLite 中接受任意 JSON；应用按 ORM 注解依赖字典或列表。
        hints = get_type_hints(model)
        json_types = {c.name: get_args(hints[c.name])[0] for c in cols if isinstance(c.type, JSON)}
        rows = plan.tables.get(name, [])
        keys = [c.name for c in model.__table__.primary_key]
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict) or any(type(row.get(k)) is not int for k in keys):
                raise ValueError(f"{name} 第 {i} 条记录缺少有效主键")
            try:
                values = {k: _deser(cols[k], v, json_types) for k, v in row.items() if k in cols}
                if model is Bangumi and "auto_download_disabled" not in values:
                    values["auto_download_disabled"] = bool(values.get("bd_owned"))
                db.add(model(**values))
            except (KeyError, ValueError, TypeError):
                raise ValueError(f"{name} 第 {i} 条记录包含无效字段值") from None
        try:
            db.flush()
        except IntegrityError:
            raise ValueError(f"{name} 存在重复记录、无效引用或缺少必要字段") from None
        except DBAPIError:
            raise
        except StatementError:
            raise ValueError(f"{name} 包含无效字段值") from None
        counts[name] = len(rows)
    return counts


def preview_restore(db: Session, plan: RestorePlan) -> dict:
    """在独立的内存库中执行相同的入库与外键检查，只读取现有库的行数。"""
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            Base.metadata.create_all(conn)
            with Session(conn) as scratch:
                incoming = _load_rows(scratch, plan)
    finally:
        engine.dispose()
    current = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in plan.models}
    return {"version": plan.version, "incoming": incoming, "current": current,
            "settings_count": len(plan.settings)}


def restore(db: Session, plan: RestorePlan) -> dict:
    """库记录、通知与设置一起提交成功后才更新进程内配置。"""
    with db.begin():
        for model in reversed(plan.models):
            db.execute(delete(model))
        counts = _load_rows(db, plan)
        settings_service.persist(db, plan.settings)
    settings_service.activate(plan.settings)
    counts["settings"] = len(plan.settings)
    return counts
