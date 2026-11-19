"""番剧生命周期自动化(grill 第七轮决策):

- 完结自动检测 → 转补全(airing→finished:开 auto_best、放开合集、推送)。【Q2】
- 全 BD 完成 → 收尾(清 auto_best、停真订阅、删被 BD 顶替的 web 种子任务+文件、推送)。【Q1】
  「删 web」是对 ADR-0001「文件不动」的刻意例外(BDrip 覆盖 web):见 ADR-0001 修订。

触发:postprocess / 库扫描完成后即时调用 on_torrent_processed + scheduler 每日 daily_reconcile 兜底。
完成判据全部钉死在 eps_total 可靠;eps_total 未知 → 不触发(宁可不自动也不误判)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.config import settings
from app.database import db_session
from app.models import (AiringStatus, Bangumi, Episode, EpisodeType, Kind, Subscription,
                        Torrent, TorrentStatus, VideoFile)
from app.services import storage
from app.services.auto_best import AUTO_SUBGROUP_ID
from app.services.download_paths import media_file_exists
from app.services.local_import import LOCAL_SUBGROUP_ID

log = logging.getLogger(__name__)


def _complete_bd_files(db: Session, b: Bangumi) -> dict[int, set[Path]]:
    """整季正式正片有可用 BD 时返回剧集 ID → 实盘路径，用于判定旧种是否可整包清理。"""
    if b.kind != Kind.TV or not b.eps_total or b.eps_total < 1:
        return {}
    if settings.storage_mode in ("smb", "nfs") and not storage.is_mounted():
        return {}
    start = b.ep_start or 1
    end = start + b.eps_total - 1
    rows = db.execute(
        select(Episode.id, Episode.number, VideoFile.relative_path)
        .join(VideoFile, VideoFile.episode_id == Episode.id)
        .join(Torrent, Torrent.id == VideoFile.torrent_id)
        .where(Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
               Episode.number.between(start, end), Torrent.is_preview.is_(False),
               VideoFile.is_active.is_(True), VideoFile.source == "BD"))
    files: dict[int, set[Path]] = {}
    root = settings.download_root_local.resolve()
    for episode_id, number, path in rows:
        if float(number).is_integer() and media_file_exists(path):
            resolved = (root / path).resolve()
            if resolved.is_relative_to(root):
                files.setdefault(episode_id, set()).add(Path(path))
    # (番剧、类型、集号)有唯一约束。范围内的 N 个不同整数即覆盖整季。
    return files if len(files) == b.eps_total else {}


def bd_complete(db: Session, b: Bangumi) -> bool:
    """全 BD 完成判据:eps_total 已知 且 正片每一集(bangumi 编号 ep_start..ep_start+N-1)
    都有 is_active 的 BD 文件。"""
    return bool(_complete_bd_files(db, b))


def _real_subs(db: Session, bangumi_id: int) -> list[Subscription]:
    """真字幕组 RSS 订阅(排除 auto 智能容器 / local 本地导入容器)。"""
    return [s for s in db.execute(select(Subscription).where(
        Subscription.bangumi_id == bangumi_id)).scalars()
        if s.mikan_subgroup_id not in (AUTO_SUBGROUP_ID, LOCAL_SUBGROUP_ID)]


def _shared_file_paths(db: Session, root: Path) -> set[str]:
    """旧库允许多个种子引用同一路径；解析别名后保护所有共用文件。"""
    owner: dict[str, int] = {}
    shared: set[str] = set()
    for torrent_id, path in db.execute(select(VideoFile.torrent_id, VideoFile.relative_path)):
        # Linux 容器的 SMB 共享也可能不区分大小写，宁可保留疑似同名文件。
        key = str((root / path).resolve()).casefold()
        if owner.setdefault(key, torrent_id) != torrent_id:
            shared.add(key)
    return shared


def finalize_complete(db: Session, b: Bangumi) -> bool:
    """全 BD 完成 → 收尾(幂等):清 auto_best、停真订阅、删被 BD 顶替的 web 种子(任务+文件)。

    整包清理前对照下载器清单：每个视频都必须是已归档的正式备用 Web，
    且各自有独立的正式 BD 实盘替代。下载器不可达时保留记录以供重试。
    返回是否执行了收尾动作(用于幂等:无动作即已收尾过)。
    """
    replacements = _complete_bd_files(db, b)
    if not replacements:
        return False
    root = settings.download_root_local.resolve()
    shared_paths = None
    changed = False
    if b.auto_best:
        b.auto_best = False
        changed = True
    for s in _real_subs(db, b.id):
        if s.enabled:
            s.enabled = False
            changed = True
    torrents = db.execute(select(Torrent).join(Subscription).where(
        Subscription.bangumi_id == b.id, Torrent.info_hash.isnot(None),
        Torrent.is_preview.is_(False), Torrent.status == TorrentStatus.ARCHIVED)).scalars().all()
    removed = failures = 0
    for t in torrents:
        vfs = list(t.files)
        if not vfs or any(f.is_active or f.source != "Web" or f.episode_id not in replacements
                          for f in vfs):
            continue
        try:
            # 下载器可能已重命名、或持有数据库未登记的特典。只有文件清单一致才可整包删除。
            web_paths = {Path(f.relative_path) for f in vfs}
            listed = {Path(row["name"].replace("\\", "/")) for row in downloader.files(t.info_hash)}
            if web_paths != listed:
                continue
            web_targets = {(root / path).resolve() for path in web_paths}
            if any(not path.is_relative_to(root) for path in web_targets):
                continue
            if shared_paths is None:
                shared_paths = _shared_file_paths(db, root)
            if any(str(path).casefold() in shared_paths for path in web_targets):
                continue
            # 多个任务依次清理时挂载仍可能断开，每次删前重新核对此包依赖的替代文件。
            if any(not any(media_file_exists(path) for path in replacements[ep_id])
                   for ep_id in {f.episode_id for f in vfs}):
                continue
            downloader.delete(t.info_hash, delete_files=True)
        except Exception as e:  # noqa: BLE001 — 保留映射，稍后重试整包清理
            log.warning("收尾删 web 种子 %s 失败: %s", t.info_hash, e)
            failures += 1
            continue
        for f in vfs:
            db.delete(f)
        db.delete(t)  # ORM 同步删除 torrent_episode 关联
        removed += 1
        changed = True
    if changed:
        db.flush()
        log.info("BD 补全完成收尾:%s → 停扫/停订阅/删冗余 web", b.title)
        message = f"{b.title} 正式正片已全部替换为 BD，已清理 {removed} 个 Web 种子。"
        if failures:
            message += f"另有 {failures} 个清理失败，将在下次核对时重试。"
        _notify(b, "BD 补全完成", message)
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
