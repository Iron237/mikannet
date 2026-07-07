"""自更新:检查(只读)+ 应用(纯代码就地 / 完整换镜像)。docs/adr/0005。

控制面 = GitHub Releases API(公开免认证)+ 每版 manifest.json 资产。
更新类型由 base_rev 决定:相同 → 纯代码(下载代码包、重指 current、退出交给 wrapper);
不同 / 低于 min_version → 完整(经 docker socket 启 helper 换镜像重建容器)。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import signal
import tarfile
import threading
import time
from pathlib import Path

from app._version import BASE_REV, VERSION
from app.clients.http import make_client
from app.config import settings
from app.services import update_gate

log = logging.getLogger(__name__)

# --- 进度状态(前端轮询 /update/status)---------------------------------------------------
_status_lock = threading.Lock()
_status: dict = {"phase": "idle", "message": "", "version": "", "type": "",
                 "progress": 0, "error": ""}


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _set_status(**kw) -> None:
    with _status_lock:
        _status.update(kw)
    if kw.get("phase"):
        log.info("自更新[%s] %s", kw.get("phase"), kw.get("message", ""))


# --- semver(预发布排序:0.1.0 > 0.1.0-rc.1)---------------------------------------------
def ver_key(v: str):
    v = (v or "0.0.0").lstrip("vV")
    core, _, pre = v.partition("-")
    nums = []
    for part in core.split("."):
        m = re.match(r"(\d+)", part)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    if not pre:
        pre_key = (1,)
    else:
        ids = [((0, int(p)) if p.isdigit() else (1, p)) for p in pre.split(".")]
        pre_key = (0, tuple(ids))
    return (tuple(nums), pre_key)


# --- GitHub Releases 访问 ----------------------------------------------------------------
def _releases_api() -> str:
    return f"https://api.github.com/repos/{settings.update_repo}/releases"


def _list_releases() -> list[dict]:
    with make_client("github", timeout=20,
                     headers={"Accept": "application/vnd.github+json"}) as c:
        r = c.get(_releases_api(), params={"per_page": 30})
        r.raise_for_status()
        return r.json()


def _find_asset(release: dict, name: str) -> dict | None:
    for a in release.get("assets", []):
        if a.get("name") == name:
            return a
    return None


def _fetch_manifest(release: dict) -> dict:
    a = _find_asset(release, "manifest.json")
    if not a:
        # 无 manifest(老/手工 release):退化为「完整」(base_rev 未知)
        return {"version": (release.get("tag_name") or "").lstrip("v"),
                "base_rev": "", "prerelease": bool(release.get("prerelease")),
                "changelog": release.get("body") or ""}
    with make_client("github", timeout=30) as c:
        r = c.get(a["browser_download_url"])
        r.raise_for_status()
        return r.json()


# --- 检查 --------------------------------------------------------------------------------
def _check_internal(include_prerelease: bool | None = None):
    """返回 (public_result, manifest|None, release|None)。"""
    if include_prerelease is None:
        include_prerelease = settings.update_channel_prerelease
    result = {"current": VERSION, "current_base_rev": BASE_REV, "latest": None,
              "type": "none", "changelog": "", "size": 0, "prerelease": False,
              "channel": "prerelease" if include_prerelease else "stable"}
    releases = _list_releases()
    cands = [r for r in releases
             if not r.get("draft") and (include_prerelease or not r.get("prerelease"))]
    best = max(cands, key=lambda r: ver_key(r.get("tag_name", "")), default=None)
    if not best:
        return result, None, None
    manifest = _fetch_manifest(best)
    latest = manifest.get("version") or (best.get("tag_name") or "").lstrip("v")
    result["latest"] = latest
    result["prerelease"] = bool(best.get("prerelease"))
    result["changelog"] = manifest.get("changelog") or best.get("body") or ""
    if ver_key(latest) <= ver_key(VERSION):
        return result, manifest, best                       # 已是最新或更旧
    # 有新版:类型由 base_rev 决定;min_version 兜底强制完整
    same_base = manifest.get("base_rev") and manifest["base_rev"] == BASE_REV
    too_old = manifest.get("min_version") and ver_key(VERSION) < ver_key(manifest["min_version"])
    result["type"] = "code" if (same_base and not too_old) else "full"
    code_asset = _find_asset(best, f"mikannet-{latest}-code.tar.gz")
    result["size"] = int(code_asset.get("size", 0)) if code_asset else 0
    return result, manifest, best


def check(include_prerelease: bool | None = None) -> dict:
    """只读检查更新;网络/解析失败抛异常由上层转友好错误。"""
    result, _m, _r = _check_internal(include_prerelease)
    return result


# --- 代码卷操作(与 wrapper 对齐)--------------------------------------------------------
def _releases_dir() -> Path:
    return settings.code_dir / "releases"


def _current_version_name() -> str | None:
    cur = settings.code_dir / "current"
    try:
        if cur.is_symlink() or cur.exists():
            return cur.resolve().name
    except OSError:
        return None
    return None


def _point_current(version: str) -> None:
    target = _releases_dir() / version
    tmp = settings.code_dir / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, settings.code_dir / "current")     # 原子重指(POSIX)


def _write_pending(prev: str | None, new: str) -> None:
    import json
    (settings.code_dir / "pending.json").write_text(
        json.dumps({"prev": prev, "new": new, "attempts": 0}), "utf-8")


def _safe_extract(data: bytes, dest: Path) -> None:
    """解压 tar.gz 到 dest(防路径穿越)。

    除前缀校验外一律拒绝链接成员:先放「指向卷外的符号链接」再放「穿过它的普通文件」
    可绕过提取前的一次性校验(经典 tar 逃逸)。代码包由 CI 生成,本就不含链接。"""
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.issym() or m.islnk():
                raise RuntimeError(f"代码包含链接成员(不允许): {m.name}")
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise RuntimeError(f"代码包含非法路径: {m.name}")
        tar.extractall(dest)   # noqa: S202 — 已逐成员校验路径且无链接成员


def _download(url: str) -> bytes:
    """下载到内存并更新进度(代码包仅几 MB)。"""
    buf = io.BytesIO()
    with make_client("github", timeout=120) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            for chunk in r.iter_bytes(64 * 1024):
                buf.write(chunk)
                got += len(chunk)
                if total:
                    _set_status(progress=int(got * 100 / total))
    return buf.getvalue()


def _quiesce() -> None:
    """停调度 / 后处理,置 updating 标记(挡新写)。"""
    update_gate.set_updating(True)
    try:
        from app import scheduler
        scheduler.stop()
    except Exception as e:  # noqa: BLE001
        log.warning("停调度失败: %s", e)


def _exit_soon(delay: float = 1.5) -> None:
    """让 HTTP 响应先回到前端,再向自身发 SIGTERM,交给 wrapper 重启拾起新 current。"""
    def _kill():
        time.sleep(delay)
        log.info("自更新:向自身发 SIGTERM 触发重启")
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_kill, daemon=True).start()


# --- 应用:纯代码 ------------------------------------------------------------------------
def apply_code(manifest: dict) -> None:
    """下载 → 校验 → 解压 → 重指 current → 写 pending → 退出(wrapper 拉起新版)。"""
    reason = update_gate.busy_reason()
    if reason:
        _set_status(phase="error", error=reason)
        raise RuntimeError(reason)
    version = manifest["version"]
    try:
        _set_status(phase="downloading", version=version, type="code",
                    message="下载代码包", progress=0, error="")
        data = _download(manifest["code_package_url"])

        _set_status(phase="verifying", message="校验 sha256")
        digest = hashlib.sha256(data).hexdigest()
        if digest != manifest.get("code_sha256"):
            raise RuntimeError(f"sha256 校验不通过(期望 {manifest.get('code_sha256')},实得 {digest})")

        _set_status(phase="applying", message="解压并切换版本")
        rel = _releases_dir() / version
        tmp = _releases_dir() / f".apply-{version}.tmp"
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        _safe_extract(data, tmp)
        if not (tmp / "backend" / "app" / "main.py").exists():
            raise RuntimeError("代码包结构异常(缺 backend/app/main.py)")
        if rel.exists():
            import shutil
            shutil.rmtree(rel, ignore_errors=True)
        tmp.rename(rel)

        prev = _current_version_name()
        # 二次核验:下载/解压耗时里可能有新开始的移文件操作(入口只查过一次)。
        # 切换后就要自杀重启,硬中断移文件会留半套文件;此刻中止零副作用
        # (解压好的 release 目录留着,下次重试直接覆盖)。
        reason = update_gate.busy_reason()
        if reason:
            raise RuntimeError(f"{reason}(更新已在切换前中止,未产生影响,稍后重试即可)")
        _write_pending(prev, version)
        _point_current(version)            # 原子切换

        _set_status(phase="restarting", message="重启应用", progress=100)
        _quiesce()
        _exit_soon()
    except Exception as e:  # noqa: BLE001
        _set_status(phase="error", error=str(e))
        log.exception("纯代码更新失败")
        raise


# --- 应用:完整(换镜像)----------------------------------------------------------------
_FULL_UPDATE_TIMEOUT = 600.0   # helper 拉镜像+重建的宽限(秒);超时本进程还活着 = helper 失败


def _arm_full_update_watchdog(timeout: float = _FULL_UPDATE_TIMEOUT) -> None:
    """helper 成功会重建本容器(本进程消失);超时还活着说明 helper 静默失败
    (镜像拉取失败 / GHCR 权限 / compose 目录不对等,helper 是脱管异步执行,
    run_compose_recreate 只保证「请求已发出」)。若不自愈,updating 标记会让
    全站写请求永久 503 且无任何提示。"""
    def _watch():
        time.sleep(timeout)
        if not update_gate.is_updating():
            return                       # 已被其他路径复位
        update_gate.set_updating(False)
        try:
            from app import scheduler
            scheduler.resume_after_failed_update()
        except Exception as e:  # noqa: BLE001
            log.warning("恢复调度失败: %s", e)
        _set_status(phase="error", error=(
            f"完整更新未生效:helper 在 {int(timeout)} 秒内没有重建容器"
            "(常见原因:镜像拉取失败 / GHCR 包不可见 / compose 目录配置不对)。"
            "已解除更新锁,应用恢复可用,可查看 Docker 日志后重试。"))
        log.error("完整更新 watchdog:helper 未在 %ss 内重建容器,已解除 updating 锁", timeout)
    threading.Thread(target=_watch, daemon=True).start()


def apply_full(manifest: dict) -> None:
    """经 docker socket 启 helper 跑 `compose up -d` 换镜像重建本容器。"""
    from app.services import docker_api
    reason = update_gate.busy_reason()
    if reason:
        _set_status(phase="error", error=reason)
        raise RuntimeError(reason)
    if not docker_api.available():
        msg = "docker.sock 不可用:完整更新需挂载 /var/run/docker.sock(见 README)"
        _set_status(phase="error", error=msg)
        raise RuntimeError(msg)
    version = manifest["version"]
    image_ref = manifest.get("image_ref")
    if not image_ref:
        msg = "manifest 缺 image_ref,无法完整更新"
        _set_status(phase="error", error=msg)
        raise RuntimeError(msg)
    try:
        _set_status(phase="recreating", version=version, type="full",
                    message="拉新镜像并重建容器(完整更新)", progress=0, error="")
        _quiesce()
        # 不写 pending:完整更新后由 wrapper「烤死版本 > 卷版本」重播种;
        # v1 完整更新失败走手动回滚(ADR 决策,镜像级自动回滚为后续)。
        docker_api.run_compose_recreate(
            host_compose_dir=settings.compose_host_dir,
            project=settings.compose_project,
            image_ref=image_ref,
            compose_files=settings.compose_files,
        )
        _set_status(phase="restarting", message="helper 正在重建容器…", progress=100)
        # helper 会重建本容器(杀死自己),无需自行退出;
        # 但 helper 是脱管异步的 —— 静默失败时靠 watchdog 解锁自愈(否则写请求永久 503)
        _arm_full_update_watchdog()
    except Exception as e:  # noqa: BLE001
        update_gate.set_updating(False)
        _set_status(phase="error", error=str(e))
        log.exception("完整更新失败")
        raise


def apply_latest() -> dict:
    """检查并按类型应用(在后台线程跑)。返回将要应用的类型/版本。"""
    result, manifest, _release = _check_internal()
    if result["type"] == "none" or not manifest:
        raise RuntimeError("已是最新,无可用更新")
    typ = result["type"]
    target = apply_code if typ == "code" else apply_full
    threading.Thread(target=target, args=(manifest,), daemon=True).start()
    return {"type": typ, "version": result["latest"]}
