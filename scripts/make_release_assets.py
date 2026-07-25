#!/usr/bin/env python3
"""打代码包 + 生成 manifest.json(CI release 流水线用)。

产物(写到 --out 目录):
- mikannet-<version>-code.tar.gz : backend/app + frontend/dist,保持镜像内布局
- manifest.json                  : 自更新控制面(字段见 docs/adr/0005)

changelog 经环境变量 RELEASE_CHANGELOG 传入(避免命令行注入/引号问题)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_code_package(out_dir: Path, version: str) -> Path:
    backend_app = REPO_ROOT / "backend" / "app"
    dist = REPO_ROOT / "frontend" / "dist"
    if not backend_app.is_dir():
        raise SystemExit("缺 backend/app")
    if not dist.is_dir():
        raise SystemExit("缺 frontend/dist(先 npm run build)")

    code_path = out_dir / f"mikannet-{version}-code.tar.gz"

    def _norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mtime = 0
        return ti

    with tarfile.open(code_path, "w:gz", compresslevel=9) as tar:
        for arc, src in (("backend/app", backend_app), ("frontend/dist", dist)):
            for f in sorted(src.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    tar.add(f, arcname=f"{arc}/{f.relative_to(src).as_posix()}", filter=_norm)
    return code_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--base-rev", required=True)
    ap.add_argument("--image-ref", required=True)
    ap.add_argument("--image-digest", default="")
    ap.add_argument("--repo", default="Iron237/mikannet")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--prerelease", default="false")
    ap.add_argument("--min-version", default="0.1.0")
    ap.add_argument("--out", default="dist/release")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    code_path = build_code_package(out_dir, args.version)
    code_sha = sha256_file(code_path)
    code_name = code_path.name
    url = (f"https://github.com/{args.repo}/releases/download/{args.tag}/{code_name}")

    manifest = {
        "version": args.version,
        "base_rev": args.base_rev,
        "image_ref": args.image_ref,
        "image_digest": args.image_digest,
        "code_package_url": url,
        "code_sha256": code_sha,
        "min_version": args.min_version,
        "prerelease": str(args.prerelease).strip().lower() == "true",
        "changelog": os.environ.get("RELEASE_CHANGELOG", ""),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")

    print(f"[code] {code_path} ({code_path.stat().st_size} bytes)")
    print(f"[sha256] {code_sha}")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
