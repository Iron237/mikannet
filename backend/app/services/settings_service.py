"""运行时配置:Setting(DB)覆盖 .env 默认,改完即时生效(带 apply 钩子)。

启动时把 DB 覆盖灌进全局 settings 对象,之后各处照常读 settings.<field> 即可拿到生效值。
NAS 挂载凭据不在此列(那是 Docker 卷参数,只能改 .env/compose)。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Setting

log = logging.getLogger(__name__)

SECRET_MASK = "********"

# key -> (分组, 类型, 是否密钥)
EDITABLE: dict[str, tuple[str, type, bool]] = {
    # 常规
    "poll_interval_min": ("常规", int, False),
    "tmdb_api_key": ("常规", str, True),
    # 下载器
    "downloader": ("下载器", str, False),
    "qb_host": ("下载器", str, False),
    "qb_port": ("下载器", int, False),
    "qb_username": ("下载器", str, False),
    "qb_password": ("下载器", str, True),
    "download_root": ("下载器", str, False),
    # 代理
    "proxy_url": ("代理", str, False),
    # 搜索源
    "mikan_base_url": ("搜索源", str, False),
    "mikan_cookie": ("搜索源", str, True),
    "nyaa_base_url": ("搜索源", str, False),
    "dmhy_base_url": ("搜索源", str, False),
    # 整理(Jellyfin)
    "organize_enabled": ("整理", bool, False),
    "nfo_enabled": ("整理", bool, False),
    # 坏种清理
    "dead_torrent_enabled": ("坏种清理", bool, False),
    "dead_torrent_hours": ("坏种清理", int, False),
    "stall_pause_enabled": ("坏种清理", bool, False),
    "stall_pause_hours": ("坏种清理", int, False),
    # 智能下载偏好
    "auto_dl_resolution": ("智能下载", str, False),
    "auto_dl_sub_lang": ("智能下载", str, False),
    "auto_dl_prefer_bd": ("智能下载", bool, False),
    "auto_dl_interval_min": ("智能下载", int, False),
    # 原生启动(协议头播放/打开)
    "media_host_root": ("播放", str, False),
    "bd_owned_host_root": ("播放", str, False),
    "powerdvd_path": ("播放", str, False),
    # AniDB(剧集级元数据)
    "anidb_enabled": ("AniDB", bool, False),
    "anidb_client_name": ("AniDB", str, False),
    "anidb_client_ver": ("AniDB", int, False),
    "anidb_search_base": ("AniDB", str, False),
    "anidb_lang": ("AniDB", str, False),
    # LLM 兜底
    "llm_enabled": ("LLM", bool, False),
    "llm_base_url": ("LLM", str, False),
    "llm_api_key": ("LLM", str, True),
    "llm_model": ("LLM", str, False),
}


def _coerce(typ: type, v):
    if typ is bool:
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
    if typ is int:
        return int(v)
    return str(v)


def load_overrides() -> None:
    """启动时把 DB 里的覆盖值灌进 settings 对象。"""
    with db_session() as db:
        rows = db.execute(select(Setting)).scalars().all()
    n = 0
    for r in rows:
        if r.key in EDITABLE:
            try:
                setattr(settings, r.key, _coerce(EDITABLE[r.key][1], (r.value or {}).get("v")))
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("加载设置 %s 失败: %s", r.key, e)
    if n:
        log.info("已加载 %s 项 DB 设置覆盖", n)


def effective() -> dict:
    """供 WebUI 读取:当前生效值(密钥打码)。"""
    out = {}
    for key, (group, typ, secret) in EDITABLE.items():
        v = getattr(settings, key, None)
        if secret:
            v = SECRET_MASK if v else ""
        out[key] = {"value": v, "group": group, "type": typ.__name__, "secret": secret}
    return out


def update(changes: dict) -> dict:
    """写入更改 → 灌进 settings → 持久化 Setting → 跑 apply 钩子。返回实际生效的项。"""
    applied: dict = {}
    with db_session() as db:
        for key, raw in changes.items():
            if key not in EDITABLE:
                continue
            _group, typ, secret = EDITABLE[key]
            if secret and (raw == SECRET_MASK or raw is None):
                continue   # 密钥未改(仍是打码占位)→ 跳过,保留原值
            try:
                val = _coerce(typ, raw)
            except Exception as e:  # noqa: BLE001
                log.warning("设置 %s 取值失败: %s", key, e)
                continue
            setattr(settings, key, val)
            applied[key] = val
            row = db.get(Setting, key)
            if row is None:
                db.add(Setting(key=key, value={"v": val}))
            else:
                row.value = {"v": val}
    _apply(applied)
    return applied


def _apply(applied: dict) -> None:
    """对需要动作的项执行即时生效。"""
    if "poll_interval_min" in applied:
        try:
            from app.scheduler import reschedule
            reschedule(applied["poll_interval_min"])
        except Exception as e:  # noqa: BLE001
            log.warning("重排 RSS 轮询失败: %s", e)
    if "auto_dl_interval_min" in applied:
        try:
            from app.scheduler import reschedule_auto_best
            reschedule_auto_best(applied["auto_dl_interval_min"])
        except Exception as e:  # noqa: BLE001
            log.warning("重排智能扫描失败: %s", e)
    if any(k.startswith("qb_") or k == "downloader" for k in applied):
        try:
            from app.clients.qbittorrent import qb_client
            qb_client._client = None        # 下次用时按新配置重连
        except Exception:  # noqa: BLE001
            pass
    if any(k.startswith("bitcomet_") or k == "downloader" for k in applied):
        try:
            from app.clients.bitcomet import bitcomet_client
            bitcomet_client._token = None
        except Exception:  # noqa: BLE001
            pass
