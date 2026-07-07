"""番剧生命周期自动化(grill 第七轮决策):

- 完结自动检测 → 转补全(airing→finished:开 auto_best、放开合集、推送)。【Q2】
- 全 BD 完成 → 收尾(清 auto_best、停真订阅、删被 BD 顶替的 web 种子任务+文件、推送)。【Q1】
  「删 web」是对 ADR-0001「文件不动」的刻意例外(BDrip 覆盖 web):见 ADR-0001 修订。

触发:postprocess / 库扫描完成后即时调用 on_torrent_processed + scheduler 每日 daily_reconcile 兜底。
完成判据全部钉死在 eps_total 可靠;eps_total 未知 → 不触发(宁可不自动也不误判)。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.config import settings
from app.database import db_session
from app.models import (AiringStatus, Bangumi, Episode, EpisodeType, Subscription,
                        Torrent, TorrentEpisode, VideoFile)
from app.services.auto_best import AUTO_SUBGROUP_ID
from app.services.local_import import LOCAL_SUBGROUP_ID

log = logging.getLogger(__name__)


def _active_bd_regular_eps(db: Session, bangumi_id: int) -> set[int]:
    """该番剧「有 is_active BD 文件」的正片集号集合。"""
    rows = db.execute(
        select(Episode.number)
        .join(VideoFile, VideoFile.episode_id == Episode.id)
        .where(Episode.bangumi_id == bangumi_id, Episode.type == EpisodeType.REGULAR,
               VideoFile.is_active.is_(True), VideoFile.source == "BD")).scalars().all()
    return {int(n) for n in rows if n is not None}


def bd_complete(db: Session, b: Bangumi) -> bool:
    """全 BD 完成判据:eps_total 已知 且 正片每一集(bangumi 编号 ep_start..ep_start+N-1)
    都有 is_active 的 BD 文件。"""
    if not b.eps_total:
        return False
    have = _active_bd_regular_eps(db, b.id)
    start = b.ep_start or 1
    return all(n in have for n in range(start, start + b.eps_total))


def _real_subs(db: Session, bangumi_id: int) -> list[Subscription]:
    """真字幕组 RSS 订阅(排除 auto 智能容器 / local 本地导入容器)。"""
    return [s for s in db.execute(select(Subscription).where(
        Subscription.bangumi_id == bangumi_id)).scalars()
        if s.mikan_subgroup_id not in (AUTO_SUBGROUP_ID, LOCAL_SUBGROUP_ID)]


def finalize_complete(db: Session, b: Bangumi) -> bool:
    """全 BD 完成 → 收尾(幂等):清 auto_best、停真订阅、删被 BD 顶替的 web 种子(任务+文件)。

    删 web 判据:真下载的(有 info_hash)、其文件全非 active(已被 BD 顶替)、且不含 BD 源
    (别误删 BD 自身)。合集种子整包都被顶替才会全非 active → 安全(避免破坏种子完整性)。
    返回是否执行了收尾动作(用于幂等:无动作即已收尾过)。
    """
    if not bd_complete(db, b):
        return False
    changed = False
    if b.auto_best:
        b.auto_best = False
        changed = True
    for s in _real_subs(db, b.id):
        if s.enabled:
            s.enabled = False
            changed = True
    torrents = db.execute(select(Torrent).join(Subscription).where(
        Subscription.bangumi_id == b.id, Torrent.info_hash.isnot(None))).scalars().all()
    for t in torrents:
        vfs = list(t.files)
        if not vfs or any(f.is_active for f in vfs) or any(f.source == "BD" for f in vfs):
            continue
        try:
            downloader.delete(t.info_hash, delete_files=True)
        except Exception as e:  # noqa: BLE001 — 下载器里可能已不存在
            log.warning("收尾删 web 种子 %s 失败: %s", t.info_hash, e)
        for f in vfs:
            db.delete(f)
        for te in db.execute(select(TorrentEpisode).where(
                TorrentEpisode.torrent_id == t.id)).scalars():
            db.delete(te)
        db.flush()
        db.delete(t)
        changed = True
    if changed:
        db.flush()
        log.info("BD 补全完成收尾:%s → 停扫/停订阅/删冗余 web", b.title)
        _notify(b, "BD 补全完成", f"{b.title} 正片已全部替换为 BD,停止补全并清理 web 版本。")
    return changed


def evaluate_airing(db: Session, b: Bangumi) -> bool:
    """检测 airing→finished(日期推算完结 或 RSS 已下满 eps_total 正片集)→ 转补全。返回是否转换。

    仅对有蜜柑 ID 的番剧生效(无蜜柑 ID 无法智能扫描,转补全无意义)。
    """
    if b.airing_status != AiringStatus.AIRING or not b.eps_total or not b.mikan_bangumi_id:
        return False
    from app.services.phase import before_official_air
    if before_official_air(b.air_date):
        return False   # 官方还没开播(先行放送期):就算先行集齐 12 集也绝不判完结
    from app.services.metadata_service import _infer_airing_status
    by_date = _infer_airing_status(b.air_date, b.eps_total) == AiringStatus.FINISHED
    # 只数正式流的集:先行(上季度网络先行放送)下满不代表官方播完
    done = db.execute(
        select(Episode.id).join(VideoFile, VideoFile.episode_id == Episode.id)
        .join(Torrent, VideoFile.torrent_id == Torrent.id)
        .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
               VideoFile.is_active.is_(True), Torrent.is_preview.is_(False))
        .distinct()).scalars().all()
    by_eps = len(done) >= b.eps_total
    if not (by_date or by_eps):
        return False
    b.airing_status = AiringStatus.FINISHED
    b.auto_best = True
    for s in _real_subs(db, b.id):
        s.exclude_batch = False    # 完结后合集为补番首选
    db.flush()
    log.info("完结自动转补全:%s(%s)", b.title, "下满 eps_total" if by_eps else "日期推算完结")
    _notify(b, "已完结,开始补 BD", f"{b.title} 检测到完结,已开启智能补全(BD 优先)。")
    return True


def _notify(b: Bangumi, subtitle: str, message: str) -> None:
    try:
        from app.services.events import notify
        poster = None
        if b.poster_path and (settings.data_dir / b.poster_path).exists():
            poster = str(settings.data_dir / b.poster_path)
        notify("on_complete", f"{b.title} · {subtitle}", message, poster)
    except Exception:  # noqa: BLE001 — 推送失败不影响主流程
        log.debug("生命周期推送失败", exc_info=True)


def on_torrent_processed(db: Session, bangumi_id: int | None) -> None:
    """postprocess / 库扫描完成后调用:先看是否完结转补全,再看是否全 BD 完成收尾。"""
    if not bangumi_id:
        return
    b = db.get(Bangumi, bangumi_id)
    if not b:
        return
    try:
        evaluate_airing(db, b)
        finalize_complete(db, b)
    except Exception:  # noqa: BLE001 — 生命周期处理失败不阻塞主流程
        log.exception("生命周期处理失败 bangumi=%s", bangumi_id)


def daily_reconcile() -> None:
    """每日兜底:扫所有已知 eps_total 的番剧 → 完结转补全 + 全 BD 完成收尾。"""
    with db_session() as db:
        ids = db.execute(select(Bangumi.id).where(
            Bangumi.eps_total.isnot(None))).scalars().all()
    done = 0
    for bid in ids:
        try:
            with db_session() as db:
                b = db.get(Bangumi, bid)
                if not b:
                    continue
                if evaluate_airing(db, b) or finalize_complete(db, b):
                    done += 1
        except Exception:  # noqa: BLE001 — 单部失败不拖垮整批
            log.exception("每日生命周期复算失败 bangumi=%s", bid)
    if done:
        log.info("每日生命周期复算:%s 部发生转补全/收尾", done)
