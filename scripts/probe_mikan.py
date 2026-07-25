"""探针:验证 Mikan(mikanani.me)页面结构与 RSS,抓取 fixtures 快照。

验证点:
1. 首页/季度页能否提取番剧 ID 列表
2. 搜索页结构(/Home/Search?searchstr=)
3. 番剧页结构(/Home/Bangumi/{id}):字幕组列表、各组完整种子列表、bgm.tv 链接
4. RSS 结构(/RSS/Bangumi?bangumiId=&subgroupid=):字段、条目数(是否截断)
5. bgm.tv 链接覆盖率(抽样多部番)

用法: python probe_mikan.py [--proxy http://127.0.0.1:10808]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE = "https://mikanani.me"
FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mikannet-probe"}


def save_fixture(name: str, content: str) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(content, encoding="utf-8")
    print(f"  [fixture] {name} ({len(content)} chars)")


def probe_homepage(client: httpx.Client) -> list[int]:
    print("\n=== 1. 首页(当季番剧列表)===")
    r = client.get(BASE + "/")
    r.raise_for_status()
    save_fixture("mikan_home.html", r.text)
    soup = BeautifulSoup(r.text, "lxml")
    # 番剧卡片链接形如 /Home/Bangumi/3xxx
    ids = sorted({int(m.group(1)) for a in soup.select('a[href^="/Home/Bangumi/"]')
                  if (m := re.match(r"/Home/Bangumi/(\d+)", a["href"]))})
    print(f"  首页提取到 {len(ids)} 个番剧 ID,样例: {ids[:8]}")
    # 封面图
    covers = soup.select("span[data-src], img[data-src], div[data-src]")
    print(f"  data-src 封面元素: {len(covers)} 个")
    return ids


def probe_search(client: httpx.Client, keyword: str) -> list[dict]:
    print(f"\n=== 2. 搜索页 searchstr={keyword!r} ===")
    r = client.get(f"{BASE}/Home/Search", params={"searchstr": keyword})
    r.raise_for_status()
    save_fixture("mikan_search.html", r.text)
    soup = BeautifulSoup(r.text, "lxml")
    results = []
    for li in soup.select("ul.list-inline.an-ul li"):
        a = li.select_one('a[href^="/Home/Bangumi/"]')
        title_el = li.select_one(".an-text")
        if a:
            bid = int(re.match(r"/Home/Bangumi/(\d+)", a["href"]).group(1))
            results.append({"id": bid, "title": (title_el or a).get("title") or (title_el or a).get_text(strip=True)})
    print(f"  搜索结果 {len(results)} 条: {json.dumps(results[:5], ensure_ascii=False)}")
    return results


def probe_bangumi_page(client: httpx.Client, bangumi_id: int, save: bool = True) -> dict:
    r = client.get(f"{BASE}/Home/Bangumi/{bangumi_id}")
    r.raise_for_status()
    if save:
        save_fixture(f"mikan_bangumi_{bangumi_id}.html", r.text)
    soup = BeautifulSoup(r.text, "lxml")

    title_el = soup.select_one("p.bangumi-title")
    title = title_el.get_text(strip=True) if title_el else "?"

    # bgm.tv 链接
    bgmtv = None
    for a in soup.select('a[href*="bgm.tv/subject/"], a[href*="bangumi.tv/subject/"], a[href*="chii.in/subject/"]'):
        m = re.search(r"subject/(\d+)", a["href"])
        if m:
            bgmtv = int(m.group(1))
            break

    # 字幕组(subgroup-text 锚点带 id)
    groups = []
    for sg in soup.select(".subgroup-text"):
        anchor_id = sg.get("id")
        name_a = sg.select_one("a")
        name = name_a.get_text(strip=True) if name_a else sg.get_text(strip=True).split("\n")[0]
        # 该组种子表:紧随其后的 table
        table = sg.find_next("table")
        rows = table.select("tbody tr") if table else []
        torrents = []
        for tr in rows:
            link = tr.select_one('a[href^="/Home/Episode/"]')
            dl = tr.select_one('a[href$=".torrent"]')
            tds = tr.select("td")
            torrents.append({
                "title": link.get_text(strip=True) if link else None,
                "episode_url": link["href"] if link else None,
                "torrent_url": dl["href"] if dl else None,
                "size": tds[1].get_text(strip=True) if len(tds) > 1 else None,
                "date": tds[2].get_text(strip=True) if len(tds) > 2 else None,
            })
        groups.append({"subgroup_id": anchor_id, "name": name, "torrent_count": len(torrents), "torrents": torrents})

    # 元数据(贴在 bangumi-info 区)
    info = {el.get_text(strip=True) for el in soup.select(".bangumi-info")}
    return {"id": bangumi_id, "title": title, "bgmtv_subject": bgmtv,
            "groups": groups, "info_lines": sorted(info)[:10]}


def probe_rss(client: httpx.Client, bangumi_id: int, subgroup_id: str) -> None:
    print(f"\n=== 4. RSS bangumiId={bangumi_id} subgroupid={subgroup_id} ===")
    r = client.get(f"{BASE}/RSS/Bangumi", params={"bangumiId": bangumi_id, "subgroupid": subgroup_id})
    r.raise_for_status()
    save_fixture(f"mikan_rss_{bangumi_id}_{subgroup_id}.xml", r.text)
    import feedparser
    feed = feedparser.parse(r.text)
    print(f"  feed 标题: {feed.feed.get('title')}")
    print(f"  条目数: {len(feed.entries)}")
    if feed.entries:
        e = feed.entries[0]
        print(f"  首条字段: {sorted(e.keys())}")
        print(f"  首条 title: {e.get('title')}")
        print(f"  首条 link(guid 候选): {e.get('link')}")
        enc = e.get("links", [])
        print(f"  links: {json.dumps([{k: l.get(k) for k in ('href','type','length')} for l in enc], ensure_ascii=False)}")
        print(f"  发布时间: {e.get('published')} / torrent ns: {e.get('torrent_pubdate', e.get('pubdate'))}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    ap.add_argument("--keyword", default="药屋少女")
    args = ap.parse_args()

    with httpx.Client(proxy=args.proxy or None, timeout=30, follow_redirects=True,
                      trust_env=False, headers=UA) as client:
        ids = probe_homepage(client)
        results = probe_search(client, args.keyword)

        target = results[0]["id"] if results else (ids[0] if ids else None)
        if not target:
            print("!! 没有可用番剧 ID,中止")
            return 1

        print(f"\n=== 3. 番剧页 {target} ===")
        detail = probe_bangumi_page(client, target)
        print(f"  标题: {detail['title']}")
        print(f"  bgm.tv subject: {detail['bgmtv_subject']}")
        print(f"  字幕组 {len(detail['groups'])} 个:")
        for g in detail["groups"][:8]:
            print(f"    [{g['subgroup_id']}] {g['name']} — {g['torrent_count']} 个种子")
            for t in g["torrents"][:2]:
                print(f"        {t['title'][:70] if t['title'] else '?'}")
        print(f"  info 行: {detail['info_lines']}")

        if detail["groups"]:
            g0 = max(detail["groups"], key=lambda g: g["torrent_count"])
            probe_rss(client, target, g0["subgroup_id"])
            # 对比:番剧页种子列表 vs RSS 条目数(验证补齐数据源完整性)
            print(f"\n=== 5. 补齐数据源对比:番剧页 {g0['torrent_count']} 个种子 vs RSS 条目数(见上)===")

        # bgm.tv 链接覆盖率抽样
        print("\n=== 6. bgm.tv 链接覆盖率(抽样 8 部)===")
        sample = ids[:8] if len(ids) >= 8 else ids
        ok = 0
        for bid in sample:
            try:
                d = probe_bangumi_page(client, bid, save=False)
                has = d["bgmtv_subject"] is not None
                ok += has
                print(f"    {bid} {d['title'][:30]:32} bgm.tv={'Y' if has else 'N'} 字幕组={len(d['groups'])}")
            except Exception as e:
                print(f"    {bid} FAIL {type(e).__name__}")
        print(f"  覆盖率: {ok}/{len(sample)}")
    print("\nPROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
