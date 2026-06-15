"""智能下载:扫一部番剧的所有字幕组种子,按偏好(BD>Web、严格分辨率/简中)挑最佳源。

- 补全:库里没有的正片集 → 下最佳源。
- 升级:已有 Web 而出现合格 BD(可能在别的字幕组)→ 下 BD,后处理按画质把 is_active 切到 BD。

一次性手动扫(库页勾选/详情页按钮)与定期扫(auto_best 番剧)共用 scan_bangumi。
种子挂到番剧的「auto」容器订阅(enabled=False,不参与 RSS 轮询),复用下载/后处理管线。
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.mikan import mikan_client
from app.config import settings
from app.database import db_session
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.parsers.title_parser import detect_subtitle_tags, parse

log = logging.getLogger(__name__)

AUTO_SUBGROUP_ID = "auto"
_SOURCE_RANK = {"BD": 0, "Web": 1}

# 批量/定期扫描进度(前端轮询)
state: dict = {"running": False, "phase": "", "done": 0, "total": 0, "current": "",
               "result": [], "error": None}


def _source_rank(source: str | None) -> int:
    return _SOURCE_RANK.get(source, 2)


def _has_pref_sub(title: str) -> bool:
    """严格字幕:标题须含偏好语言。简中=含简体(简体/简日/简繁/简繁日);繁中=含繁体。"""
    want = (settings.auto_dl_sub_lang or "简中").strip()
    tags = detect_subtitle_tags(title)
    if "简" in want:
        return any("简" in t for t in tags)
    if "繁" in want:
        return any("繁" in t for t in tags)
    return True


def _candidate(subgroup_id: str, subgroup_name: str | None, st) -> dict | None:
    """字幕组种子 → 候选(严格过滤:分辨率必须等于目标 + 含偏好字幕);不合格返回 None。"""
    if not st.torrent_url:
        return None
    p = parse(st.title)
    if not p.episodes and not p.is_batch:
        return None
    if p.resolution != (settings.auto_dl_resolution or "1080p"):
        return None
    if not _has_pref_sub(st.title):
        return None
    # 番剧页给的是相对链接(/Download/...torrent)→ 存成绝对 URL
    url = st.torrent_url
    if not url.startswith("http"):
        url = mikan_client.base + "/" + url.lstrip("/")
    return {
        "subgroup_id": subgroup_id, "subgroup_name": subgroup_name,
        "guid": st.episode_url, "torrent_url": url, "title": st.title,
        "episodes": [int(e) for e in p.episodes if float(e).is_integer()],
        "is_batch": p.is_batch, "source": p.source, "version": p.version,
    }


def _current_ranks(db: Session, bangumi_id: int) -> dict[int, int]:
    """各正片集当前 is_active 文件的最佳片源等级(用于判断是否值得升级)。"""
    rows = db.execute(
        select(Episode.number, VideoFile.source)
        .join(VideoFile, VideoFile.episode_id == Episode.id)
        .join(Torrent, VideoFile.torrent_id == Torrent.id)
        .where(Episode.bangumi_id == bangumi_id, Episode.type == EpisodeType.REGULAR,
               VideoFile.is_active.is_(True))).all()
    out: dict[int, int] = {}
    for number, source in rows:
        if number is None:
            continue
        ep = int(number)
        out[ep] = min(out.get(ep, 99), _source_rank(source))
    return out


def _auto_sub(db: Session, bangumi: Bangumi) -> Subscription:
    from app.api.subscriptions import _safe_dirname
    sub = db.execute(select(Subscription).where(
        Subscription.bangumi_id == bangumi.id,
        Subscription.mikan_subgroup_id == AUTO_SUBGROUP_ID)).scalar_one_or_none()
    if sub is None:
        sub = Subscription(
            bangumi_id=bangumi.id, mikan_subgroup_id=AUTO_SUBGROUP_ID,
            subgroup_name="智能下载", enabled=False, backfill=False, exclude_batch=False,
            save_path=f"{settings.download_root}/{_safe_dirname(bangumi.title)}")
        db.add(sub)
        db.flush()
    return sub


def _submit_candidate(db: Session, sub: Subscription, c: dict) -> bool:
    """建 Torrent 行 + 关联正片集 + 提交下载器。

    guid 已存在:之前提交失败(SUBMIT_FAILED)的修正 URL 后重试,其余跳过。
    """
    from app.services.events import emit
    from app.services.rss_engine import _submit
    existing = db.execute(select(Torrent).where(
        Torrent.guid == c["guid"])).scalar_one_or_none()
    if existing is not None:
        if existing.status != TorrentStatus.SUBMIT_FAILED:
            return False
        existing.torrent_url = c["torrent_url"]   # 修正旧的相对/坏 URL
        existing.status = TorrentStatus.PENDING
        existing.error_message = None
        db.flush()
        _submit(db, sub, existing)
        return True
    p = parse(c["title"])
    t = Torrent(subscription_id=sub.id, guid=c["guid"], title_raw=c["title"],
                parsed_json=p.to_dict(), torrent_url=c["torrent_url"],
                is_batch=c["is_batch"], version=c["version"], status=TorrentStatus.PENDING)
    db.add(t)
    db.flush()
    for n in c["episodes"]:
        ep = db.execute(select(Episode).where(
            Episode.bangumi_id == sub.bangumi_id, Episode.type == EpisodeType.REGULAR,
            Episode.number == float(n))).scalar_one_or_none()
        if ep is None:
            ep = Episode(bangumi_id=sub.bangumi_id, number=float(n), type=EpisodeType.REGULAR)
            db.add(ep)
            db.flush()
        if not db.get(TorrentEpisode, (t.id, ep.id)):
            db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
    db.flush()
    emit("on_new", t)
    _submit(db, sub, t)
    return True


def scan_bangumi(db: Session, bangumi: Bangumi, do_fill: bool = True,
                 do_upgrade: bool = True) -> dict:
    """扫一部番剧:挑最佳源补缺集 + 升级现有源。返回结果摘要。"""
    if not bangumi.mikan_bangumi_id:
        return {"bangumi": bangumi.id, "title": bangumi.title, "note": "无蜜柑 ID,跳过"}
    if bangumi.bd_owned:
        return {"bangumi": bangumi.id, "title": bangumi.title, "note": "已购买(有原盘),跳过"}
    do_upgrade = do_upgrade and settings.auto_dl_prefer_bd

    detail = mikan_client.get_bangumi(bangumi.mikan_bangumi_id)
    cands: list[dict] = []
    for sg in detail.subgroups:
        for st in sg.torrents:
            c = _candidate(sg.subgroup_id, sg.name, st)
            if c and c["episodes"]:
                cands.append(c)
    if not cands:
        return {"bangumi": bangumi.id, "title": bangumi.title, "candidates": 0,
                "submitted": 0, "note": "无满足偏好的源(分辨率/字幕严格)"}

    # 已下过的 guid 不重复下
    have = set(db.execute(
        select(Torrent.guid).join(Subscription).where(
            Subscription.bangumi_id == bangumi.id,
            Torrent.status.notin_([TorrentStatus.SKIPPED, TorrentStatus.SUBMIT_FAILED]))
        ).scalars().all())
    cands = [c for c in cands if c["guid"] not in have]
    if not cands:
        return {"bangumi": bangumi.id, "title": bangumi.title, "candidates": 0,
                "submitted": 0, "note": "合格源都已下载"}

    cur = _current_ranks(db, bangumi.id)
    universe = sorted({e for c in cands for e in c["episodes"]})
    needed: set[int] = set()
    for ep in universe:
        best = min((_source_rank(c["source"]) for c in cands if ep in c["episodes"]),
                   default=None)
        if best is None:
            continue
        have_rank = cur.get(ep)
        if have_rank is None:
            if do_fill:
                needed.add(ep)            # 缺集:补全(取最佳可用源)
        elif do_upgrade and best == 0 and have_rank > 0:
            needed.add(ep)                # 仅「→BD」升级:已有 Web/未知 且出现合格 BD 才换
            # (不靠 Web>未知 这类弱信号升级,避免把未标 BD 标记的蓝光误降级成 Web)
    if not needed:
        return {"bangumi": bangumi.id, "title": bangumi.title, "candidates": len(cands),
                "submitted": 0, "note": "已是最佳,无需下载"}

    # 贪心选种:优先 BD、合集(覆盖广)、高版本;逐个吃掉待办集
    remaining = set(needed)
    selected: list[dict] = []
    for c in sorted(cands, key=lambda c: (_source_rank(c["source"]),
                                          not c["is_batch"], -c["version"])):
        cover = remaining & set(c["episodes"])
        if cover:
            selected.append(c)
            remaining -= cover
        if not remaining:
            break

    sub = _auto_sub(db, bangumi)
    submitted = sum(1 for c in selected if _submit_candidate(db, sub, c))
    db.flush()
    log.info("智能扫描 %s:候选 %s,补/升 %s 集,提交 %s 个种子",
             bangumi.title, len(cands), len(needed), submitted)
    return {"bangumi": bangumi.id, "title": bangumi.title, "candidates": len(cands),
            "needed": sorted(needed), "submitted": submitted}


def run_scan(bangumi_ids: list[int], do_fill: bool = True, do_upgrade: bool = True) -> None:
    state.update(running=True, phase="智能扫描", done=0, total=len(bangumi_ids),
                 current="", result=[], error=None)
    try:
        for bid in bangumi_ids:
            with db_session() as db:
                b = db.get(Bangumi, bid)
                if not b:
                    state["done"] += 1
                    continue
                state["current"] = b.title
                try:
                    r = scan_bangumi(db, b, do_fill, do_upgrade)
                except Exception as e:  # noqa: BLE001 — 单部失败不拖垮整批
                    log.exception("智能扫描 #%s 失败", bid)
                    r = {"bangumi": bid, "error": str(e)}
            state["result"].append(r)
            state["done"] += 1
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
    finally:
        state.update(running=False, phase="完成", current="")


def start_scan(bangumi_ids: list[int], do_fill: bool = True, do_upgrade: bool = True) -> bool:
    if state["running"]:
        return False
    threading.Thread(target=run_scan, args=(bangumi_ids, do_fill, do_upgrade),
                     daemon=True).start()
    return True


def scan_auto_all() -> None:
    """定期任务:扫所有开启智能下载的番剧(补全 + 升级)。手动扫描进行中则跳过。"""
    if state["running"]:
        return
    with db_session() as db:
        ids = db.execute(select(Bangumi.id).where(
            Bangumi.auto_best.is_(True),
            Bangumi.bd_owned.is_(False),          # 已购买原盘 → 不自动下载
            Bangumi.mikan_bangumi_id.isnot(None))).scalars().all()
    if ids:
        log.info("定期智能扫描:%s 部番剧", len(ids))
        run_scan(list(ids), do_fill=True, do_upgrade=True)
