#!/usr/bin/env python3
"""发布前隐私检查：扫描 Git 已跟踪和待新增文件，不读取已忽略的本地数据。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publishable_files() -> list[Path]:
    proc = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]


FORBIDDEN_PATHS = [
    ("本地环境文件", re.compile(r"(^|/)\.env(?:\..+)?$")),
    ("编辑器/Agent 本地配置", re.compile(r"(^|/)(?:\.claude|\.codex|\.agents|\.vscode|\.idea)/")),
    ("Cookie 导出", re.compile(r"(^|/).*cookie.*\.json$", re.I)),
    ("运行数据", re.compile(r"^(?:data|dist|downloads|import|ab-trial)/")),
    ("数据库", re.compile(r"\.(?:db|sqlite|sqlite3)(?:-.+)?$", re.I)),
    ("日志", re.compile(r"\.log(?:\.gz)?$", re.I)),
    ("备份", re.compile(r"(^|/)mikannet-backup-.*\.json$", re.I)),
    ("私钥/证书私钥容器", re.compile(r"\.(?:pem|key|p12|pfx)$", re.I)),
]

# `.env.example` 是唯一允许跟踪的环境文件。
PATH_ALLOW = {".env.example"}

CONTENT_RULES = [
    ("私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub 令牌", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack 令牌", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Telegram Bot 令牌", re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("硬编码 CIFS 凭据", re.compile(
        r"username=(?!\$|\{|%)[^,$\s\"'{}]+,password=(?!\$|\{|%)[^,\s\"'{}]+",
        re.I,
    )),
    ("本机用户目录", re.compile(r"\b[A-Z]:\\Users\\[^\\\r\n]+", re.I)),
]

CONFIG_SECRET_DEFAULT = re.compile(
    r"\b(?:qb_password|bitcomet_password|smb_password|tmdb_api_key|llm_api_key|"
    r"bgmtv_access_token)\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]",
    re.I,
)


def main() -> int:
    findings: list[str] = []
    for path in publishable_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel not in PATH_ALLOW:
            for label, pattern in FORBIDDEN_PATHS:
                if pattern.search(rel):
                    findings.append(f"{rel}: {label}")
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for label, pattern in CONTENT_RULES:
            if pattern.search(text):
                findings.append(f"{rel}: {label}")
        if rel == "backend/app/config.py" and CONFIG_SECRET_DEFAULT.search(text):
            findings.append(f"{rel}: 密钥类配置存在非空默认值")

    if findings:
        print("隐私检查失败：")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1
    print("隐私检查通过：待发布文件中未发现本地数据、常见令牌或硬编码凭据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
