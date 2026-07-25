"""自更新并发门控(docs/adr/0005 决策 11)。

两道闸:
- 应用更新**前**:若移文件操作(本地导入 / BD 导入 / 扫描)进行中 → 拒绝/延后(不可安全打断)。
- 应用更新**时**:置 `updating` 标记 → 中间件挡掉新的写请求(POST/PUT/PATCH/DELETE)。

外部下载器(qB/BitComet)里的下载跨重启继续,启动对账收拾其余,无需在此处理。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_lock = threading.Lock()
_file_ops = 0          # 显式登记的移文件操作计数
_updating = False      # 应用更新中


@contextmanager
def file_operation(name: str = ""):
    """长移文件操作用此包裹,期间拒绝自更新。"""
    global _file_ops
    with _lock:
        _file_ops += 1
    try:
        yield
    finally:
        with _lock:
            _file_ops -= 1


def busy_reason() -> str | None:
    """返回阻止更新的原因(移文件 / 导入进行中),否则 None。"""
    with _lock:
        if _file_ops > 0:
            return "有移文件操作正在进行,请稍后再更新"
    # 复用已知导入服务的运行标志(无需改动它们的代码)
    try:
        from app.services import local_import
        if local_import.state.get("running"):
            return "本地导入正在进行,请完成后再更新"
        if local_import.scan_state.get("running"):
            return "本地扫描正在进行,请完成后再更新"
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import bd_scan
        if bd_scan.state.get("running"):
            return "BD 扫描正在进行,请完成后再更新"
    except Exception:  # noqa: BLE001
        pass
    return None


def set_updating(v: bool) -> None:
    global _updating
    _updating = v


def is_updating() -> bool:
    return _updating
