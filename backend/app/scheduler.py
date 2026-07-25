"""APScheduler:RSS 周期轮询。"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import db_session

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def _rss_job() -> None:
    from app.services.rss_engine import poll_all
    with db_session() as db:
        results = poll_all(db)
    log.info("RSS 轮询完成: %s", results)


def _auto_best_job() -> None:
    from app.services.auto_best import scan_auto_all
    scan_auto_all()


def _drain_completed_job() -> None:
    """补跑后处理:把卡在 COMPLETED 的种子(tracker 完成入队后因重启/积压漏掉)重新入队。

    process_torrent 幂等(已探测文件跳过、无失败才转 ARCHIVED)→ 重复入队无害。
    保证「种子下好的文件」最终都会生成 VideoFile,在详情页显示文件详情。
    """
    from sqlalchemy import select

    from app.models import Torrent, TorrentStatus
    from app.services.postprocess import enqueue
    with db_session() as db:
        ids = db.execute(select(Torrent.id).where(
            Torrent.status == TorrentStatus.COMPLETED)).scalars().all()
    for tid in ids:
        enqueue(tid)
    if ids:
        log.info("补跑后处理:重新入队 %s 个 COMPLETED 种子", len(ids))


def _lifecycle_job() -> None:
    """每日:完结自动转补全 + 全 BD 完成收尾(Q1/Q2 兜底,即时触发见 postprocess/库扫描)。"""
    from app.services.lifecycle import daily_reconcile
    daily_reconcile()


def _air_refresh_job() -> None:
    """定期重拉连载番剧的 bgm.tv 放送信息:检测放送延期/提档并推送(首次填充不提醒)。"""
    from app.services.metadata_service import refresh_air_dates
    refresh_air_dates(notify_changes=True)


def _storage_watchdog_job() -> None:
    """SMB 挂载看门狗:运行中途 SMB 断线会留下僵尸挂载(/downloads 连不上),
    仅靠启动/手动重挂无法自愈。定期检测,发现未正常挂载即自动重挂(重挂含清僵尸挂载)。"""
    if settings.storage_mode != "smb" or not settings.smb_host_path:
        return
    from app.services import storage
    if storage.is_mounted():
        return
    log.warning("存储看门狗:/downloads 未正常挂载,尝试自动重挂…")
    result = storage.apply()
    if result.get("mounted"):
        log.info("存储看门狗:已自动重挂 /downloads")
    else:
        log.warning("存储看门狗:重挂未成功(SMB 可能仍不可达): %s", result.get("error"))


def start() -> None:
    scheduler.add_job(_rss_job, "interval", minutes=settings.poll_interval_min,
                      id="rss_poll", coalesce=True, max_instances=1)
    scheduler.add_job(_drain_completed_job, "interval", minutes=5,
                      id="drain_completed", coalesce=True, max_instances=1)
    scheduler.add_job(_lifecycle_job, "interval", hours=24,
                      id="lifecycle", coalesce=True, max_instances=1)
    scheduler.add_job(_air_refresh_job, "interval", hours=12,
                      id="air_refresh", coalesce=True, max_instances=1)
    if settings.storage_mode == "smb":
        scheduler.add_job(_storage_watchdog_job, "interval", minutes=2,
                          id="storage_watchdog", coalesce=True, max_instances=1)
    if settings.auto_dl_interval_min and settings.auto_dl_interval_min > 0:
        scheduler.add_job(_auto_best_job, "interval", minutes=settings.auto_dl_interval_min,
                          id="auto_best", coalesce=True, max_instances=1)
    scheduler.start()


def ensure_storage_watchdog() -> None:
    """按需注册 SMB 看门狗(首次向导把存储切到 smb 时调用)。

    start() 只在启动瞬间按当时的 storage_mode 决定是否注册——全新部署先启动后配 SMB,
    看门狗会一直缺席到下次重启,断线无法自愈。幂等:已注册则跳过。"""
    if not scheduler.running:
        return
    if settings.storage_mode == "smb" and scheduler.get_job("storage_watchdog") is None:
        scheduler.add_job(_storage_watchdog_job, "interval", minutes=2,
                          id="storage_watchdog", coalesce=True, max_instances=1)
        log.info("已注册 SMB 存储看门狗(运行时启用)")


def reschedule_auto_best(minutes: int) -> None:
    """智能扫描间隔改动后即时重排;0/负 = 移除定期任务(仅保留手动)。"""
    if not scheduler.running:
        return
    job = scheduler.get_job("auto_best")
    if minutes and minutes > 0:
        if job:
            scheduler.reschedule_job("auto_best", trigger="interval", minutes=minutes)
        else:
            scheduler.add_job(_auto_best_job, "interval", minutes=minutes,
                              id="auto_best", coalesce=True, max_instances=1)
        log.info("智能扫描间隔已设为 %s 分钟", minutes)
    elif job:
        scheduler.remove_job("auto_best")
        log.info("已关闭定期智能扫描")


def reschedule(minutes: int) -> None:
    """RSS 间隔改动后即时重排(设置页 apply 钩子调用)。"""
    if scheduler.running:
        scheduler.reschedule_job("rss_poll", trigger="interval", minutes=minutes)
        log.info("RSS 轮询间隔已改为 %s 分钟", minutes)


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def resume_after_failed_update() -> None:
    """完整更新 helper 静默失败(容器没被重建)时恢复调度。

    _quiesce 已 shutdown;APScheduler 实例 shutdown 后不可复用 → 重建全局实例再照常 start
    (start() 会重新注册全部任务)。幂等:仍在跑则不动。"""
    global scheduler
    if scheduler.running:
        return
    scheduler = BackgroundScheduler()
    start()
    log.info("完整更新未生效,调度器已恢复运行")
