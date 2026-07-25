"""日志:内存环形缓冲(WebUI 实时看)+ 文件持久化(重启时 gzip 归档上一份,全部保留)。"""
from __future__ import annotations

import gzip
import logging
import shutil
from collections import deque
from datetime import datetime, timezone

from app.config import settings

LOG_DIR = settings.data_dir / "logs"
RING_MAX = 2000
_ring: "deque[dict]" = deque(maxlen=RING_MAX)


class RingHandler(logging.Handler):
    """把日志记录存进内存环形缓冲,供 /api/logs 读取。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _ring.append({
                "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            })
        except Exception:  # noqa: BLE001 — 日志本身绝不能抛
            pass


def recent(level: str = "ALL", limit: int = 500) -> list[dict]:
    items = list(_ring)
    if level and level != "ALL":
        items = [x for x in items if x["level"] == level]
    return items[-limit:]


def archives() -> list[str]:
    if not LOG_DIR.exists():
        return []
    return sorted((p.name for p in LOG_DIR.glob("mikannet-*.log.gz")), reverse=True)


def _archive_previous() -> None:
    """启动时把上一次的 mikannet.log 压成 mikannet-<时间>.log.gz(全部保留,不删旧)。"""
    cur = LOG_DIR / "mikannet.log"
    if cur.exists() and cur.stat().st_size > 0:
        ts = datetime.fromtimestamp(cur.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
        gz = LOG_DIR / f"mikannet-{ts}.log.gz"
        try:
            with open(cur, "rb") as fin, gzip.open(gz, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            cur.unlink()
        except Exception:  # noqa: BLE001
            pass


def setup() -> None:
    """配置 root logger:文件 + 内存环 + 控制台。在应用最早期调用。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _archive_previous()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):       # 清掉可能已存在的 handler,避免重复
        root.removeHandler(h)
    fh = logging.FileHandler(LOG_DIR / "mikannet.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(RingHandler())
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
