"""AniDB 剧集级元数据编排:番剧→aid 匹配 + 剧集表同步(带 ≥24h 缓存)。ADR-0003。

按需触发(详情页「同步 AniDB 剧集」/ BD 库扫描),不在创建时拉(避免批量封 IP)。
未启用/未配置/失败 → 全链路优雅退化(不抛给调用方,返回 {"ok": False, "reason": ...})。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients import anidb, anidb_search
from app.models import Bangumi, Episode, EpisodeType, Kind

log = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=24)


def search_candidates(query: str) -> list[dict]:
    """供 UI 手动选 aid。"""
    return [{"aid": c.aid, "title": c.title} for c in anidb_search.search(query)]


def match_aid(db: Session, b: Bangumi, auto_bind: bool = True) -> int | None:
    """番剧 → aid。已绑直接返回;否则按原名/中文名搜,取首个候选(可手动改)。"""
    if b.anidb_aid:
        return b.anidb_aid
    for q in (b.title_original, b.title):
        cands = anidb_search.search(q) if q else []
        if cands:
            if auto_bind:
                b.anidb_aid = cands[0].aid
                db.flush()
                log.info("AniDB 自动绑定 %s → aid %s (%s)", b.title, cands[0].aid, cands[0].title)
            return cands[0].aid
    return None


def _upsert_episode(db: Session, b: Bangumi, ae: anidb.AnidbEpisode) -> None:
    et = EpisodeType(ae.type) if ae.type in EpisodeType._value2member_map_ else EpisodeType.REGULAR
    # AniDB 正片按季内 1..N 计数;本地 Episode.number 存 bangumi 编号(续作从上季续数)
    # → 正片集号平移 ep_start-1(第2期 AniDB ep1 → 本地 第13话),否则同一集分裂成两行
    number = ae.number
    if et == EpisodeType.REGULAR and number is not None and (b.ep_start or 1) > 1:
        number = number + (b.ep_start - 1)
    # 先按 anidb_eid;再按 (type, number) 回填既有(下载已建但没绑 AniDB 的)
    ep = db.execute(select(Episode).where(Episode.anidb_eid == ae.eid)).scalar_one_or_none()
    if ep is None:
        q = select(Episode).where(Episode.bangumi_id == b.id, Episode.type == et)
        q = q.where(Episode.number == number) if number is not None \
            else q.where(Episode.number.is_(None))
        ep = db.execute(q).scalars().first()
    if ep is None:
        ep = Episode(bangumi_id=b.id, type=et, number=number)
        db.add(ep)
    ep.anidb_eid = ae.eid
    if ae.title and not ep.title:     # 不覆盖已有(可能是用户/其他源填的)
        ep.title = ae.title
    if ae.airdate and not ep.air_date:
        ep.air_date = ae.airdate
    db.flush()


def sync_episodes(db: Session, b: Bangumi, force: bool = False) -> dict:
    """同步 aid 的剧集表到本地。缓存 ≥24h;返回结果摘要(不抛异常)。"""
    if not anidb.enabled():
        return {"ok": False, "reason": "disabled"}
    if not force and b.anidb_synced_at:
        synced = b.anidb_synced_at
        if synced.tzinfo is None:
            synced = synced.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - synced < CACHE_TTL:
            return {"ok": True, "reason": "cached", "aid": b.anidb_aid}
    aid = match_aid(db, b)
    if not aid:
        return {"ok": False, "reason": "no_match"}
    try:
        anime = anidb.fetch_anime(aid)
    except Exception as e:  # noqa: BLE001
        log.warning("AniDB 同步失败 aid=%s: %s", aid, e)
        return {"ok": False, "reason": str(e)}
    # 形态:AniDB anime type 最可靠
    kind_val = anidb.kind_from_anime_type(anime.anime_type)
    b.kind = Kind(kind_val) if kind_val in Kind._value2member_map_ else Kind.TV
    for ae in anime.episodes:
        _upsert_episode(db, b, ae)
    b.anidb_synced_at = datetime.now(timezone.utc)
    db.flush()
    log.info("AniDB 同步完成 %s (aid=%s):%s 集,kind=%s",
             b.title, aid, len(anime.episodes), b.kind.value)
    return {"ok": True, "aid": aid, "episodes": len(anime.episodes), "kind": b.kind.value}
