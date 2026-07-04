"""探针:验证 bgm.tv v0 API 能拿到 Mikannet 需要的元数据字段。

验证点:年代/放送日期、制作公司(infobox 动画制作)、中文译名、简介、封面、连载状态。
用法: python probe_bgmtv.py [subject_id] [--proxy http://127.0.0.1:10808]
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"
UA = {"User-Agent": "mikannet/0.1 (probe; https://github.com/local/mikannet)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("subject_id", nargs="?", type=int, default=486347)  # 药屋少女二期,来自 Mikan 探针
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    with httpx.Client(proxy=args.proxy or None, timeout=30, trust_env=False, headers=UA) as c:
        r = c.get(f"https://api.bgm.tv/v0/subjects/{args.subject_id}")
        r.raise_for_status()
        data = r.json()

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"bgmtv_subject_{args.subject_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    infobox = {item["key"]: item["value"] for item in data.get("infobox", [])
               if isinstance(item.get("value"), str)}
    studio = infobox.get("动画制作") or infobox.get("製作") or infobox.get("制作")

    print("=== bgm.tv subject", args.subject_id, "===")
    print("name        :", data.get("name"))
    print("name_cn     :", data.get("name_cn"))
    print("date        :", data.get("date"))          # 放送开始 → 年代
    print("platform    :", data.get("platform"))      # TV / 剧场版
    print("eps         :", data.get("eps"))
    print("studio      :", studio)
    print("score       :", (data.get("rating") or {}).get("score"))
    print("images.large:", (data.get("images") or {}).get("large"))
    print("summary     :", (data.get("summary") or "")[:80].replace("\n", " "))
    print("infobox keys:", sorted(infobox.keys()))
    # 连载状态判断素材:date + eps + 集数信息;v0 API 无直接 airing 字段
    print("\nPROBE OK" if data.get("name") and studio else "\nPROBE PARTIAL: 缺字段,检查 infobox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
