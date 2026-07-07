"""RSS 引擎:条目 → 解析 → 过滤 → 去重(含 v2)→ 提交 qB 的状态机主管线。

唯一向 torrent 表写入新行的入口。补齐与轮询共用本管线(数据源同为 RSS,全量历史)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.clients.mikan import RssItem, mikan_client
from app.models import (Episode, EpisodeType, Subscription, Torrent, TorrentEpisode,
                        TorrentStatus)
from app.parsers.title_parser import ParsedTitle, parse

log = logging.getLogger(__name__)

SUBMIT_RETRIES = 3


# ---- 过滤 ---------------------------------------------------------------------

def passes_filters(title: str, parsed: ParsedTitle, include: list[str], exclude: list[str],
                   exclude_batch: bool) -> tuple[bool, str]:
    """返回 (是否通过, 不通过原因)。include=AND,exclude=任一命中即排除。"""
    for kw in (include or []):
        if kw.lower() not in title.lower():
            return False, f"缺少包含关键词: {kw}"
    for kw in (exclude or []):
        if kw.lower() in title.lower():
            return False, f"命中排除关键词: {kw}"
    if parsed.is_batch and exclude_batch:
        return False, "合集(订阅设置排除)"
    return True, ""


def _passes_filters(sub: Subscription, title: str, parsed: ParsedTitle) -> tuple[bool, str]:
    return passes_filters(title, parsed, sub.include_keywords, sub.exclude_keywords,
                          sub.exclude_batch)


# ---- 去重 / v2 ----------------------------------------------------------------

def _dedup_verdict(db: Session, sub: Subscription, parsed: ParsedTitle,
                   is_preview: bool) -> tuple[bool, str]:
    """同订阅同集去重:无既有→接受;新版本更高→接受(v2);其余→跳过。

    先行与正式是两条独立的流:只在同阶段内去重(先行 ep4 不顶替正式 ep4,反之亦然),
    这样官方开播后正式集能照常下载,先行版仍保留。
    """
    if not parsed.episodes:
        return True, ""   # 集数未知(低置信度)不拦,下载后人工确认
    ep_set = set(parsed.episodes)
    rows = db.execute(
        select(Torrent).where(
            Torrent.subscription_id == sub.id,
            Torrent.is_preview.is_(is_preview),
            Torrent.status.notin_([TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED]))
    ).scalars().all()
    # 按集取并集:每集记录已有的最高版本。曾要求「单个既有种子」覆盖全部集号,
    # 逐集追完的番来一个 [01-12] 合集时没有任何单种子能覆盖 → 合集被判「新」整季重下
    # (完结时 lifecycle 自动放开 exclude_batch,该路径系统自己就会走到)。
    best_ver: dict = {}       # 集号 → 已有最高版本
    best_row: dict = {}       # 集号 → 该版本对应种子(报因用)
    for t in rows:
        for e in (t.parsed_json or {}).get("episodes") or []:
            if e in ep_set and t.version > best_ver.get(e, 0):
                best_ver[e] = t.version
                best_row[e] = t
    fresh = [e for e in ep_set
             if best_ver.get(e, 0) == 0 or parsed.version > best_ver[e]]
    if fresh:
        return True, ""       # 有未覆盖的集,或本条对某集是更高版本(v2)
    owners = {best_row[e].id for e in ep_set}
    return False, (f"重复(已有 #{'/#'.join(str(i) for i in sorted(owners))} "
                   f"合计覆盖集 {sorted(ep_set)},版本不低于本条 v{parsed.version})")


# ---- 剧集行 -------------------------------------------------------------------

# 发布日兜底判先行:比官方开播日早这么多天以上,才算先行(避开时区/开播当天的误差)
_PREVIEW_DATE_MARGIN_DAYS = 2


def _is_preview(sub: Subscription, parsed: ParsedTitle,
                published_at: datetime | None) -> bool:
    """判定该条目是否「先行」:标题带先行标记,或发布明显早于官方开播日(bgm.tv air_date)。"""
    if parsed.is_preview:
        return True
    air = getattr(sub.bangumi, "air_date", None)
    if not published_at or not air:
        return False
    try:
        air_d = date.fromisoformat(air[:10].replace("/", "-"))
    except ValueError:
        return False
    return (air_d - published_at.date()).days > _PREVIEW_DATE_MARGIN_DAYS


def _effective_episodes(sub: Subscription, numbers: list[float]) -> list[float]:
    """应用集数偏移(Mikan 跨季连续编号 → 本季集号)。"""
    off = sub.episode_offset or 0
    return [n - off for n in numbers if n - off > 0]


def _auto_detect_offset(db: Session, sub: Subscription, parsed: ParsedTitle) -> None:
    """集数 > 总集数时自动推断偏移(如二季 25-48 / 全 24 集 → 偏移 24)。可手动改写。"""
    if sub.episode_offset or not parsed.episodes:
        return
    total = sub.bangumi.eps_total
    if not total or total <= 0:
        return
    n = min(parsed.episodes)
    if n > total:
        sub.episode_offset = int((n - 1) // total) * total
        db.flush()
        log.info("订阅 %s 自动检测集数偏移 = %s(集号 %s > 总集数 %s)",
                 sub.id, sub.episode_offset, n, total)


def _ensure_episodes(db: Session, bangumi_id: int, numbers: list[float]) -> list[Episode]:
    eps: list[Episode] = []
    for n in numbers:
        ep = db.execute(select(Episode).where(
            Episode.bangumi_id == bangumi_id, Episode.type == EpisodeType.REGULAR,
            Episode.number == n)).scalar_one_or_none()
        if ep is None:
            ep = Episode(bangumi_id=bangumi_id, number=n, type=EpisodeType.REGULAR)
            db.add(ep)
            db.flush()
        eps.append(ep)
    return eps


# ---- 主管线 -------------------------------------------------------------------

def process_item(db: Session, sub: Subscription, item: RssItem) -> Torrent | None:
    """单条 RSS 条目走完整管线。返回新建的 Torrent 行(guid 已存在返回 None)。"""
    if db.execute(select(Torrent.id).where(Torrent.guid == item.guid)).first():
        return None

    parsed = parse(item.title)
    is_preview = _is_preview(sub, parsed, item.published_at)
    torrent = Torrent(
        subscription_id=sub.id, guid=item.guid, title_raw=item.title,
        parsed_json=parsed.to_dict(), torrent_url=item.torrent_url,
        is_batch=parsed.is_batch, version=parsed.version, is_preview=is_preview,
        published_at=item.published_at, size=item.size,
    )

    # 手动勾选优先于规则:强制排除直接淘汰,强制包含跳过规则与去重
    if item.guid in (sub.blocked_guids or []):
        ok, reason = False, "手动排除"
    elif item.guid in (sub.pinned_guids or []):
        ok, reason = True, ""
    else:
        ok, reason = _passes_filters(sub, item.title, parsed)
        if ok:
            ok, reason = _dedup_verdict(db, sub, parsed, is_preview)
    if not ok:
        torrent.status = TorrentStatus.SKIPPED
        torrent.error_message = reason
        db.add(torrent)
        db.flush()
        return torrent

    torrent.status = TorrentStatus.PENDING
    db.add(torrent)
    db.flush()
    from app.services.events import emit
    try:
        _auto_detect_offset(db, sub, parsed)
        for ep in _ensure_episodes(db, sub.bangumi_id, _effective_episodes(sub, parsed.episodes)):
            db.add(TorrentEpisode(torrent_id=torrent.id, episode_id=ep.id))
        db.flush()
    except Exception as e:  # noqa: BLE001 — 建集失败别让种子永久卡 PENDING(无回收路径,重试可救)
        log.warning("建剧集失败 #%s: %s", torrent.id, e)
        torrent.status = TorrentStatus.SUBMIT_FAILED
        torrent.error_message = f"建剧集失败: {e}"
        db.flush()
        emit("on_fail", torrent)
        return torrent

    emit("on_new", torrent)
    _submit(db, sub, torrent)
    return torrent


def _submit(db: Session, sub: Subscription, torrent: Torrent) -> None:
    from app.services.events import emit
    last_err: Exception | None = None
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            data = mikan_client.download_torrent(torrent.torrent_url)
            ih = downloader.add_torrent(data, save_path=sub.save_path)
            torrent.info_hash = ih
            torrent.status = TorrentStatus.DOWNLOADING
            torrent.error_message = None
            db.flush()
            log.info("提交下载器成功 #%s %s", torrent.id, torrent.title_raw[:50])
            emit("on_start", torrent)
            return
        except Exception as e:  # noqa: BLE001 — 重试后落 SUBMIT_FAILED
            last_err = e
            log.warning("提交 qB 失败(第 %s 次)#%s: %s", attempt, torrent.id, e)
    torrent.status = TorrentStatus.SUBMIT_FAILED
    torrent.error_message = str(last_err)
    db.flush()
    emit("on_fail", torrent)


MANUAL_SKIP_REASON = "手动删除"
DEAD_SKIP_REASON = "坏种自动清理"   # 坏种被移除后标记,不再复活(否则会循环重下死种)


def reevaluate_skipped(db: Session, sub: Subscription) -> int:
    """过滤规则变更/坏种换源后,重新评估本订阅被淘汰的条目(手动删除、坏种的不复活)。"""
    rows = db.execute(select(Torrent).where(
        Torrent.subscription_id == sub.id,
        Torrent.status == TorrentStatus.SKIPPED,
        Torrent.error_message.notin_([MANUAL_SKIP_REASON, DEAD_SKIP_REASON]))).scalars().all()
    revived = 0
    for t in rows:
        parsed = ParsedTitle.from_dict(t.parsed_json or {})
        if t.guid in (sub.blocked_guids or []):
            continue
        if t.guid in (sub.pinned_guids or []):
            ok = True
        else:
            ok, _ = _passes_filters(sub, t.title_raw, parsed)
            if ok:
                ok, _ = _dedup_verdict(db, sub, parsed, bool(t.is_preview))
        if not ok:
            continue
        t.status = TorrentStatus.PENDING
        t.error_message = None
        for ep in _ensure_episodes(db, sub.bangumi_id, _effective_episodes(sub, parsed.episodes)):
            if not db.get(TorrentEpisode, (t.id, ep.id)):
                db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
        db.flush()
        _submit(db, sub, t)
        revived += 1
    return revived


def poll_subscription(db: Session, sub: Subscription, backfill: bool | None = None,
                      items: list[RssItem] | None = None) -> dict:
    """轮询一个订阅。backfill=False 时只看比 last_checked_at 新的条目(只追新)。
    items 可由调用方预先并发取回(poll_all)。"""
    if not sub.enabled:
        return {"subscription": sub.id, "note": "已停用,跳过"}
    do_backfill = sub.backfill if backfill is None else backfill
    if items is None:
        items = mikan_client.fetch_rss(sub.bangumi.mikan_bangumi_id, sub.mikan_subgroup_id)
    if not do_backfill and sub.last_checked_at is None:
        # 只追新的首轮:记录现状,全部跳过(只为后续轮询建立基线把 guid 入库会污染留痕,直接不入库)
        cutoff = max((i.published_at for i in items if i.published_at), default=None)
        sub.last_checked_at = cutoff or datetime.now(timezone.utc)
        db.flush()
        return {"subscription": sub.id, "seen": len(items), "accepted": 0, "skipped": 0,
                "note": "只追新基线已建立"}

    revived = reevaluate_skipped(db, sub)   # 规则可能已变,先复活符合条件的留痕条目
    accepted = skipped = 0
    for item in items:
        t = process_item(db, sub, item)
        if t is None:
            continue
        if t.status == TorrentStatus.SKIPPED:
            skipped += 1
        else:
            accepted += 1
    sub.last_checked_at = datetime.now(timezone.utc)
    db.flush()
    return {"subscription": sub.id, "seen": len(items), "accepted": accepted,
            "skipped": skipped, "revived": revived}


def safe_poll(db: Session, sub: Subscription, **kwargs) -> dict:
    """带 RSS 健康记录的轮询。"""
    try:
        result = poll_subscription(db, sub, **kwargs)
        sub.last_poll_ok = True
        sub.last_poll_error = None
        db.flush()
        return result
    except Exception as e:  # noqa: BLE001 — 单订阅失败不影响其他
        log.exception("轮询订阅 %s 失败", sub.id)
        sub.last_poll_ok = False
        sub.last_poll_error = str(e)[:500]
        db.flush()
        return {"subscription": sub.id, "error": str(e)}


def poll_all(db: Session) -> list[dict]:
    """并发拉取 RSS(网络阶段并行,数据库阶段串行 — SQLite 单写者)。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    subs = db.execute(select(Subscription).where(Subscription.enabled)).scalars().all()
    # 排除本地导入/智能下载等容器伪订阅(无单一 RSS 源);已购买原盘的番剧整体跳过(ADR-0004)
    subs = [s for s in subs if s.mikan_subgroup_id not in ("local", "auto")
            and not s.bangumi.bd_owned]

    fetched: dict[int, list | Exception] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(mikan_client.fetch_rss,
                            s.bangumi.mikan_bangumi_id, s.mikan_subgroup_id): s.id
                for s in subs}
        for f in as_completed(futs):
            sid = futs[f]
            try:
                fetched[sid] = f.result()
            except Exception as e:  # noqa: BLE001
                fetched[sid] = e

    results = []
    for sub in subs:
        r = fetched.get(sub.id)
        if isinstance(r, Exception):
            sub.last_poll_ok = False
            sub.last_poll_error = str(r)[:500]
            db.flush()
            results.append({"subscription": sub.id, "error": str(r)})
        else:
            results.append(safe_poll(db, sub, items=r))
    return results
