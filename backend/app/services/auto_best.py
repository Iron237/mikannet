"""智能下载:扫一部番剧的所有字幕组种子,按偏好(BD>Web、严格分辨率/简中)挑最佳源。

- 补全:库里没有的正片集 → 下最佳源。
- 升级:已有 Web 而出现合格 BD(可能在别的字幕组)→ 下 BD,后处理按画质把 is_active 切到 BD。

一次性手动扫(库页勾选/详情页按钮)与定期扫(auto_best 番剧)共用 scan_bangumi。
种子挂到番剧的「auto」容器订阅(enabled=False,不参与 RSS 轮询),复用下载/后处理管线。
"""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict

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


_SIZE_RE = re.compile(r"([\d.]+)\s*(TB|GB|MB|KB)", re.I)
_SIZE_UNIT = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def _size_bytes(s: str | None) -> int:
    m = _SIZE_RE.search(s or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _SIZE_UNIT[m.group(2).upper()])
    except (ValueError, KeyError):
        return 0


def _quality_score(title: str) -> int:
    """画质评分(越高越优):满足分辨率/字幕后,据编码/色深判优劣。同分再比体积(码率)。"""
    t = (title or "").lower()
    score = 0
    if re.search(r"10\s*-?\s*bit|ma10p|yuv420p10|hi10p?|x265.*10|10bit", t):
        score += 4                         # 10bit 色深更佳
    if re.search(r"hevc|x265|h\.?\s?265", t):
        score += 2                         # HEVC 同码率画质更好
    elif re.search(r"avc|x264|h\.?\s?264", t):
        score += 1
    if re.search(r"flac|tta|\bpcm\b", t):
        score += 1                         # 无损音轨(BD 常见)
    return score


_PUB_RE = re.compile(r"(\d{4})[/\-年.](\d{1,2})[/\-月.](\d{1,2})")


def _pub_ord(published: str | None) -> int:
    """发布时间 → 可排序整数(YYYYMMDD)。蜜柑不给做种数,用发布越新≈种子越活作可用性代理。"""
    m = _PUB_RE.search(published or "")
    if not m:
        return 0
    return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))


def _candidate(subgroup_id: str, subgroup_name: str | None, st) -> dict | None:
    """字幕组种子 → 候选(严格过滤:分辨率必须等于目标 + 含偏好字幕);不合格返回 None。"""
    if not st.torrent_url:
        return None
    p = parse(st.title)
    # SP/OVA/OP·ED/PV 等非正片单发不作为正片候选(否则会被当正片集下载,与真正片撞集号)
    if p.ep_type != "regular" and not p.is_batch:
        return None
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
        "quality": _quality_score(st.title), "size": _size_bytes(getattr(st, "size", None)),
        "pub": _pub_ord(getattr(st, "published", None)),
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
    # 官方开播前扫到的源必然是先行(上季度网络先行放送等)→ 归先行流,
    # 否则「已播」被虚高、下满还会误判完结(与 RSS/本地导入路径同一判据)
    from app.services.phase import before_official_air
    is_prev = p.is_preview or before_official_air(sub.bangumi.air_date)
    t = Torrent(subscription_id=sub.id, guid=c["guid"], title_raw=c["title"],
                parsed_json=p.to_dict(), torrent_url=c["torrent_url"],
                is_batch=c["is_batch"], version=c["version"], is_preview=is_prev,
                status=TorrentStatus.PENDING)
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
    # 排除已处理过的 guid:SKIPPED 也算(过滤/手动删/坏种 → 不该再被选中,否则贪心占了集却
    # 在 _submit_candidate 被跳过 → 漏下);仅 SUBMIT_FAILED 留待重试
    have = set(db.execute(
        select(Torrent.guid).join(Subscription).where(
            Subscription.bangumi_id == bangumi.id,
            Torrent.status != TorrentStatus.SUBMIT_FAILED)
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

    # 选种:优先「同一字幕组」整部覆盖,避免「逐集源 + 别家合集」的重复下载。
    # 每轮挑最优子组(片源 BD>Web → 画质 10bit/HEVC/无损 → 可用性发布越新 → 对仍缺集覆盖数),
    # 子组内按画质/合集优先挑最小非重叠种子。去重关卡:某种子重复「已认领/已在库」的集多于
    # 它新带来的集时视为浪费(典型:为补 1-2 集去下整包 01-11),跳过 → 缺口留待下次更贴合的源。
    covered: set[int] = set(cur) - needed       # 已在最佳源、无需重下的集(防跨次重复下载)
    remaining = set(needed)
    selected: list[dict] = []
    by_sg: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        by_sg[c["subgroup_id"]].append(c)

    def _sg_key(sg: str) -> tuple:
        cs = by_sg[sg]
        return (min(_source_rank(c["source"]) for c in cs),     # BD > Web > 未知
                -max(c["quality"] for c in cs),                  # 画质越高越先
                -max(c["pub"] for c in cs),                      # 发布越新种子越活
                -len({e for c in cs for e in c["episodes"]} & remaining))  # 同字幕组尽量多覆盖

    while remaining and by_sg:
        usable = [sg for sg in by_sg
                  if any(set(c["episodes"]) & remaining for c in by_sg[sg])]
        if not usable:
            break
        sg = min(usable, key=_sg_key)
        for c in sorted(by_sg[sg], key=lambda c: (_source_rank(c["source"]), -c["quality"],
                                                  -c["pub"], -c["version"], not c["is_batch"],
                                                  -c["size"])):
            eps = set(c["episodes"])
            new = remaining & eps
            if not new or len(eps & covered) > len(new):   # 无新增、或重叠多于新增 → 跳过
                continue
            selected.append(c)
            covered |= eps                                  # 整段范围认领,后续不再重复下
            remaining -= eps
        del by_sg[sg]

    sub = _auto_sub(db, bangumi)
    submitted = sum(1 for c in selected if _submit_candidate(db, sub, c))
    db.flush()
    gaps = sorted(remaining)   # 仍缺、但只有「会重复已有集的大合集」能补 → 按去重策略未下,显式留痕
    log.info("智能扫描 %s:候选 %s,补/升 %s 集,提交 %s 个种子%s",
             bangumi.title, len(cands), len(needed), submitted,
             f",留待 {gaps}(仅大合集源,避免重复未下)" if gaps else "")
    return {"bangumi": bangumi.id, "title": bangumi.title, "candidates": len(cands),
            "needed": sorted(needed), "submitted": submitted, "gaps": gaps,
            # 进度列表用:本次选中的种子(画质标签 + 集范围),展示「进入下载前」挑了什么
            "picked": [{"title": c["title"][:80], "source": c["source"],
                        "quality": c["quality"], "episodes": sorted(c["episodes"])[:3],
                        "ep_count": len(c["episodes"])} for c in selected]}


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
