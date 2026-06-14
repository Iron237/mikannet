"""AniDB 官方 HTTP API:aid → 剧集表 + 作品类型。ADR-0003。

约束(违反会被封 IP):串行、每次请求间隔 ≥2s、需注册 client 名、结果必须本地缓存。
本模块只负责单次取数 + 限速;缓存(≥24h)与编排在 services/anidb_service.py。
未配置 client 名 → enabled=False,调用方退化到文件名启发式。
"""
from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from app.clients.http import make_client
from app.config import settings

log = logging.getLogger(__name__)

API = "http://api.anidb.net:9001/httpapi"
MIN_INTERVAL = 2.5   # 秒;官方要求 ≥2s,留余量

# AniDB epno type → EpisodeType 值(对齐 models.EpisodeType)
_EPTYPE = {1: "regular", 2: "special", 3: "credits", 4: "trailer", 5: "other", 6: "other"}

_lock = threading.Lock()
_last_call = 0.0


@dataclass
class AnidbEpisode:
    eid: int
    type: str            # regular/special/credits/trailer/other
    number: float | None  # epno 数字部分(S1→1、C2→2);解析不出 None
    title: str | None
    airdate: str | None


@dataclass
class AnidbAnime:
    aid: int
    anime_type: str                  # 原始 <type>(TV Series / Movie / OVA / Web / …)
    episodes: list[AnidbEpisode] = field(default_factory=list)


def enabled() -> bool:
    return bool(settings.anidb_enabled and settings.anidb_client_name)


def _pick_title(ep: ET.Element, lang_pref: str) -> str | None:
    """剧集名:首选配置语言(zh-Hans)→ romaji(x-jat)→ 英文 → 任一。"""
    titles = {(t.get("{http://www.w3.org/XML/1998/namespace}lang") or ""): (t.text or "")
              for t in ep.findall("title")}
    for lang in (lang_pref, "zh-Hans", "x-jat", "en", "ja"):
        if titles.get(lang):
            return titles[lang]
    return next((v for v in titles.values() if v), None)


def _epno(el: ET.Element | None) -> tuple[str, float | None]:
    """<epno type=N> → (EpisodeType值, 数字)。'S1'/'C2'→去字母取数。"""
    if el is None:
        return "regular", None
    etype = _EPTYPE.get(int(el.get("type") or 1), "regular")
    digits = "".join(ch for ch in (el.text or "") if ch.isdigit() or ch == ".")
    try:
        return etype, float(digits) if digits else None
    except ValueError:
        return etype, None


def fetch_anime(aid: int) -> AnidbAnime:
    """拉取 aid 的剧集表。限速串行;失败/被封抛异常。"""
    if not enabled():
        raise RuntimeError("AniDB 未启用或未配置 client 名")
    global _last_call
    with _lock:
        gap = MIN_INTERVAL - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        params = {"request": "anime", "client": settings.anidb_client_name,
                  "clientver": settings.anidb_client_ver, "protover": 1, "aid": aid}
        try:
            with make_client("anidb") as c:
                r = c.get(API, params=params)
                r.raise_for_status()
                xml = r.text
        finally:
            _last_call = time.monotonic()

    root = ET.fromstring(xml)
    if root.tag == "error":
        raise RuntimeError(f"AniDB 错误: {root.text}")

    anime_type = (root.findtext("type") or "").strip()
    eps: list[AnidbEpisode] = []
    for ep in root.findall("./episodes/episode"):
        etype, number = _epno(ep.find("epno"))
        eps.append(AnidbEpisode(
            eid=int(ep.get("id") or 0), type=etype, number=number,
            title=_pick_title(ep, settings.anidb_lang),
            airdate=ep.findtext("airdate") or None))
    return AnidbAnime(aid=aid, anime_type=anime_type, episodes=eps)


def kind_from_anime_type(anime_type: str) -> str:
    """AniDB <type> → Kind 值(tv/movie/ova)。"""
    t = (anime_type or "").lower()
    if "movie" in t:
        return "movie"
    if "ova" in t or "oad" in t:
        return "ova"
    return "tv"
