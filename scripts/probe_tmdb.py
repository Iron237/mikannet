"""探针:验证 TMDB API 能搜到番剧并拿到高清横版背景图。

验证点:按日文/中文名搜索 TV、backdrop 图 URL、图片 CDN 经代理可达。
用法: python probe_tmdb.py --api-key XXX [--query 薬屋のひとりごと] [--proxy http://127.0.0.1:10808]
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--query", default="薬屋のひとりごと")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    with httpx.Client(proxy=args.proxy or None, timeout=30, trust_env=False) as c:
        r = c.get("https://api.themoviedb.org/3/search/tv",
                  params={"api_key": args.api_key, "query": args.query, "language": "zh-CN"})
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        print(f"[1] 搜索 {args.query!r} → {len(results)} 条")
        if not results:
            print("PROBE FAIL: 无结果")
            return 1
        hit = results[0]
        print(f"    首条: id={hit['id']} name={hit.get('name')} original={hit.get('original_name')} "
              f"first_air={hit.get('first_air_date')}")
        print(f"    backdrop_path: {hit.get('backdrop_path')}")

        detail = c.get(f"https://api.themoviedb.org/3/tv/{hit['id']}",
                       params={"api_key": args.api_key, "language": "zh-CN"}).json()
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / f"tmdb_tv_{hit['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[2] 详情 OK — 季数 {detail.get('number_of_seasons')} 状态 {detail.get('status')}")

        if hit.get("backdrop_path"):
            img_url = f"https://image.tmdb.org/t/p/w1280{hit['backdrop_path']}"
            img = c.get(img_url)
            print(f"[3] 背景图下载: {img.status_code} {len(img.content)/1024:.0f} KB ← {img_url}")
        print("PROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
