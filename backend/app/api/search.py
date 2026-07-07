"""Mikan 站内搜索(订阅向导第 1-3 步数据源)。

浏览器无法直连 Mikan(需代理),封面经 /api/search/cover 由后端代理+磁盘缓存。
"""
import hashlib
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.clients.mikan import mikan_client
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])

_CACHE_DIR = settings.data_dir / "images" / "mikan-cache"


@router.get("")
def search(keyword: str):
    if not keyword.strip():
        raise HTTPException(400, "keyword 不能为空")
    try:
        results = mikan_client.search(keyword)
    except Exception as e:  # noqa: BLE001 — Mikan 不可达/解析失败 → 502 而非 500
        log.warning("搜索失败 kw=%s: %s", keyword, e)
        raise HTTPException(502, f"Mikan 搜索失败:{e}") from e
    return [{"mikan_bangumi_id": r.mikan_bangumi_id, "title": r.title,
             "cover": f"/api/search/cover?path={r.cover_url}" if r.cover_url else None}
            for r in results]


def _chips(title: str, group_hint: str | None) -> dict:
    from app.parsers.title_parser import detect_subtitle_tags, episode_label, parse
    p = parse(title)
    return {
        "group": p.group or group_hint,
        "resolution": p.resolution,
        "episode": episode_label(p),
        "version": p.version,
        "is_batch": p.is_batch,
        "source": p.source,                 # Web / BD
        "ep_type": p.ep_type,               # regular/special/credits/trailer/other
        "subtitle_tags": detect_subtitle_tags(title),
    }


def _group_caps(titles: list[str]) -> dict:
    """字幕组能力摘要:聚合该组全部发布的分辨率/字幕语言/片源/有无合集(向导第 2 步 chips)。"""
    from app.parsers.title_parser import detect_subtitle_tags, parse
    resolutions, langs, sources = {}, {}, {}
    has_batch = False
    for t in titles:
        p = parse(t)
        if p.resolution:
            resolutions[p.resolution] = resolutions.get(p.resolution, 0) + 1
        if p.source:
            sources[p.source] = sources.get(p.source, 0) + 1
        for lang in detect_subtitle_tags(t):
            langs[lang] = langs.get(lang, 0) + 1
        has_batch = has_batch or p.is_batch
    # 按出现频次降序,保留标签
    pick = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])]   # noqa: E731
    return {"resolutions": pick(resolutions), "subtitle_langs": pick(langs),
            "sources": pick(sources), "has_batch": has_batch}


def _torrent_dict(t) -> dict:
    return {
        "title": t.title, "source": t.source,
        "torrent_url": t.torrent_url, "magnet": t.magnet, "page_url": t.page_url,
        "size": t.size,
        "published_at": t.published_at.isoformat() if t.published_at else None,
        "seeders": t.seeders, "leechers": t.leechers,
        "chips": _chips(t.title, t.group_hint),
    }


@router.get("/multi")
def multi_search(source: str, keyword: str = "", bangumi_id: int | None = None):
    """多源搜索(搜索页)。
    - nyaa / dmhy:keyword → 种子级结果。
    - mikan:未给 bangumi_id → 返回命中的番剧列表(选海报);给了 → 返回该番剧全部种子。
    返回 {source, series:[...], current_series:{...}|None, torrents:[...]}。
    """
    from app.clients import sources
    src = source.lower()
    try:
        if src == "nyaa":
            torrents = sources.search_nyaa(keyword) if keyword.strip() else []
            return {"source": src, "series": [], "current_series": None,
                    "torrents": [_torrent_dict(t) for t in torrents]}
        if src == "dmhy":
            torrents = sources.search_dmhy(keyword) if keyword.strip() else []
            return {"source": src, "series": [], "current_series": None,
                    "torrents": [_torrent_dict(t) for t in torrents]}
        if src == "mikan":
            if bangumi_id:
                hit, torrents = sources.mikan_bangumi_torrents(bangumi_id)
                return {
                    "source": src, "series": [],
                    "current_series": {"mikan_bangumi_id": hit.mikan_bangumi_id, "title": hit.title,
                                       "cover": f"/api/search/cover?path={hit.cover_url}" if hit.cover_url else None},
                    "torrents": [_torrent_dict(t) for t in torrents]}
            hits = sources.search_mikan_series(keyword) if keyword.strip() else []
            return {
                "source": src, "current_series": None, "torrents": [],
                "series": [{"mikan_bangumi_id": h.mikan_bangumi_id, "title": h.title,
                            "cover": f"/api/search/cover?path={h.cover_url}" if h.cover_url else None}
                           for h in hits]}
        raise HTTPException(400, f"未知来源:{source}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("多源搜索失败 source=%s kw=%s: %s", source, keyword, e)
        raise HTTPException(502, f"{source} 搜索失败:{e}") from e


@router.get("/bangumi/{mikan_bangumi_id}")
def bangumi_detail(mikan_bangumi_id: int):
    """番剧页:字幕组列表 + 各组最近发布(向导选组依据)+ bgm.tv 关联。"""
    try:
        d = mikan_client.get_bangumi(mikan_bangumi_id)
    except Exception as e:  # noqa: BLE001 — HTML 结构变化/番剧页缺失 → 502 而非 500
        log.warning("番剧页解析失败 id=%s: %s", mikan_bangumi_id, e)
        raise HTTPException(502, f"Mikan 番剧页获取失败:{e}") from e
    return {
        "mikan_bangumi_id": d.mikan_bangumi_id,
        "title": d.title,
        "cover": f"/api/search/cover?path={d.cover_url}" if d.cover_url else None,
        "bgmtv_subject_id": d.bgmtv_subject_id,
        "air_date": d.air_date_str,
        "subgroups": [{
            "subgroup_id": g.subgroup_id, "name": g.name,
            "torrent_count": len(g.torrents),
            "caps": _group_caps([t.title for t in g.torrents]),
            "recent_titles": [t.title for t in g.torrents[:5]],   # 兼容旧前端
            "recent": [{"title": t.title, "chips": _chips(t.title, g.name)}
                       for t in g.torrents[:5]],
        } for g in d.subgroups],
    }


@router.get("/preview")
def preview(bangumi_id: int, subgroup_id: str, include: str = "", exclude: str = "",
            exclude_batch: bool = False):
    """订阅向导第 3 步:字幕组全部发布源 + 按当前规则的实时通过/排除判定。"""
    from app.parsers.title_parser import parse
    from app.services.rss_engine import passes_filters
    inc = [k for k in include.split() if k]
    exc = [k for k in exclude.split() if k]
    items = mikan_client.fetch_rss(bangumi_id, subgroup_id)
    out = []
    for it in items:
        parsed = parse(it.title)
        ok, reason = passes_filters(it.title, parsed, inc, exc, exclude_batch)
        out.append({
            "guid": it.guid, "title": it.title, "size": it.size,
            "torrent_url": it.torrent_url,
            "published_at": it.published_at.isoformat() if it.published_at else None,
            "episodes": parsed.episodes[:2] + (["…"] if len(parsed.episodes) > 2 else []),
            # 完整集号列表(不截断)供前端算「完整度」覆盖:曾复用上面截断版,
            # [01-24] 合集被当只覆盖 2 集 → 误报缺 3-24
            "episodes_full": parsed.episodes,
            "is_batch": parsed.is_batch, "version": parsed.version,
            "chips": _chips(it.title, None),    # 集号/分辨率/字幕语言/片源/合集/版本(tag 化展示)
            "pass": ok, "reason": reason,
        })
    out.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return out


@router.post("/scrape")
def scrape(payload: dict):
    """批量探测种子活跃度。payload: {"torrent_urls": [...]}(≤40 条)"""
    from app.services.scrape import scrape_many
    urls = (payload.get("torrent_urls") or [])[:40]
    if not urls:
        raise HTTPException(400, "缺少 torrent_urls")
    return scrape_many(urls)


@router.get("/cover")
def cover(path: str):
    if not path.startswith("/images/"):
        raise HTTPException(400, "仅允许 Mikan 图片路径")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _CACHE_DIR / (hashlib.md5(path.encode()).hexdigest()[:16] + ".img")
    if not cached.exists():
        try:
            cached.write_bytes(mikan_client.download_image(path))
        except Exception as e:  # noqa: BLE001
            log.warning("封面代理失败 %s: %s", path, e)
            raise HTTPException(502, "封面获取失败") from e
    return Response(cached.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})
