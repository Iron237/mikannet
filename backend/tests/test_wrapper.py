"""PID-1 wrapper(docker/wrapper.py)纯逻辑回归:semver、播种、重播种、自愈回滚决策。

symlink 创建在 Windows 需开发者模式/管理员;无权限平台自动跳过 symlink 相关用例
(真实验证在 Linux 容器冒烟里)。ver_key 不依赖 symlink,始终跑。
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

WRAPPER_PY = Path(__file__).resolve().parents[2] / "docker" / "wrapper.py"


def _symlinks_ok(tmp: Path) -> bool:
    """探测 point_current 真正需要的能力:原子替换一个已存在的目录 symlink。

    Windows 即便开了开发者模式可建 symlink,os.replace 覆盖已存在的目录 symlink 仍报
    WinError 5(生产是 Linux 容器,该操作原子且合法)→ 此处探到则跳过,留给 Linux CI/容器跑。
    """
    try:
        a, b = tmp / "_a", tmp / "_b"
        a.mkdir()
        b.mkdir()
        link, t = tmp / "_l", tmp / "_t"
        os.symlink(a, link)
        os.symlink(b, t)
        os.replace(t, link)                       # Windows 在此抛 PermissionError
        ok = os.path.realpath(link) == os.path.realpath(b)
        link.unlink()
        a.rmdir()
        b.rmdir()
        return ok
    except (OSError, NotImplementedError):
        return False


def load_wrapper(code_dir: Path, baked_dir: Path, version: str):
    os.environ["MIKANARR_CODE_DIR"] = str(code_dir)
    os.environ["MIKANARR_BAKED_DIR"] = str(baked_dir)
    os.environ["MIKANARR_VERSION"] = version
    spec = importlib.util.spec_from_file_location(
        f"wrapper_{version}_{abs(hash(str(code_dir)))}", WRAPPER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_baked(baked_dir: Path) -> None:
    app = baked_dir / "backend" / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text("# app\n")
    dist = baked_dir / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")


def test_ver_key_ordering(tmp_path):
    w = load_wrapper(tmp_path / "c", tmp_path / "b", "0.1.0")
    assert w.ver_key("0.1.1") > w.ver_key("0.1.0")
    assert w.ver_key("0.2.0") > w.ver_key("0.1.9")
    assert w.ver_key("1.0.0") > w.ver_key("0.9.9")
    # 正式版 > 同核心预发布;预发布之间按标识符排序
    assert w.ver_key("0.1.0") > w.ver_key("0.1.0-rc.1")
    assert w.ver_key("0.1.0-rc.2") > w.ver_key("0.1.0-rc.1")
    assert w.ver_key("0.1.0") == w.ver_key("v0.1.0")


def test_start_app_injects_current_version(tmp_path, monkeypatch):
    """子进程的 MIKANARR_VERSION 必须 = current 实际版本(rel.name),而非镜像烤死 env。

    回归:纯代码更新只换卷代码不换镜像;若沿用镜像 env 的旧版本号,/version 永远报旧值,
    导致检查更新死循环 + 前端「等待新版本」永久卡住。不依赖 symlink,始终跑。
    """
    code, baked = tmp_path / "code", tmp_path / "baked"
    make_baked(baked)
    w = load_wrapper(code, baked, "0.1.0")            # 镜像烤死版本 = 0.1.0
    rel = code / "releases" / "0.1.3"                  # current 指向的纯代码新版
    (rel / "backend" / "app").mkdir(parents=True)
    (rel / "backend" / "app" / "main.py").write_text("# v013\n")
    captured = {}

    class FakePopen:
        def __init__(self, cmd, cwd=None, env=None):
            captured["env"] = env

    monkeypatch.setattr(w.subprocess, "Popen", FakePopen)
    w.start_app(rel)
    assert captured["env"]["MIKANARR_VERSION"] == "0.1.3"   # 跟随 current,非烤死 0.1.0


def test_first_seed(tmp_path):
    if not _symlinks_ok(tmp_path):
        pytest.skip("symlink 无权限(非 Linux/未开开发者模式)")
    code, baked = tmp_path / "code", tmp_path / "baked"
    make_baked(baked)
    w = load_wrapper(code, baked, "0.1.0")
    w.ensure_seeded()
    assert w.current_version() == "0.1.0"
    assert (code / "releases" / "0.1.0" / "backend" / "app" / "main.py").exists()
    assert (code / "current" / "frontend" / "dist" / "index.html").exists()


def test_reseed_when_baked_newer(tmp_path):
    if not _symlinks_ok(tmp_path):
        pytest.skip("symlink 无权限")
    code = tmp_path / "code"
    baked_old, baked_new = tmp_path / "b010", tmp_path / "b011"
    make_baked(baked_old)
    make_baked(baked_new)
    # 先以 0.1.0 播种
    load_wrapper(code, baked_old, "0.1.0").ensure_seeded()
    # 镜像换成 0.1.1(完整更新后)→ 启动应重播种并重指
    w = load_wrapper(code, baked_new, "0.1.1")
    w.ensure_seeded()
    assert w.current_version() == "0.1.1"
    assert (code / "releases" / "0.1.1" / "backend" / "app" / "main.py").exists()


def test_no_downgrade_when_baked_older(tmp_path):
    if not _symlinks_ok(tmp_path):
        pytest.skip("symlink 无权限")
    code = tmp_path / "code"
    baked = tmp_path / "baked"
    make_baked(baked)
    # 卷里已有更新的纯代码版本 0.1.2 且为 current
    w = load_wrapper(code, baked, "0.1.0")
    (code / "releases" / "0.1.2" / "backend" / "app").mkdir(parents=True)
    (code / "releases" / "0.1.2" / "backend" / "app" / "main.py").write_text("# v012\n")
    w.point_current("0.1.2")
    # 容器重启(镜像仍 0.1.0):不得降级
    w.ensure_seeded()
    assert w.current_version() == "0.1.2"


def test_rollback_after_k_attempts(tmp_path):
    if not _symlinks_ok(tmp_path):
        pytest.skip("symlink 无权限")
    code, baked = tmp_path / "code", tmp_path / "baked"
    make_baked(baked)
    w = load_wrapper(code, baked, "0.1.0")
    w.ensure_seeded()                       # current = 0.1.0 (prev good)
    # 落一个坏的新版本 0.1.1(目录有效,但模拟启动不健康)
    (code / "releases" / "0.1.1" / "backend" / "app").mkdir(parents=True)
    (code / "releases" / "0.1.1" / "backend" / "app" / "main.py").write_text("# bad\n")
    w.point_current("0.1.1")
    w.save_pending({"prev": "0.1.0", "new": "0.1.1", "attempts": 0})
    # 前 K-1 次仍重试新版,不回滚
    for _ in range(w.ROLLBACK_K - 1):
        w.handle_unhealthy("0.1.1")
        assert w.current_version() == "0.1.1"
    # 第 K 次 → 回滚到 prev 并清 pending
    w.handle_unhealthy("0.1.1")
    assert w.current_version() == "0.1.0"
    assert not w.PENDING.exists()


def test_healthy_confirm_clears_pending(tmp_path):
    if not _symlinks_ok(tmp_path):
        pytest.skip("symlink 无权限")
    code, baked = tmp_path / "code", tmp_path / "baked"
    make_baked(baked)
    w = load_wrapper(code, baked, "0.1.0")
    w.ensure_seeded()
    w.save_pending({"prev": "0.1.0", "new": "0.1.1", "attempts": 1})
    assert w.load_pending() is not None
    w.clear_pending()                       # confirm 路径
    assert w.load_pending() is None
    assert not w.PENDING.exists()
