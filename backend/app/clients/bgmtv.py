"""bgm.tv v0 API 客户端:番剧元数据(年代/制作公司/简介/官方译名/评分)+ 标题搜索。

字段依据 P0 探针(fixtures/bgmtv_subject_486347.json)。

搜索用于本地导入匹配:蜜柑 /Home/Search 只索引罗马音标题(中文/日文标题一律 0 命中),
而本地番剧文件夹多用中文名 → 必须用 bgm.tv 搜(原生索引中文 name_cn + 日文 name)。
"""
import logging
import re
import urllib.parse
from dataclasses import dataclass

from app.clients.http import make_client

log = logging.getLogger(__name__)

_BRACKET_RE = re.compile(r"[\[\(【][^\]\)】]*[\]\)】]")


def _query_variants(title: str) -> list[str]:
    """搜索查询逐级退化:去括号名 → 主标题(逗号/波浪号前) → 原名。

    本地作品文件夹两种形态:① 干净中文名(`比宇宙更遥远的地方`,去括号是空操作);
    ② 罗马音发布名带噪声(`[Sakurato] Watashi… [01-12Fin][HEVC-10bit][CHS&CHT]`)——
    整串带字幕组/集数/编码标签会让 bgm.tv 0 命中,去括号后 `Watashi… , Muri Muri` 才命中。
    """
    raw = (title or "").strip()
    debr = re.sub(r"\s+", " ", _BRACKET_RE.sub(" ", raw)).strip()
    base = debr or raw
    precomma = re.split(r"[,，、~〜]", base, maxsplit=1)[0].strip()
    seen, out = set(), []
    for q in (debr, precomma, raw):
        k = q.lower()
        if len(q) >= 2 and k not in seen:
            seen.add(k)
            out.append(q)
    return out

API = "https://api.bgm.tv"
UA = {"User-Agent": "mikanarr/0.1 (https://github.com/local/mikanarr)"}


@dataclass
class BgmtvSubject:
    subject_id: int
    name: str                 # 原名
    name_cn: str | None       # 官方中文译名
    date: str | None          # "2025-01-10" → 年代
    platform: str | None      # TV / 剧场版
    eps: int | None
    studio: str | None        # infobox 动画制作
    score: float | None
    summary: str | None
    cover_url: str | None


@dataclass
class BgmtvSearchResult:
    subject_id: int
    name: str                 # 原名(日文/罗马音)
    name_cn: str | None       # 中文译名


class BgmtvClient:
    def get_subject(self, subject_id: int) -> BgmtvSubject:
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(f"{API}/v0/subjects/{subject_id}")
            r.raise_for_status()
            d = r.json()
        infobox = {i["key"]: i["value"] for i in d.get("infobox", [])
                   if isinstance(i.get("value"), str)}
        return BgmtvSubject(
            subject_id=subject_id,
            name=d.get("name") or "",
            name_cn=d.get("name_cn") or None,
            date=d.get("date"),
            platform=d.get("platform"),
            eps=d.get("eps") or None,
            studio=infobox.get("动画制作") or infobox.get("製作") or infobox.get("制作"),
            score=(d.get("rating") or {}).get("score"),
            summary=d.get("summary") or None,
            cover_url=(d.get("images") or {}).get("large"))

    def search(self, keyword: str, limit: int = 6) -> list[BgmtvSearchResult]:
        """搜动画条目。v0 模糊搜索优先(中日文命中好),空则退 legacy(对罗马音/别名更宽)。"""
        out = self._search_v0(keyword, limit)
        return out or self._search_legacy(keyword, limit)

    def _search_v0(self, keyword: str, limit: int) -> list[BgmtvSearchResult]:
        try:
            with make_client("bgmtv", headers=UA) as c:
                r = c.post(f"{API}/v0/search/subjects", params={"limit": limit},
                           json={"keyword": keyword, "filter": {"type": [2]}})
                r.raise_for_status()
                data = (r.json() or {}).get("data") or []
        except Exception as e:  # noqa: BLE001
            log.warning("bgm.tv v0 搜索 %r 失败: %s", keyword, e)
            return []
        return [BgmtvSearchResult(x["id"], x.get("name") or "", x.get("name_cn") or None)
                for x in data if x.get("id")]

    def _search_legacy(self, keyword: str, limit: int) -> list[BgmtvSearchResult]:
        url = f"{API}/search/subject/{urllib.parse.quote(keyword, safe='')}"
        try:
            with make_client("bgmtv", headers=UA) as c:
                r = c.get(url, params={"type": 2, "responseGroup": "small",
                                       "max_results": limit})
                if r.status_code == 404:   # legacy 无结果返回 404 错误体
                    return []
                r.raise_for_status()
                data = (r.json() or {}).get("list") or []
        except Exception as e:  # noqa: BLE001
            log.warning("bgm.tv legacy 搜索 %r 失败: %s", keyword, e)
            return []
        return [BgmtvSearchResult(x["id"], x.get("name") or "", x.get("name_cn") or None)
                for x in data if x.get("id")]

    def search_best(self, title: str, threshold: float = 0.5):
        """本地导入匹配:去括号逐级退化搜,返回最佳条目(否则 None)。

        bgm.tv 相关度排序已较准,且原生支持中文/日文/罗马音别名。对首个有结果的查询形态:
        与候选 name/name_cn 取最大相似度,达阈值取最像的;否则信 bgm.tv 排序首条
        (跨语言相似度常为 0,如罗马音 `Watashi…`→中文标题;bgm.tv 排序仍能选对季)。
        """
        from app.clients.mikan import _similarity   # 复用归一化+子串相似度(保留 CJK)

        for q in _query_variants(title):
            cands = self.search(q)
            if not cands:
                continue

            def sim(c: BgmtvSearchResult) -> float:
                return max(_similarity(q, c.name), _similarity(q, c.name_cn or ""))

            best = max(cands, key=sim)
            score = sim(best)
            if score >= threshold:
                log.info("bgm.tv 匹配 %r →(%r)→ %r (相似 %.2f)", title, q,
                         best.name_cn or best.name, score)
                return best
            log.info("bgm.tv 匹配 %r →(%r)→ %r (排序首条,跨语言)", title, q,
                     cands[0].name_cn or cands[0].name)
            return cands[0]
        return None

    def download_image(self, url: str) -> bytes:
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content


bgmtv_client = BgmtvClient()
