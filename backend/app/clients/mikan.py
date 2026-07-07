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


def _dedup(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for q in seq:
        k = q.strip().lower()
        if len(q.strip()) >= 2 and k not in seen:
            seen.add(k)
            out.append(q.strip())
    return out


def _query_tiers(title: str) -> tuple[list[str], list[str]]:
    """搜索查询分两级:
    - strong(全标题级):原名、去括号名、去标点名。够特异,可信蜜柑相关度首条(即便跨语言)。
    - weak(关键词级):递减词前缀、最长词、末词。易误配(如「Punch」→ Punch Line),须相似度兜底。

    Mikan 的 searchstr 对带 !!!!!/撇号/字幕组前缀/技术标签的名常 0 命中,故逐级退化。
    去括号:`[LPSub] BanG Dream MyGO [01-13]` → `BanG Dream MyGO`;`[DMG] BOCCHI THE ROCK! [Ma10p]` → `BOCCHI THE ROCK!`。
    """
    raw = (title or "").strip()
    debr = re.sub(r"\s+", " ", re.sub(r"[\[\(【][^\]\)】]*[\]\)】]", " ", raw)).strip()
    base = debr or raw
    # 逗号/顿号/波浪号前的主标题:副标题(", Muri Muri"、"〜再次闪耀〜")常使全名 0 命中,主标题能中
    precomma = re.split(r"[,，、~〜]", base, maxsplit=1)[0].strip()
    cleaned = re.sub(r"\s+", " ", _NORM_RE.sub(" ", base)).strip()
    toks = [t for t in cleaned.split(" ") if len(t) >= 2]
    weak = [" ".join(toks[:n]) for n in range(len(toks) - 1, 1, -1)]
    if toks:
        weak.append(max(toks, key=len))   # 最长词
        weak.append(toks[-1])             # 末词
    return _dedup([raw, debr, precomma, cleaned]), _dedup(weak)[:4]


@dataclass
class RssItem:
    guid: str            # Episode 页 URL,全站唯一
    title: str
    torrent_url: str
    size: int | None
    published_at: datetime | None


class MikanClient:
    @property
    def base(self) -> str:
        # 实时读设置:单例在 load_overrides() 之前就构造,且设置页可随时改域名(镜像切换)。
        # 构造期缓存会导致改域名永不生效(请求打旧域名)。
        return settings.mikan_base_url.rstrip("/")

    def search(self, keyword: str):
        from app.parsers.mikan_html import parse_search
        with make_client("mikan") as c:
            r = c.get(f"{self.base}/Home/Search", params={"searchstr": keyword})
            r.raise_for_status()
        return parse_search(r.text)

    def _search_safe(self, q: str):
        try:
            return self.search(q)
        except Exception as e:  # noqa: BLE001
            log.warning("Mikan 搜索 %r 失败: %s", q, e)
            return []

    def search_best(self, title: str, threshold: float = 0.5):
        """自动匹配用:strong(全名)→ weak(关键词)两级退化,返回最佳命中(否则 None)。

        给本地导入/番剧库扫描用。strong 查询够特异 → 即便跨语言(罗马音/日文文件夹名 →
        蜜柑中文标题,相似度为 0)也信蜜柑相关度首条;weak 关键词易误配 → 必须相似度达阈值。
        都不中 → 视为没匹配上(交给手动指定),不硬塞错的。
        """
        strong, weak = _query_tiers(title)
        # 1) strong:全标题级。命中里若有够像的取最像(同系列分季靠它选对季);否则信蜜柑首条
        for q in strong:
            hits = self._search_safe(q)
            if not hits:
                continue
            best = max(hits, key=lambda h: _similarity(title, h.title))
            sim = _similarity(title, best.title)
            if sim >= threshold:
                log.info("Mikan 匹配 %r → %r (强/相似 %.2f)", title, best.title, sim)
                return best
            if len(hits) <= 5:               # 跨语言:够特异(结果不发散)→ 信首条
                log.info("Mikan 匹配 %r → %r (强/跨语言首条)", title, hits[0].title)
                return hits[0]
        # 2) weak:关键词级,必须够像才接受
        best, best_score = None, 0.0
        for q in weak:
            for h in self._search_safe(q):
                sc = _similarity(title, h.title)
                if sc > best_score:
                    best, best_score = h, sc
            if best_score >= 0.75:
                break
        if best and best_score >= threshold:
            log.info("Mikan 匹配 %r → %r (弱/相似 %.2f)", title, best.title, best_score)
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
        """qB 容器无代理,.torrent 必须由 app 经代理取回字节再投递(PROBE-NOTES)。

        番剧页解析出的下载链接是相对路径(/Download/...torrent),RSS 的是绝对 URL → 兼容两者。
        """
        url = torrent_url if torrent_url.startswith("http") else self.base + "/" + torrent_url.lstrip("/")
        with make_client("mikan") as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content


mikan_client = MikanClient()
