"""番剧标题 → AniDB aid:第三方托管搜索(anidb-search,MIT,支持中文/拼音)。

AniDB 官方 HTTP 无搜索接口(只有 anime-titles.dat.gz 全量转储)。anidb-search 封装了它
并加了中文/拼音匹配。base URL 可在设置里改成自托管(它开源)。见 ADR-0003。
响应形如 {"中文标题": aid, ...};也兼容 [{"title":..,"aid":..}] 形态。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clients.http import make_client
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class AnidbCandidate:
    aid: int
    title: str


def _parse(data) -> list[AnidbCandidate]:
    out: list[AnidbCandidate] = []
    if isinstance(data, dict):
        for title, aid in data.items():
            try:
                out.append(AnidbCandidate(aid=int(aid), title=str(title)))
            except (TypeError, ValueError):
                continue
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and it.get("aid"):
                out.append(AnidbCandidate(aid=int(it["aid"]),
                                          title=str(it.get("title") or it.get("name") or "")))
    return out


def search(query: str, lang: str | None = None) -> list[AnidbCandidate]:
    """返回候选 aid 列表(按服务端给的顺序,通常相关度优先)。失败返回 []。"""
    q = (query or "").strip()
    if not q:
        return []
    base = settings.anidb_search_base.rstrip("/")
    try:
        with make_client("anidb") as c:
            r = c.get(f"{base}/api/s", params={"content": q, "lang": lang or settings.anidb_lang})
            r.raise_for_status()
            return _parse(r.json())
    except Exception as e:  # noqa: BLE001 — 第三方挂掉不阻塞,退化到手动绑定/文件名启发式
        log.warning("anidb-search 失败 q=%s: %s", q, e)
        return []


anidb_search_client = search
