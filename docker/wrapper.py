#!/usr/bin/env python3
"""PID-1 wrapper: run app from a writable code volume with health-gated auto-rollback.

Responsibilities (docs/adr/0005, slice 3):
1. Seed the code volume from the image-baked baseline on first boot; reseed when the
   baked version is newer than the volume's current version (happens after a full/image
   update so the new baseline replaces the volume copy).
2. Maintain ``<code>/current`` symlink -> ``<code>/releases/<version>``.
3. Supervise the app subprocess. After start, probe HTTP /api/system/healthz:
   - healthy  -> confirm (clear the ``pending`` marker), reset failure counter;
   - the app exits before turning healthy too many times (crash loop) -> roll the
     ``current`` symlink back to the previous good version.
4. When the app exits *after* being healthy (e.g. an update calls sys.exit), just loop;
   the next iteration picks up the possibly-repointed ``current``.

Standard library only: this must keep working even if the volume code is broken. It runs
from the image-baked location (/opt/mikannet/wrapper.py), never from the volume.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- paths / knobs (env-overridable for tests) -----------------------------------------
BAKED = Path(os.environ.get("MIKANNET_BAKED_DIR", "/opt/mikannet"))      # image baseline
CODE = Path(os.environ.get("MIKANNET_CODE_DIR", "/code"))               # writable volume
RELEASES = CODE / "releases"
CURRENT = CODE / "current"
PENDING = CODE / "pending.json"
BAKED_VERSION = (os.environ.get("MIKANNET_VERSION") or os.environ.get("MIKANARR_VERSION")
                 or "0.1.0")   # 改名兼容:新名优先旧名兜底;默认与 app/_version._DEFAULT_VERSION 对齐
PORT = int(os.environ.get("MIKANNET_PORT", "8008"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/system/healthz"
HEALTH_TIMEOUT = int(os.environ.get("MIKANNET_HEALTH_TIMEOUT", "120"))   # s to first 200
HEALTH_INTERVAL = 2
ROLLBACK_K = int(os.environ.get("MIKANNET_ROLLBACK_K", "3"))             # crash-loop limit

_state = {"proc": None, "stopping": False}


def log(msg: str) -> None:
    print(f"[wrapper] {msg}", flush=True)


# --- semver (minimal; prerelease sorts before its release) -----------------------------
def ver_key(v: str):
    """Return a comparable key. 0.1.0 > 0.1.0-rc.1; non-numeric parts compared as str."""
    v = (v or "0.0.0").lstrip("vV")
    core, _, pre = v.partition("-")
    nums = []
    for part in core.split("."):
        m = re.match(r"(\d+)", part)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    # release (no prerelease) ranks above any prerelease of same core -> use (1,) vs (0,...)
    if not pre:
        pre_key = (1,)
    else:
        ids = []
        for p in pre.split("."):
            ids.append((0, int(p)) if p.isdigit() else (1, p))
        pre_key = (0, tuple(ids))
    return (tuple(nums), pre_key)


# --- code volume seeding ---------------------------------------------------------------
def _valid_release(rel: Path) -> bool:
    return (rel / "backend" / "app" / "main.py").exists()


def seed_baseline(force: bool = False) -> Path:
    """Copy the image-baked baseline into releases/<BAKED_VERSION>. Returns it.

    Skips if a valid release dir already exists, unless ``force`` (dev rebuilds of the
    same version with changed code: refresh the volume copy).
    """
    target = RELEASES / BAKED_VERSION
    if _valid_release(target) and not force:
        return target
    RELEASES.mkdir(parents=True, exist_ok=True)
    tmp = RELEASES / f".seed-{BAKED_VERSION}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "backend").mkdir(parents=True, exist_ok=True)
    shutil.copytree(BAKED / "backend" / "app", tmp / "backend" / "app")
    shutil.copytree(BAKED / "frontend" / "dist", tmp / "frontend" / "dist")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    log(f"seeded baseline -> {target}")
    return target


def current_version() -> str | None:
    """Version name the ``current`` symlink points at, or None if missing/broken."""
    try:
        if not (CURRENT.is_symlink() or CURRENT.exists()):
            return None
        real = CURRENT.resolve()
        if _valid_release(real):
            return real.name
    except OSError:
        return None
    return None


def point_current(version: str) -> None:
    """Atomically repoint ``current`` -> releases/<version>."""
    target = RELEASES / version
    tmp = CODE / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, CURRENT)   # atomic rename over existing symlink (POSIX)
    log(f"current -> {version}")


def ensure_seeded() -> None:
    RELEASES.mkdir(parents=True, exist_ok=True)
    # dev-only:同版本号重建镜像后强制刷新卷代码(生产/发布包不设此变量,走严格版本判断)
    if os.environ.get("MIKANNET_DEV_RESEED"):
        log("DEV_RESEED: force reseed baked baseline + repoint current")
        seed_baseline(force=True)
        point_current(BAKED_VERSION)
        clear_pending()
        return
    seed_baseline()
    cur = current_version()
    if cur is None:
        point_current(BAKED_VERSION)
        return
    if ver_key(BAKED_VERSION) > ver_key(cur):
        log(f"baked {BAKED_VERSION} > volume {cur}: reseed + repoint")
        seed_baseline(force=True)
        point_current(BAKED_VERSION)


# --- pending marker --------------------------------------------------------------------
def load_pending() -> dict | None:
    try:
        return json.loads(PENDING.read_text("utf-8"))
    except Exception:
        return None


def save_pending(d: dict) -> None:
    PENDING.write_text(json.dumps(d), "utf-8")


def clear_pending() -> None:
    try:
        PENDING.unlink()
    except FileNotFoundError:
        pass


# --- app subprocess --------------------------------------------------------------------
def start_app(rel: Path) -> subprocess.Popen:
    env = dict(os.environ)
    backend = str(rel / "backend")
    env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
    # 关键:让 app 报告的版本 = current 实际指向的版本,而非镜像烤死的 env。
    # 纯代码更新只换卷代码不换镜像,若沿用镜像 env 版本号,/version 会一直报旧值,
    # 导致「检查更新」永远判定有新版 + 前端「等待新版本起来」永久卡住。base_rev 纯代码
    # 更新不变,仍由镜像 env 提供(故不在此覆盖 MIKANNET_BASE_REV)。
    env["MIKANNET_VERSION"] = rel.name
    env["MIKANARR_VERSION"] = rel.name   # 改名兼容:回滚到改名前的旧代码时它仍读旧名
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", "0.0.0.0", "--port", str(PORT)]
    log(f"starting app from {rel} (v={rel.name})")
    return subprocess.Popen(cmd, cwd=backend, env=env)


def terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:   # noqa: S310 (localhost)
            return 200 <= r.status < 300
    except Exception:
        return False


def wait_health(proc: subprocess.Popen) -> bool:
    """True once /healthz returns 200; False if the app exits or times out first."""
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if http_ok(HEALTH_URL):
            return True
        time.sleep(HEALTH_INTERVAL)
    log("health probe timed out; terminating app")
    terminate(proc)
    return False


def handle_unhealthy(cur: str) -> None:
    """Count a failed start of a pending version; roll back after ROLLBACK_K attempts."""
    pending = load_pending()
    if not (pending and pending.get("new") == cur and pending.get("prev")):
        return   # nothing to roll back to (e.g. baseline itself is bad)
    pending["attempts"] = int(pending.get("attempts", 0)) + 1
    save_pending(pending)
    log(f"pending {cur} unhealthy (attempt {pending['attempts']}/{ROLLBACK_K})")
    if pending["attempts"] < ROLLBACK_K:
        return
    prev = pending["prev"]
    if _valid_release(RELEASES / prev):
        log(f"crash-loop -> rollback {cur} -> {prev}")
        point_current(prev)
    else:
        log(f"rollback target {prev} missing; cannot auto-roll back")
    clear_pending()


def _on_signal(signum, _frame):
    _state["stopping"] = True
    log(f"got signal {signum}; shutting down")
    p = _state["proc"]
    if p:
        terminate(p)
    sys.exit(0)


def run() -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    ensure_seeded()
    while not _state["stopping"]:
        cur = current_version() or BAKED_VERSION
        rel = RELEASES / cur
        if not _valid_release(rel):
            log(f"current release {cur} invalid; reseeding baseline")
            seed_baseline()
            point_current(BAKED_VERSION)
            cur, rel = BAKED_VERSION, RELEASES / BAKED_VERSION
        proc = start_app(rel)
        _state["proc"] = proc
        if wait_health(proc):
            clear_pending()                  # confirm: this version is good
            log(f"v{cur} healthy; confirmed")
            proc.wait()                      # block until app exits (e.g. update sys.exit)
            if _state["stopping"]:
                break
            log("app exited after healthy; restarting loop")
        else:
            rc = proc.poll()
            log(f"v{cur} exited before healthy (rc={rc})")
            handle_unhealthy(cur)
            time.sleep(2)                    # small backoff against tight loops


if __name__ == "__main__":
    run()
