"""存储:首次向导配置的 NAS/SMB,由 App 在容器内自挂载到 download_root_local(/downloads)。

直接走内核 mount() 系统调用(ctypes),避开 mount.cifs setuid 助手在容器里的 capability 报错
(Unable to apply new capability set)。只需:容器 cap_add: SYS_ADMIN + 宿主内核有 cifs 模块
(Docker Desktop / 多数 Linux 自带)。设置存 DB(Setting),启动时灌进 settings 并按模式挂载。

storage_mode:
- ""     compose 托管(旧/dev:/downloads 由 compose 的 CIFS 卷/绑定提供,App 不挂)
- "smb"  App 用 mount() 把 //host/share 挂到 /downloads
- "local" /downloads 为容器内本地路径(由 compose 绑定提供),App 不挂
"""
from __future__ import annotations

import ctypes
import errno
import logging
import os
import tempfile

from app.config import settings
from app.database import db_session
from app.models import Setting

log = logging.getLogger(__name__)

_KEYS = ("storage_mode", "smb_host_path", "smb_username", "smb_password", "smb_vers", "setup_done")
_MS_RDONLY = 1
state: dict = {"mounted": False, "error": None}


def _libc():
    return ctypes.CDLL("libc.so.6", use_errno=True)


def load() -> None:
    """启动时把存储设置从 DB 灌进 settings 对象。"""
    with db_session() as db:
        for k in _KEYS:
            row = db.get(Setting, k)
            if row is None:
                continue
            v = (row.value or {}).get("v")
            setattr(settings, k, bool(v) if k == "setup_done" else (v if v is not None else ""))


def save(**kw) -> None:
    """持久化存储设置(只接受 _KEYS)并更新 settings。"""
    with db_session() as db:
        for k, v in kw.items():
            if k not in _KEYS:
                continue
            setattr(settings, k, v)
            row = db.get(Setting, k)
            if row is None:
                db.add(Setting(key=k, value={"v": v}))
            else:
                row.value = {"v": v}


def _mount_data(user: str, pwd: str, vers: str) -> str:
    # rw,容器内 root 身份,UTF-8。注意:password 含逗号会破坏选项串(v1 已知限制)。
    return (f"username={user},password={pwd},vers={vers or '3.0'},"
            "iocharset=utf8,uid=0,gid=0,file_mode=0664,dir_mode=0775")


def _mount(src: str, target: str, data: str, ro: bool = False) -> None:
    os.makedirs(target, exist_ok=True)
    r = _libc().mount(src.encode(), target.encode(), b"cifs",
                      _MS_RDONLY if ro else 0, data.encode())
    if r != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


_MNT_DETACH = 2   # umount2 懒卸载:僵尸/忙挂载也能从命名空间摘除


def _umount(target: str) -> None:
    try:
        # 懒卸载优先:SMB 断线留下的僵尸挂载普通 umount 会 EBUSY/ESTALE 失败
        if _libc().umount2(target.encode(), _MNT_DETACH) != 0:
            _libc().umount(target.encode())
    except Exception:  # noqa: BLE001
        pass


def _proc_mounted(target: str) -> bool:
    """/proc/mounts 是否记录了该挂载点 —— 对僵尸挂载也为真(os.path.ismount 会漏判)。"""
    try:
        with open("/proc/mounts") as f:
            return any(line.split()[1] == target for line in f if len(line.split()) > 1)
    except OSError:
        return False


def is_mounted(target: str | None = None) -> bool:
    """挂载点是否**活着可用**:僵尸挂载(stale file handle)对 os.path.ismount 为 False,即视为不可用。"""
    t = target or str(settings.download_root_local)
    try:
        return os.path.ismount(t)
    except OSError:
        return False


def test(mode: str, host_path: str, username: str, password: str, vers: str) -> dict:
    """不影响 /downloads:smb 挂到临时点验证读写后卸载;local 直接验证 download_root_local 读写。"""
    if mode != "smb":
        p = str(settings.download_root_local)
        try:
            os.makedirs(p, exist_ok=True)
            t = os.path.join(p, ".mikannet_wtest")
            with open(t, "w"):
                pass
            os.remove(t)
            return {"ok": True, "writable": True, "sample": sorted(os.listdir(p))[:8]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"本地路径不可写: {e}"}
    if not host_path:
        return {"ok": False, "error": "请填写 SMB 共享地址(//主机/共享名)"}
    tmp = tempfile.mkdtemp(prefix="smbtest_")
    try:
        _mount(host_path, tmp, _mount_data(username, password, vers))
    except OSError as e:
        _safe_rmdir(tmp)
        return {"ok": False, "error": f"挂载失败: {e.strerror} (errno {e.errno})"}
    try:
        sample = sorted(os.listdir(tmp))[:8]
        writable, werr = True, None
        try:
            t = os.path.join(tmp, ".mikannet_wtest")
            with open(t, "w"):
                pass
            os.remove(t)
        except Exception as e:  # noqa: BLE001
            writable, werr = False, str(e)
        return {"ok": True, "writable": writable, "write_error": werr, "sample": sample}
    finally:
        _umount(tmp)
        _safe_rmdir(tmp)


def _safe_rmdir(p: str) -> None:
    try:
        os.rmdir(p)
    except OSError:
        pass


def apply() -> dict:
    """按 storage_mode 把 download_root_local 挂好(smb)。启动 & 改配置后调用。返回 state。"""
    target = str(settings.download_root_local)
    state["error"] = None
    if settings.storage_mode == "smb":
        if not settings.smb_host_path:
            state.update(mounted=False, error="SMB 未配置")
            return state
        # 先清理任何已存在挂载:含 SMB 断线留下的僵尸挂载(os.path.ismount 漏判,故也查 /proc/mounts),
        # 否则 mount() 会因目标已有挂载记录而报 EEXIST(File exists)。
        if is_mounted(target) or _proc_mounted(target):
            _umount(target)
        data = _mount_data(settings.smb_username, settings.smb_password, settings.smb_vers)
        err = None
        for attempt in (1, 2):
            try:
                _mount(settings.smb_host_path, target, data)   # //host/share → /downloads
                err = None
                break
            except OSError as e:
                err = e
                # 僵尸挂载没摘净 → 再懒卸载一次后重试(EEXIST=残留挂载 / EBUSY=忙)
                if attempt == 1 and e.errno in (errno.EEXIST, errno.EBUSY):
                    _umount(target)
                    continue
                break
        if err is None:
            state.update(mounted=True, error=None)
            log.info("SMB 已挂载 %s → %s", settings.smb_host_path, target)
        else:
            state.update(mounted=False, error=f"挂载失败: {err.strerror} (errno {err.errno})")
            log.warning("SMB 挂载失败 %s: %s", settings.smb_host_path, state["error"])
    else:
        state.update(mounted=is_mounted(target))   # local/compose:不由 App 挂载
    return state


def ensure_at_startup() -> None:
    """lifespan 调用:加载存储设置,smb 模式则挂载(失败不阻塞启动)。"""
    try:
        load()
        if settings.storage_mode == "smb" and settings.smb_host_path:
            apply()
    except Exception as e:  # noqa: BLE001
        log.warning("存储启动挂载异常: %s", e)


def status() -> dict:
    """当前存储状态(密码打码),供向导/设置页展示。"""
    return {
        "mode": settings.storage_mode or "",
        "smb_host_path": settings.smb_host_path or "",
        "smb_username": settings.smb_username or "",
        "smb_vers": settings.smb_vers or "3.0",
        "smb_password_set": bool(settings.smb_password),
        "mounted": is_mounted(),
        "error": state.get("error"),
        "download_root_local": str(settings.download_root_local),
    }
