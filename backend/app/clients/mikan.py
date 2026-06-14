"""Mikan 客户端(P1:RSS 拉取 + 种子下载;P2 增加搜索/番剧页)。

RSS 是全量历史(P0 实测 49 条 > 番剧页表 15 条),补齐与轮询共用此数据源。
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

import feedparser

from app.clients.http import make_client
from app.config import settings

log = logging.getLogger(__name__)

# 归一化:转小写、去掉所有非字母数字/中日文字符(标点、空格、!!!!! 等噪声)
_NORM_RE = re.compile(r"[^\w぀-ヿ一-鿿]+")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", (s or "").lower())


def _similarity(a: str, b: str) -> float:
    """两个标题的相似度(0~1),基于归一化后字符串。一方是另一方子串时给高分。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return max(0.92, len(min(na, nb, key=len)) / len(max(na, nb, key=len)))
    return SequenceMatcher(None, na, nb).ratio()


def _candidate_queries(title: str) -> list[str]:
    """容错搜索的候选查询,从具体到宽泛:全名 → 去标点 → 递减词前缀 → 最长词/末词。

    Mikan 的 searchstr 对带 !!!!!/撇号/续作后缀的全名常 0 命中,但短关键词能命中(实测
    「BanG Dream! It's MyGO!!!!!」0 条,而「BanG Dream」「MyGO」都命中)。逐级退化提升匹配率。
    """
    raw = (title or "").strip()
    cleaned = re.sub(r"\s+", " ", _NORM_RE.sub(" ", raw)).strip()
    toks = [t for t in cleaned.split(" ") if len(t) >= 2]
    cands = [raw]
    if cleaned and cleaned.lower() != raw.lower():
        cands.append(cleaned)
    for n in range(len(toks) - 1, 1, -1):    # 前 n-1、… 、前 2 个词(去尾部噪声/季号)
        cands.append(" ".join(toks[:n]))
    if toks:                                  # 最长词、末词(分别作为关键词)
        longest = max(toks, key=len)
        cands.append(longest)
        if toks[-1] != longest:
            cands.append(toks[-1])
    seen, out = set(), []
    for q in cands:
        k = q.lower()
        if len(q) >= 2 and k not in seen:
            seen.add(k)
            out.append(q)
    return out[:6]                            # 限请求数(最坏 6 次)


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

    def search_best(self, title: str, threshold: float = 0.5):
        """自动匹配用:逐级退化搜索,返回与 title 最相似且达阈值的命中(否则 None)。

        给本地导入/番剧库扫描用——文件夹名常是完整作品名(带 !!!!!/续作后缀),直接全名搜
        Mikan 会 0 命中,故退化到关键词再按相似度挑回正确番剧。阈值以下视为没匹配上(交给手动)。
        """
        best, best_score = None, 0.0
        for q in _candidate_queries(title):
            try:
                hits = self.search(q)
            except Exception as e:  # noqa: BLE001
                log.warning("Mikan 搜索 %r 失败: %s", q, e)
                continue
            for h in hits:
                sc = _similarity(title, h.title)
                if sc > best_score:
                    best, best_score = h, sc
            if best and best_score >= 0.75:    # 已足够像 → 提前停,省请求
                break
        if best and best_score >= threshold:
            log.info("Mikan 容错匹配 %r → %r (%.2f)", title, best.title, best_score)
            return best
        return None

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
