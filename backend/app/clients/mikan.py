"""Mikan 客户端(P1:RSS 拉取 + 种子下载;P2 增加搜索/番剧页)。

RSS 是全量历史(P0 实测 49 条 > 番剧页表 15 条),补齐与轮询共用此数据源。
"""
from dataclasses import dataclass
from datetime import datetime

import feedparser

from app.clients.http import make_client
from app.config import settings


@dataclass
class RssItem:
    guid: str            # Episode 页 URL,全站唯一
    title: str
    torrent_url: str
    size: int | None
    published_at: datetime | None


class MikanClient:
    def __init__(self) -> None:
        self.base = settings.mikan_base_url.rstrip("/")

    def search(self, keyword: str):
        from app.parsers.mikan_html import parse_search
        with make_client("mikan") as c:
            r = c.get(f"{self.base}/Home/Search", params={"searchstr": keyword})
            r.raise_for_status()
        return parse_search(r.text)

    def get_bangumi(self, mikan_bangumi_id: int):
        from app.parsers.mikan_html import parse_bangumi
        with make_client("mikan") as c:
            r = c.get(f"{self.base}/Home/Bangumi/{mikan_bangumi_id}")
            r.raise_for_status()
        return parse_bangumi(r.text, mikan_bangumi_id)

    def get_episode_origin(self, episode_url: str) -> tuple[int, str]:
        """Episode 页 URL(绝对或相对)→ (bangumi_id, subgroup_id)。"""
        from app.parsers.mikan_html import parse_episode
        path = episode_url
        if path.startswith("http"):
            path = "/" + path.split("/", 3)[-1]
        with make_client("mikan") as c:
            r = c.get(self.base + path)
            r.raise_for_status()
        return parse_episode(r.text)

    def fetch_rss_url(self, rss_url: str):
        """拉任意 Mikan RSS(个人聚合订阅导入用),复用条目解析。"""
        with make_client("mikan") as c:
            r = c.get(rss_url)
            r.raise_for_status()
        return self._parse_feed(r.text)

    def download_image(self, relative_url: str) -> bytes:
        with make_client("mikan") as c:
            r = c.get(self.base + relative_url)
            r.raise_for_status()
            return r.content

    def fetch_rss(self, mikan_bangumi_id: int, subgroup_id: str) -> list[RssItem]:
        with make_client("mikan") as c:
            r = c.get(f"{self.base}/RSS/Bangumi",
                      params={"bangumiId": mikan_bangumi_id, "subgroupid": subgroup_id})
            r.raise_for_status()
        return self._parse_feed(r.text)

    def _parse_feed(self, xml: str) -> list[RssItem]:
        feed = feedparser.parse(xml)
        items: list[RssItem] = []
        for e in feed.entries:
            torrent_url = next((l["href"] for l in e.get("links", [])
                                if l.get("type") == "application/x-bittorrent"), None)
            if not torrent_url or not e.get("link"):
                continue
            length = next((l.get("length") for l in e.get("links", [])
                           if l.get("type") == "application/x-bittorrent"), None)
            published = None
            if e.get("published_parsed"):
                published = datetime(*e.published_parsed[:6])
            items.append(RssItem(
                guid=e.link, title=e.title.strip(), torrent_url=torrent_url,
                size=int(length) if length else None, published_at=published))
        return items

    def download_torrent(self, torrent_url: str) -> bytes:
        """qB 容器无代理,.torrent 必须由 app 经代理取回字节再投递(PROBE-NOTES)。"""
        with make_client("mikan") as c:
            r = c.get(torrent_url)
            r.raise_for_status()
            return r.content


mikan_client = MikanClient()
