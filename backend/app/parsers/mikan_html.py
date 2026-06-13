"""Mikan HTML 解析:全部选择器集中在此文件(唯一脆弱面,改版只修这里)。

结构依据 P0 fixtures(backend/tests/fixtures/mikan_*.html),有快照回归测试。
解析失败抛 MikanParseError,调用方负责告警而非静默。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup


class MikanParseError(Exception):
    pass


@dataclass
class SearchResult:
    mikan_bangumi_id: int
    title: str
    cover_url: str | None      # 相对路径,如 /images/Bangumi/...


@dataclass
class SubgroupTorrent:
    title: str
    episode_url: str           # /Home/Episode/<hash>(guid)
    torrent_url: str | None
    size: str | None
    published: str | None


@dataclass
class Subgroup:
    subgroup_id: str
    name: str
    torrents: list[SubgroupTorrent] = field(default_factory=list)


@dataclass
class BangumiDetail:
    mikan_bangumi_id: int
    title: str
    cover_url: str | None
    bgmtv_subject_id: int | None
    air_date_str: str | None   # "1/10/2025"
    subgroups: list[Subgroup] = field(default_factory=list)


_BANGUMI_HREF = re.compile(r"/Home/Bangumi/(\d+)")
_EPISODE_BANGUMI = re.compile(r'href="/Home/Bangumi/(\d+)#(\d+)"')


def parse_episode(html: str) -> tuple[int, str]:
    """Episode 页 → (mikan_bangumi_id, subgroup_id)。导入功能用。"""
    m = _EPISODE_BANGUMI.search(html)
    if not m:
        raise MikanParseError("Episode 页找不到 /Home/Bangumi/{id}#{subgroup} 链接,页面可能已改版")
    return int(m.group(1)), m.group(2)
_BGMTV_SUBJECT = re.compile(r"(?:bgm\.tv|bangumi\.tv|chii\.in)/subject/(\d+)")
_POSTER_URL = re.compile(r"url\('([^']+)'\)")


def parse_search(html: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[SearchResult] = []
    for li in soup.select("ul.list-inline.an-ul li"):
        a = li.select_one('a[href^="/Home/Bangumi/"]')
        if not a:
            continue
        m = _BANGUMI_HREF.match(a["href"])
        if not m:
            continue
        title_el = li.select_one(".an-text") or a
        cover_el = li.select_one("[data-src]")
        out.append(SearchResult(
            mikan_bangumi_id=int(m.group(1)),
            title=(title_el.get("title") or title_el.get_text(strip=True)),
            cover_url=cover_el["data-src"] if cover_el else None))
    return out


def parse_bangumi(html: str, mikan_bangumi_id: int) -> BangumiDetail:
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("p.bangumi-title")
    if title_el is None:
        raise MikanParseError(f"番剧页 {mikan_bangumi_id}: 找不到 p.bangumi-title,页面可能已改版")
    title = title_el.get_text(strip=True)

    poster = None
    poster_el = soup.select_one(".bangumi-poster[style]")
    if poster_el and (m := _POSTER_URL.search(poster_el["style"])):
        poster = m.group(1)

    bgmtv = None
    for a in soup.select("a[href]"):
        if m := _BGMTV_SUBJECT.search(a["href"]):
            bgmtv = int(m.group(1))
            break

    air_date = None
    for el in soup.select(".bangumi-info"):
        text = el.get_text(strip=True)
        if text.startswith(("放送开始", "放送開始")):
            air_date = re.split(r"[:：]", text, maxsplit=1)[-1].strip()
            break

    subgroups: list[Subgroup] = []
    for sg in soup.select(".subgroup-text"):
        sid = sg.get("id")
        if not sid:
            continue
        name_a = sg.select_one("a")
        name = name_a.get_text(strip=True) if name_a else sg.get_text(strip=True).split("\n")[0]
        group = Subgroup(subgroup_id=str(sid), name=name)
        table = sg.find_next("table")
        if table:
            for tr in table.select("tbody tr"):
                link = tr.select_one('a[href^="/Home/Episode/"]')
                if not link:
                    continue
                dl = tr.select_one('a[href$=".torrent"]')
                tds = tr.select("td")
                group.torrents.append(SubgroupTorrent(
                    title=link.get_text(strip=True),
                    episode_url=link["href"],
                    torrent_url=dl["href"] if dl else None,
                    size=tds[1].get_text(strip=True) if len(tds) > 1 else None,
                    published=tds[2].get_text(strip=True) if len(tds) > 2 else None))
        subgroups.append(group)

    return BangumiDetail(mikan_bangumi_id=mikan_bangumi_id, title=title, cover_url=poster,
                         bgmtv_subject_id=bgmtv, air_date_str=air_date, subgroups=subgroups)
