#!/usr/bin/env python3
"""打印 base_rev(CI 与本地共用)。

不导入 `app` 包(避免触发其依赖),用 importlib 直接 exec `_version.py`(纯标准库)。
用法:python scripts/compute_base_rev.py
"""
import importlib.util
import pathlib
import sys

_VERSION_PY = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app" / "_version.py"

spec = importlib.util.spec_from_file_location("_mikannet_version", _VERSION_PY)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.stdout.write(mod.compute_base_rev())
