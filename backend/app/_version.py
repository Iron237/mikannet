"""版本与依赖基线(base_rev)。

VERSION / BASE_REV 由 CI 在**构建期烤进镜像**(env `MIKANNET_VERSION` /
`MIKANNET_BASE_REV`)。未注入时(开发期源码直跑)回退到本地计算:
- VERSION 取下方 `_DEFAULT_VERSION`;
- BASE_REV 由 `pyproject` 依赖 + `docker/apt-packages.txt` 的确定性哈希得出。

base_rev 决定更新类型:目标 base_rev == 运行中 → **纯代码**更新(快);
不同(依赖 / 系统库变了) → **完整**更新(换镜像)。见 docs/adr/0005。

本模块只用标准库,可被 importlib 单独 exec(CI 的 compute_base_rev.py 依赖此特性)。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# 源码内置默认版本(发布时与 pyproject / package.json 同步;CI 用 release tag 覆盖)
_DEFAULT_VERSION = "0.1.0"


def _read_deps() -> list[str]:
    """读取 [project].dependencies(排序,稳定)。兼容 repo 布局与镜像内布局。"""
    candidates = [
        Path(__file__).resolve().parents[1] / "pyproject.toml",   # backend/pyproject.toml
        Path("/opt/mikannet/backend/pyproject.toml"),             # 镜像内(从卷跑时回退)
    ]
    for f in candidates:
        if f.exists():
            try:
                import tomllib
                data = tomllib.loads(f.read_text("utf-8"))
                return sorted(data.get("project", {}).get("dependencies", []))
            except Exception:
                return []
    return []


def _read_apt() -> list[str]:
    candidates = [
        Path(__file__).resolve().parents[2] / "docker" / "apt-packages.txt",  # repo docker/
        Path("/opt/mikannet/apt-packages.txt"),                               # 镜像内
    ]
    for f in candidates:
        if f.exists():
            return sorted(x.strip() for x in f.read_text("utf-8").splitlines()
                          if x.strip() and not x.lstrip().startswith("#"))
    return []


def compute_base_rev() -> str:
    """deps + apt 的确定性短哈希(12 位 hex)。CI 与运行期回退共用此逻辑。"""
    h = hashlib.sha256()
    for line in _read_deps():
        h.update(b"dep:" + line.encode() + b"\n")
    for line in _read_apt():
        h.update(b"apt:" + line.encode() + b"\n")
    return h.hexdigest()[:12]


# 兼容改名(mikanarr→mikannet):纯代码自更新可能把新代码落到改名前的旧 wrapper 上,
# 那个 wrapper 仍导出 MIKANARR_VERSION;若只读新名会回退成默认版本号,触发「永远有新版」+
# 前端「等待新版本起来」永久卡住。故新名优先、旧名兜底。
VERSION: str = (os.environ.get("MIKANNET_VERSION") or os.environ.get("MIKANARR_VERSION")
                or _DEFAULT_VERSION)
BASE_REV: str = (os.environ.get("MIKANNET_BASE_REV") or os.environ.get("MIKANARR_BASE_REV")
                 or compute_base_rev())
