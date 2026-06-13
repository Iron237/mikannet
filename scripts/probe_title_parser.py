"""探针:收集 ≥200 条真实 Mikan 种子标题,评估 anitopy 解析准确率。

语料来源:
1. /RSS/Classic(全站最新,跨字幕组多样性最好)
2. 首页若干热门番剧 → 各字幕组 RSS

产出:
- backend/tests/fixtures/titles.txt        全部语料(去重)
- backend/tests/fixtures/titles_failed.txt 解析失败样本(P1 自定义规则依据)

判定口径(探针级,宽松):
- 标题中解析出集数 → OK
- 识别为合集(范围 01-12/合集/全集/Batch/BOX/Fin)→ OK(batch)
- 都没有 → FAIL
用法: python probe_title_parser.py [--proxy http://127.0.0.1:10808]
"""
import argparse
import re
import sys
from pathlib import Path

import anitopy
import feedparser
import httpx
from bs4 import BeautifulSoup

BASE = "https://mikanani.me"
FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"
UA = {"User-Agent": "Mozilla/5.0 Mikanarr-probe"}

BATCH_RE = re.compile(r"合集|全集|Batch|BOX|Fin\b|\[\s*\d{1,3}\s*-\s*\d{1,3}\s*(?:END|Fin)?\s*\]|【\d{1,3}-\d{1,3}】", re.I)
VERSION_RE = re.compile(r"[\[\s（(]v(\d)[\]\s）)]|(?<=\d)v(\d)\b", re.I)


def collect_titles(client: httpx.Client, target: int = 250) -> list[str]:
    titles: dict[str, None] = {}

    def add_feed(xml: str) -> None:
        for e in feedparser.parse(xml).entries:
            t = (e.get("title") or "").strip()
            if t:
                titles.setdefault(t)

    print("[1] /RSS/Classic 全站最新 …")
    add_feed(client.get(f"{BASE}/RSS/Classic").text)
    print(f"    累计 {len(titles)}")

    print("[2] 首页热门番剧 × 字幕组 RSS …")
    soup = BeautifulSoup(client.get(BASE + "/").text, "lxml")
    ids = list(dict.fromkeys(
        int(m.group(1)) for a in soup.select('a[href^="/Home/Bangumi/"]')
        if (m := re.match(r"/Home/Bangumi/(\d+)", a["href"]))))
    for bid in ids:
        if len(titles) >= target:
            break
        page = client.get(f"{BASE}/Home/Bangumi/{bid}").text
        sub_ids = list(dict.fromkeys(re.findall(r'class="subgroup-text"[^>]*id="(\d+)"', page)))[:3]
        for sid in sub_ids:
            add_feed(client.get(f"{BASE}/RSS/Bangumi", params={"bangumiId": bid, "subgroupid": sid}).text)
        print(f"    bangumi {bid}: {len(sub_ids)} 组,累计 {len(titles)}")
    return list(titles)


def evaluate(titles: list[str]) -> None:
    ok_ep, ok_batch, failed = [], [], []
    for t in titles:
        parsed = anitopy.parse(t) or {}
        if parsed.get("episode_number"):
            ok_ep.append((t, parsed.get("episode_number"), parsed.get("release_version")))
        elif BATCH_RE.search(t):
            ok_batch.append(t)
        else:
            failed.append(t)

    n = len(titles)
    print(f"\n=== anitopy 评估(n={n})===")
    print(f"  集数解析成功 : {len(ok_ep):4} ({len(ok_ep)/n:.1%})")
    print(f"  合集识别     : {len(ok_batch):4} ({len(ok_batch)/n:.1%})")
    print(f"  失败         : {len(failed):4} ({len(failed)/n:.1%})")
    v2 = [t for t, _, v in ok_ep if v] + [t for t in titles if VERSION_RE.search(t)]
    print(f"  含版本标记(v2)样本: {len(set(v2))}")

    print("\n  失败样本(前 15,写入 titles_failed.txt):")
    for t in failed[:15]:
        print(f"    {t[:90]}")

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "titles.txt").write_text("\n".join(titles), encoding="utf-8")
    (FIXTURES / "titles_failed.txt").write_text("\n".join(failed), encoding="utf-8")
    print(f"\n[fixture] titles.txt({n})/ titles_failed.txt({len(failed)})")
    print("PROBE OK" if (len(ok_ep) + len(ok_batch)) / n >= 0.80 else "PROBE WARN: 裸 anitopy 低于 80%,P1 规则链工作量大")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()
    with httpx.Client(proxy=args.proxy or None, timeout=30, follow_redirects=True,
                      trust_env=False, headers=UA) as client:
        titles = collect_titles(client)
    evaluate(titles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
