"""存储挂载:SMB 断线留下的僵尸挂载导致 EEXIST → apply() 清理后重试恢复。"""
import errno
import os

from app.config import settings
from app.services import storage


def _smb(monkeypatch):
    monkeypatch.setattr(settings, "storage_mode", "smb")
    monkeypatch.setattr(settings, "smb_host_path", "//192.168.2.4/share")
    monkeypatch.setattr(settings, "smb_username", "u")
    monkeypatch.setattr(settings, "smb_password", "p")
    monkeypatch.setattr(settings, "smb_vers", "3.0")


def test_apply_recovers_from_stale_mount(monkeypatch):
    """僵尸挂载:ismount=False 但 /proc/mounts 有记录 → 首次 mount EEXIST → 清理后重试成功。"""
    _smb(monkeypatch)
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: False)
    monkeypatch.setattr(storage, "_proc_mounted", lambda t: True)
    umounts = []
    monkeypatch.setattr(storage, "_umount", lambda t: umounts.append(t))
    n = {"c": 0}

    def fake_mount(src, target, data, ro=False):
        n["c"] += 1
        if n["c"] == 1:
            raise OSError(errno.EEXIST, os.strerror(errno.EEXIST))

    monkeypatch.setattr(storage, "_mount", fake_mount)

    st = storage.apply()
    assert st["mounted"] is True and st["error"] is None
    assert n["c"] == 2            # 首次 EEXIST → 重试成功
    assert len(umounts) >= 1      # 僵尸挂载被清理(启动前 + EEXIST 后)


def test_apply_clean_first_mount(monkeypatch):
    """无残留挂载:一次成功,不做多余卸载。"""
    _smb(monkeypatch)
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: False)
    monkeypatch.setattr(storage, "_proc_mounted", lambda t: False)
    umounts = []
    monkeypatch.setattr(storage, "_umount", lambda t: umounts.append(t))
    monkeypatch.setattr(storage, "_mount", lambda *a, **k: None)

    st = storage.apply()
    assert st["mounted"] is True
    assert umounts == []


def test_apply_persistent_eexist_fails_gracefully(monkeypatch):
    """始终 EEXIST(清理不掉)→ 优雅失败,报错带 errno,不抛异常。"""
    _smb(monkeypatch)
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: False)
    monkeypatch.setattr(storage, "_proc_mounted", lambda t: True)
    monkeypatch.setattr(storage, "_umount", lambda t: None)

    def always_eexist(src, target, data, ro=False):
        raise OSError(errno.EEXIST, os.strerror(errno.EEXIST))

    monkeypatch.setattr(storage, "_mount", always_eexist)

    st = storage.apply()
    assert st["mounted"] is False
    assert "17" in (st["error"] or "")


def test_watchdog_remounts_when_stale(monkeypatch):
    """看门狗:未正常挂载(含僵尸)→ 调 apply() 重挂。"""
    from app import scheduler
    _smb(monkeypatch)
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: False)
    calls = {"n": 0}
    monkeypatch.setattr(storage, "apply",
                        lambda: (calls.__setitem__("n", calls["n"] + 1), {"mounted": True})[1])
    scheduler._storage_watchdog_job()
    assert calls["n"] == 1


def test_watchdog_skips_when_healthy(monkeypatch):
    """看门狗:挂载健康 → 不动。"""
    from app import scheduler
    _smb(monkeypatch)
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: True)
    calls = {"n": 0}
    monkeypatch.setattr(storage, "apply", lambda: calls.__setitem__("n", calls["n"] + 1))
    scheduler._storage_watchdog_job()
    assert calls["n"] == 0


def test_windows_and_unc_paths_are_normalized():
    assert storage.normalize_windows_path("d:/Anime/Mikannet/") == "D:\\Anime\\Mikannet"
    assert storage.normalize_windows_path("//nas/anime/mikannet") == "\\\\nas\\anime\\mikannet"
    assert storage.normalize_windows_path(r"G:\\Anime\\Mikannet") == r"G:\Anime\Mikannet"
    assert storage.normalize_windows_path("C:/") == "C:\\"
    assert storage.normalize_smb_path("\\\\nas\\anime\\mikannet\\") == "//nas/anime/mikannet"


def test_nfs_apply_uses_nfs_filesystem(monkeypatch):
    monkeypatch.setattr(settings, "storage_mode", "nfs")
    monkeypatch.setattr(settings, "nfs_host_path", "nas:/volume1/anime")
    monkeypatch.setattr(settings, "nfs_options", "vers=4,soft")
    monkeypatch.setattr(storage, "is_mounted", lambda t=None: False)
    monkeypatch.setattr(storage, "_proc_mounted", lambda t: False)
    calls = []

    def fake_mount(src, target, data, ro=False, fstype="cifs"):
        calls.append((src, data, fstype))

    monkeypatch.setattr(storage, "_mount", fake_mount)
    st = storage.apply()
    assert st["mounted"] is True
    assert calls == [("nas:/volume1/anime", "vers=4,soft", "nfs")]
