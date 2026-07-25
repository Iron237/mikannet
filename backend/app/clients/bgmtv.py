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
UA = {"User-Agent": "mikannet/0.1 (https://github.com/local/mikannet)"}


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


@dataclass
class BgmtvEpisode:
    """bgm.tv 章节(/v0/episodes):每集精确放送日 + 中文标题的数据源。"""
    ep_id: int                # bgm.tv 章节 id(收视进度回写用)
    type: int                 # 0 本篇 / 1 SP / 2 OP / 3 ED
    sort: float               # 章节话数(bangumi 编号,续作从上季续数)
    name: str
    name_cn: str | None
    airdate: str | None       # "2026-07-09";未定/未知为 None


@dataclass
class RelatedSubject:
    """关联条目(/v0/subjects/{id}/subjects):前作/续作/剧场版/OVA 等。"""
    subject_id: int
    type: int                 # 2=动画
    relation: str             # 续集/前传/剧场版/番外篇…
    name: str
    name_cn: str | None
    image: str | None


class BgmtvClient:
    def _headers(self, auth: bool = False) -> dict:
        """带 UA;auth=True 且配置了个人令牌时附 Bearer(收藏读写用)。"""
        if auth:
            from app.config import settings
            tok = (settings.bgmtv_access_token or "").strip()
            if tok:
                return {**UA, "Authorization": f"Bearer {tok}"}
        return dict(UA)

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

    def first_ep_sort(self, subject_id: int) -> int | None:
        """本篇(type=0)首话的 sort(bgm.tv 章节列表显示的话数)。

        续作条目常从上季续数(第2期章节列表 13-25 → 返回 13);从 1 数则返回 1。
        取不到(无章节/异常结构)→ None,调用方按 1 处理。"""
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(f"{API}/v0/episodes",
                      params={"subject_id": subject_id, "type": 0, "limit": 1, "offset": 0})
            r.raise_for_status()
            data = (r.json() or {}).get("data") or []
        if not data:
            return None
        sort = data[0].get("sort") or data[0].get("ep")
        try:
            n = int(float(sort))
            return n if n >= 1 else None
        except (TypeError, ValueError):
            return None

    def episodes(self, subject_id: int, ep_type: int = 0) -> list[BgmtvEpisode]:
        """全量拉章节(分页,type=0 本篇)。每集含精确 airdate + 中文标题。"""
        out: list[BgmtvEpisode] = []
        offset = 0
        with make_client("bgmtv", headers=UA) as c:
            while True:
                r = c.get(f"{API}/v0/episodes",
                          params={"subject_id": subject_id, "type": ep_type,
                                  "limit": 100, "offset": offset})
                r.raise_for_status()
                j = r.json() or {}
                data = j.get("data") or []
                for x in data:
                    try:
                        sort = float(x.get("sort") if x.get("sort") is not None else x.get("ep") or 0)
                    except (TypeError, ValueError):
                        continue
                    ad = (x.get("airdate") or "").strip()
                    out.append(BgmtvEpisode(
                        ep_id=int(x["id"]), type=int(x.get("type") or 0), sort=sort,
                        name=x.get("name") or "", name_cn=x.get("name_cn") or None,
                        airdate=ad if len(ad) >= 8 else None))
                offset += len(data)
                if len(data) < 100 or offset >= int(j.get("total") or 0):
                    break
        return out

    def related_subjects(self, subject_id: int) -> list[RelatedSubject]:
        """关联条目:前作/续作/剧场版/OVA 等(全类型返回,调用方自行过滤)。"""
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(f"{API}/v0/subjects/{subject_id}/subjects")
            r.raise_for_status()
            data = r.json() or []
        return [RelatedSubject(
            subject_id=int(x["id"]), type=int(x.get("type") or 0),
            relation=x.get("relation") or "", name=x.get("name") or "",
            name_cn=x.get("name_cn") or None,
            image=(x.get("images") or {}).get("common") or (x.get("images") or {}).get("large"))
            for x in data if x.get("id")]

    def weekly_calendar(self) -> list[dict]:
        """每日放送(legacy /calendar,无鉴权):当季全站番剧按星期分组,发现页数据源。

        返回原始结构:[{weekday:{id 1-7,cn,...}, items:[{id,name,name_cn,air_date,
        rating:{score},images:{large},eps/eps_count,...}]}]。
        """
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(f"{API}/calendar")
            r.raise_for_status()
            return r.json() or []

    # ---- 收藏联动(需个人令牌 next.bgm.tv/demo/access-token)------------------

    def me(self) -> dict | None:
        """当前令牌对应的用户;未配置/无效令牌 → None。"""
        with make_client("bgmtv", headers=self._headers(auth=True)) as c:
            r = c.get(f"{API}/v0/me")
            if r.status_code in (401, 403):
                return None
            r.raise_for_status()
            return r.json()

    def watching(self, username: str) -> list[dict]:
        """用户「在看」的动画收藏(分页拉全)。"""
        out: list[dict] = []
        offset = 0
        with make_client("bgmtv", headers=self._headers(auth=True)) as c:
            while True:
                r = c.get(f"{API}/v0/users/{username}/collections",
                          params={"subject_type": 2, "type": 3,   # 2=动画 3=在看
                                  "limit": 50, "offset": offset})
                r.raise_for_status()
                j = r.json() or {}
                data = j.get("data") or []
                out.extend(data)
                offset += len(data)
                if len(data) < 50 or offset >= int(j.get("total") or 0):
                    break
        return out

    def mark_subject_watching(self, subject_id: int) -> None:
        """把条目标为「在看」(幂等:已有收藏则改状态)。"""
        with make_client("bgmtv", headers=self._headers(auth=True)) as c:
            r = c.post(f"{API}/v0/users/-/collections/{subject_id}", json={"type": 3})
            if r.status_code not in (200, 201, 202, 204):
                r.raise_for_status()

    def mark_episode_watched(self, episode_id: int) -> None:
        """把单集标为「看过」(收视进度回写)。"""
        with make_client("bgmtv", headers=self._headers(auth=True)) as c:
            r = c.put(f"{API}/v0/users/-/collections/-/episodes/{episode_id}",
                      json={"type": 2})   # 2=看过
            if r.status_code not in (200, 201, 202, 204):
                r.raise_for_status()

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
