"""bgm.tv 深度联动:每集精确数据同步 + 收视进度回写 + 「在看」导入。

- 每集同步(无鉴权):/v0/episodes 的 airdate(精确放送日,休播/延期不靠周更外推)
  + name_cn(中文标题)+ 章节 id(进度回写映射),写进本地 Episode 表。
- 进度回写(需个人令牌):下载入库一集 → 条目标「在看」+ 该集标「看过」。
- 在看导入(需令牌):bgm「在看」列表 → 建库内番剧(自动配元数据 + 尝试匹配蜜柑 ID)。
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.bgmtv import bgmtv_client
from app.config import settings
from app.database import db_session
from app.models import Bangumi, Episode, EpisodeType

log = logging.getLogger(__name__)


# ---- 每集精确数据同步 -----------------------------------------------------------

def _shift_legacy_numbering(db: Session, b: Bangumi, start: int) -> None:
    """ep_start 功能上线前建的正片行按季内 1-based 编号:整体平移到 bangumi 编号。

    不平移的话:详情页 1..K 与 start.. 两套编号并存;下面的 upsert 按 bangumi 编号
    匹配不到旧行 → 同一集分裂两行。仅动 number < start 的行(已是 bangumi 编号的不动),
    目标撞号(历史混编)则跳过该行。实际平移过才矫正订阅偏移:正偏移(把连续编号拉回
    1-based 的)同步减 delta;0 偏移的订阅(编号本就随 bangumi,或按季内计数走负偏移
    自动检测)不动。幂等:平移后没有 < start 的行,再跑是空操作。"""
    delta = start - 1
    eps = db.execute(select(Episode).where(
        Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
        Episode.number.is_not(None))).scalars().all()
    by_number = {e.number: e for e in eps}
    shifted, merged = 0, []
    for e in sorted(eps, key=lambda x: -(x.number or 0)):   # 从大到小,防平移路径互撞
        if e.number >= start or by_number.get(e.number) is not e:
            continue
        tgt = e.number + delta
        target = by_number.get(tgt)
        if target is None:
            by_number.pop(e.number, None)
            e.number = tgt
            by_number[tgt] = e
            shifted += 1
        else:
            _merge_episode(db, src=e, target=target)   # 历史混编:同一集两套编号 → 并行合一
            by_number.pop(e.number, None)
            merged.append(target.id)
    if shifted or merged:
        for s in b.subscriptions:
            if (s.episode_offset or 0) > 0:
                s.episode_offset = max(0, s.episode_offset - delta)
        db.flush()
        if merged:   # 合并后同集多文件 → 重新按画质选 active(其余置灰)
            from app.services.postprocess import _apply_version_switch
            for ep_id in merged:
                _apply_version_switch(db, ep_id)
        log.info("剧集编号平移:%s 平移 %s 行、合并 %s 行 +%s(对齐 bangumi 编号 %s 起)",
                 b.title, shifted, len(merged), delta, start)


def _merge_episode(db: Session, src: Episode, target: Episode) -> None:
    """把旧编号行(src)的文件/种子关联并入 bangumi 编号行(target),删除旧行。

    历史混编场景:RSS 路径按季内 1-based 建了第 2 话,auto/bgm 路径按 bangumi 编号
    建了第 30 话——物理上是同一集。合并后目标行同集多文件,由版本切换按画质只留一个 active。"""
    from app.models import TorrentEpisode, VideoFile
    for vf in db.execute(select(VideoFile).where(
            VideoFile.episode_id == src.id)).scalars():
        vf.episode_id = target.id
    for te in db.execute(select(TorrentEpisode).where(
            TorrentEpisode.episode_id == src.id)).scalars():
        if db.get(TorrentEpisode, (te.torrent_id, target.id)):
            db.delete(te)                 # 目标行已有该种子的关联 → 丢弃重复链
        else:
            db.delete(te)
            db.flush()
            db.add(TorrentEpisode(torrent_id=te.torrent_id, episode_id=target.id))
    if src.title and not target.title:
        target.title = src.title
    if src.air_date and not target.air_date:
        target.air_date = src.air_date
    if src.anidb_eid and not target.anidb_eid:
        target.anidb_eid = src.anidb_eid
    db.delete(src)
    db.flush()


def sync_bgmtv_episodes(db: Session, b: Bangumi) -> list[dict]:
    """把 bgm.tv 本篇章节(sort=bangumi 编号)同步进 Episode 表。

    upsert 键:bgmtv_ep_id 优先,退 (REGULAR, number==sort)。
    - 标题:name_cn 优先(空则 name),覆盖旧值(AniDB 的英文题让位给官方中文)
    - air_date:每集精确放送日;**未来集**的日期变动收集返回(延期/提档检测)
    - 顺带回填 ep_start(本篇最小 sort)
    返回未来集日期变动 [{number, old, new}](首次填充不算)。
    """
    from datetime import date
    eps = bgmtv_client.episodes(b.bgmtv_subject_id, ep_type=0)
    if not eps:
        return []
    start = min(int(e.sort) for e in eps if e.sort >= 1) if any(e.sort >= 1 for e in eps) else 1
    if start > 1:
        _shift_legacy_numbering(db, b, start)   # 旧 1-based 行先平移,upsert 才对得上
        if (b.ep_start or 1) == 1:
            b.ep_start = start
    changed: list[dict] = []
    today = date.today().isoformat()
    for be in eps:
        ep = db.execute(select(Episode).where(
            Episode.bgmtv_ep_id == be.ep_id)).scalar_one_or_none()
        if ep is None:
            ep = db.execute(select(Episode).where(
                Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
                Episode.number == float(be.sort))).scalars().first()
        if ep is None:
            ep = Episode(bangumi_id=b.id, type=EpisodeType.REGULAR, number=float(be.sort))
            db.add(ep)
        ep.bgmtv_ep_id = be.ep_id
        title = be.name_cn or be.name
        if title:
            ep.title = title
        if be.airdate and be.airdate != ep.air_date:
            if ep.air_date and be.airdate >= today:   # 未来集的日期变动才算延期/提档
                changed.append({"number": ep.number, "old": ep.air_date, "new": be.airdate})
            ep.air_date = be.airdate
    db.flush()
    return changed


# ---- 系列链构建(详情页系列导航条)----------------------------------------------

# 进系列链的关系词:主线(前传/续集)+ 番外双向(番外篇/主线故事)+ 衍生(剧场版多挂此词)。
# 角色出演/总集篇/联动/相同世界观/不同演绎等一律不进(噪声)。
_SERIES_RELS = {"前传", "续集", "番外篇", "主线故事", "衍生"}
_SERIES_MAX_NODES = 12
_SERIES_MAX_DEPTH = 3


def build_series(subject_id: int) -> list[dict]:
    """从一个条目出发,沿系列关系 BFS 出完整系列,按放送日期排序。

    返回 [{subject_id, title, date}](含起点自身)。每部一次 related + 一次 subject
    (拿日期/标题),首次构建数秒,调用方缓存。单部失败跳过不拖垮整条链。
    """
    from time import sleep
    seen = {subject_id}
    depth = {subject_id: 0}
    queue = [subject_id]
    while queue and len(seen) < _SERIES_MAX_NODES:
        sid = queue.pop(0)
        if depth[sid] >= _SERIES_MAX_DEPTH:
            continue
        try:
            rels = bgmtv_client.related_subjects(sid)
        except Exception as e:  # noqa: BLE001
            log.warning("系列链拉关联失败 subject=%s: %s", sid, e)
            continue
        for r in rels:
            if r.type != 2 or r.relation not in _SERIES_RELS or r.subject_id in seen:
                continue
            seen.add(r.subject_id)
            depth[r.subject_id] = depth[sid] + 1
            queue.append(r.subject_id)
        sleep(0.2)
    out: list[dict] = []
    for sid in seen:
        try:
            s = bgmtv_client.get_subject(sid)
            out.append({"subject_id": sid, "title": s.name_cn or s.name, "date": s.date})
        except Exception as e:  # noqa: BLE001
            log.warning("系列链取条目失败 subject=%s: %s", sid, e)
        sleep(0.15)
    out.sort(key=lambda x: (x["date"] is None, x["date"] or "", x["subject_id"]))
    return out


def series_labels(titles: list[str]) -> list[str]:
    """片名短标签:去掉系列公共前缀(「相反的你和我 第二季」→「第二季」)。

    前缀太短(<3 字)不去;去完为空(第一季常与前缀同名)回退全名。"""
    import os.path
    if len(titles) < 2:
        return list(titles)
    prefix = os.path.commonprefix(titles)
    if len(prefix) < 3:
        return list(titles)
    return [(t[len(prefix):].strip(" ·-—:~〜") or t) for t in titles]


# ---- 收视进度回写(fire-and-forget,失败只记日志)------------------------------

_watching_marked: set[int] = set()   # 本进程内已标过「在看」的 subject(避免每集重复 POST)


def report_progress(bangumi_id: int, episode_ids: list[int]) -> None:
    """下载入库后调用:后台线程把对应 bgm.tv 章节标「看过」(条目先标「在看」)。

    开关:settings.bgmtv_sync_progress + 已配令牌。任何失败只记日志,不影响主流程。
    """
    if not settings.bgmtv_sync_progress or not (settings.bgmtv_access_token or "").strip():
        return

    def _run() -> None:
        try:
            with db_session() as db:
                b = db.get(Bangumi, bangumi_id)
                if b is None or not b.bgmtv_subject_id:
                    return
                sid = b.bgmtv_subject_id
                eps = db.execute(select(Episode).where(
                    Episode.id.in_(episode_ids),
                    Episode.bgmtv_ep_id.is_not(None))).scalars().all()
                targets = [(e.bgmtv_ep_id, e.number) for e in eps]
            if not targets:
                return
            if sid not in _watching_marked:
                bgmtv_client.mark_subject_watching(sid)
                _watching_marked.add(sid)
            for ep_id, num in targets:
                bgmtv_client.mark_episode_watched(ep_id)
                log.info("bgm.tv 进度回写:subject %s 第 %s 话 → 看过", sid, num)
        except Exception as e:  # noqa: BLE001 — 联动失败不能影响下载链路
            log.warning("bgm.tv 进度回写失败(bangumi %s): %s", bangumi_id, e)

    threading.Thread(target=_run, daemon=True, name="bgm-progress").start()


# ---- 「在看」导入 ---------------------------------------------------------------

def import_watching() -> dict:
    """把 bgm.tv「在看」列表导入为库内番剧(已存在的跳过)。

    每部:建 Bangumi(绑 subject)→ enrich 拉全元数据(含 ep_start/每集数据)→
    尝试按标题匹配蜜柑 ID(命中才能加订阅/智能下载,失败留空可手动绑)。
    返回 {"imported":[{title, mikan_matched}], "existed": n, "failed": n}。
    """
    me = bgmtv_client.me()
    if not me:
        raise RuntimeError("bgm.tv 令牌未配置或已失效(设置页「bgm.tv 联动」填 Access Token)")
    items = bgmtv_client.watching(me["username"])
    imported, existed, failed = [], 0, 0
    for it in items:
        subj = it.get("subject") or {}
        sid = subj.get("id")
        if not sid:
            continue
        title = subj.get("name_cn") or subj.get("name") or f"bangumi {sid}"
        try:
            with db_session() as db:
                if db.execute(select(Bangumi.id).where(
                        Bangumi.bgmtv_subject_id == sid)).first():
                    existed += 1
                    continue
                b = Bangumi(title=title, bgmtv_subject_id=sid)
                db.add(b)
                db.flush()
                from app.services.metadata_service import enrich_bangumi
                from app.services.organize import detect_season
                enrich_bangumi(db, b, bgmtv_subject_id=sid)
                b.season_number = detect_season(b.title)
                mikan_matched = False
                try:   # 蜜柑只索引罗马音 → 用原名搜;命中才能订阅/智能下载
                    from app.clients.mikan import mikan_client
                    hit = mikan_client.search_best(b.title_original or b.title)
                    if hit and not db.execute(select(Bangumi.id).where(
                            Bangumi.mikan_bangumi_id == hit.mikan_bangumi_id)).first():
                        b.mikan_bangumi_id = hit.mikan_bangumi_id
                        mikan_matched = True
                except Exception:  # noqa: BLE001 — 匹配失败留空,可手动绑
                    pass
                imported.append({"id": b.id, "title": b.title, "mikan_matched": mikan_matched})
        except Exception as e:  # noqa: BLE001 — 单部失败不阻断整批
            failed += 1
            log.warning("导入在看失败 %s: %s", title, e)
    log.info("bgm.tv 在看导入:新增 %s,已存在 %s,失败 %s", len(imported), existed, failed)
    return {"imported": imported, "existed": existed, "failed": failed}
