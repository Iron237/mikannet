"""改名过渡:旧前缀 MIKANARR_ 环境变量必须被映射到新前缀,否则纯代码自更新落到旧容器时
data_dir 会退回默认 → 番剧库空、库文件落错路径(2026-07-05 H-SERVER 真实事故)。

用子进程加载 app.config,模拟旧容器只有 MIKANARR_ 前缀的环境。
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _load_config_with_env(tmp_path, extra_env):
    data_dir = tmp_path / "cfgdata"
    # 继承真实环境(Windows 下 python 启动需要 SystemRoot/PATH 等),但清掉任何 MIKAN* 前缀,
    # 只保留本用例显式给的,精确模拟「旧容器只有 MIKANARR_」等情形。
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("MIKANARR_") or k.startswith("MIKANNET_"))}
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env)
    code = (
        "from app.config import settings;"
        "print('DATA_DIR=' + str(settings.data_dir));"
        "print('TMDB=' + settings.tmdb_api_key);"
        "print('QB_HOST=' + settings.qb_host)"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=str(BACKEND))
    assert r.returncode == 0, r.stderr
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line), data_dir


def test_legacy_prefix_is_mapped(tmp_path):
    """只给 MIKANARR_ 前缀(旧容器情形)→ 新代码仍能读到。"""
    d = tmp_path / "cfgdata"
    out, _ = _load_config_with_env(tmp_path, {
        "MIKANARR_DATA_DIR": str(d),
        "MIKANARR_TMDB_API_KEY": "legacy-key-123",
        "MIKANARR_QB_HOST": "legacy-qb-host",
    })
    assert out["DATA_DIR"].strip() == str(d)          # 关键:不再退回默认 backend/data
    assert out["TMDB"].strip() == "legacy-key-123"
    assert out["QB_HOST"].strip() == "legacy-qb-host"


def test_new_prefix_wins_when_both_present(tmp_path):
    """新旧前缀同时存在(过渡后)→ 新前缀优先,旧的只作兜底。"""
    out, _ = _load_config_with_env(tmp_path, {
        "MIKANARR_QB_HOST": "old-host",
        "MIKANNET_QB_HOST": "new-host",
        "MIKANNET_DATA_DIR": str(tmp_path / "cfgdata"),
    })
    assert out["QB_HOST"].strip() == "new-host"
