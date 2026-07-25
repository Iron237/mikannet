"""多源种子搜索:mikan / nyaa / dmhy,统一返回 TorrentResult,供搜索页消费。

- nyaa / dmhy:关键词 → RSS,直接拿到种子级结果(nyaa 自带做种数)。
- mikan:站内搜索是「番剧级」,需先定位番剧再展平其全部字幕组种子。
全部为外部站点,必须走代理(make_client 已按 proxy_services 路由)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import feedparser

from app.clients.http import make_client
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class TorrentResult:
    title: str
    source: str                  # mikan | nyaa | dmhy
    torrent_url: str | None      # .torrent 直链(经代理取回字节再投递)
    magnet: str | None
    page_url: str | None         # 详情页(可外链)
    size: int | None             # 字节
    published_at: datetime | None
    seeders: int | None = None
    leechers: int | None = None
    group_hint: str | None = None   # mikan 字幕组名(标题里常无组名时兜底)


@dataclass
class SeriesHit:
    """mikan 搜索命中的番剧(用于海报 + 选择)。"""
    mikan_bangumi_id: int
    title: str
    cover_url: str | None


# ---- 工具 ------------------------------------------------------------------
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]i?B)", re.I)
_UNIT = {
    "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
}
_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[^\s\"'<>&]+(?:&[a-z]+=[^\s\"'<>]+)*", re.I)


def _parse_size(text: str | None) -> int | None:
    if not text:
        return None
    m = _SIZE_RE.search(text)
    if not m:
        return None
    return int(float(m.group(1)) * _UNIT.get(m.group(2).upper(), 1))


def _published(entry) -> datetime | None:
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6])
    return None


# ---- nyaa ------------------------------------------------------------------
def search_nyaa(keyword: str) -> list[TorrentResult]:
    base = settings.nyaa_base_url.rstrip("/")
    url = f"{base}/?page=rss&q={quote(keyword)}&c=1_0&f=0&s=seeders&o=desc"
    with make_client("nyaa") as c:
        r = c.get(url)
        r.raise_for_status()
    feed = feedparser.parse(r.text)
    out: list[TorrentResult] = []
    for e in feed.entries:
        ih = e.get("nyaa_infohash")
        magnet = f"magnet:?xt=urn:btih:{ih}&dn={quote(e.title)}" if ih else None
        out.append(TorrentResult(
            title=e.title.strip(), source="nyaa",
            torrent_url=e.get("link"), magnet=magnet,
            page_url=e.get("id") or e.get("guid"),
            size=_parse_size(e.get("nyaa_size")),
            published_at=_published(e),
            seeders=int(e.get("nyaa_seeders") or 0),
            leechers=int(e.get("nyaa_leechers") or 0)))
    return out


# ---- dmhy ------------------------------------------------------------------
def search_dmhy(keyword: str) -> list[TorrentResult]:
    base = settings.dmhy_base_url.rstrip("/")
    url = f"{base}/topics/rss/rss.xml?keyword={quote(keyword)}"
    with make_client("dmhy") as c:
        r = c.get(url)
        r.raise_for_status()
    feed = feedparser.parse(r.text)
    out: list[TorrentResult] = []
    for e in feed.entries:
        # dmhy enclosure 的 url 往往就是磁力链;length 多为占位「1」不可信
        href = length = None
        for enc in e.get("enclosures", []):
            if enc.get("href"):
                href = enc["href"]
                length = enc.get("length")
                break
        desc = e.get("summary", "") or e.get("description", "")
        magnet = torrent_url = None
        if href and href.startswith("magnet:"):
            magnet = href
        else:
            torrent_url = href
        if not magnet and (mm := _MAGNET_RE.search(desc)):
            magnet = mm.group(0)
        size = int(length) if length and str(length).isdigit() and int(length) > 1 else _parse_size(desc)
        out.append(TorrentResult(
            title=e.title.strip(), source="dmhy",
            torrent_url=torrent_url, magnet=magnet,
            page_url=e.get("link"), size=size,
            published_at=_published(e)))
    return out


# ---- mikan -----------------------------------------------------------------
def search_mikan_series(keyword: str) -> list[SeriesHit]:
    from app.clients.mikan import mikan_client
    hits = mikan_client.search(keyword)
    return [SeriesHit(h.mikan_bangumi_id, h.title, h.cover_url) for h in hits]


def mikan_bangumi_torrents(mikan_bangumi_id: int) -> tuple[SeriesHit, list[TorrentResult]]:
    """番剧页 → (番剧信息, 展平全部字幕组的种子)。"""
    from app.clients.mikan import mikan_client
    base = settings.mikan_base_url.rstrip("/")
    d = mikan_client.get_bangumi(mikan_bangumi_id)
    out: list[TorrentResult] = []
    for g in d.subgroups:
        for t in g.torrents:
            turl = t.torrent_url
            if turl and turl.startswith("/"):
                turl = base + turl
            page = t.episode_url
            if page and page.startswith("/"):
                page = base + page
            out.append(TorrentResult(
                title=t.title, source="mikan", torrent_url=turl, magnet=None,
                page_url=page, size=_parse_size(t.size), published_at=None,
                group_hint=g.name))
    hit = SeriesHit(d.mikan_bangumi_id, d.title, d.cover_url)
    return hit, out
