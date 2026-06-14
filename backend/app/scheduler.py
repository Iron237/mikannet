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


def start() -> None:
    scheduler.add_job(_rss_job, "interval", minutes=settings.poll_interval_min,
                      id="rss_poll", coalesce=True, max_instances=1)
    if settings.auto_dl_interval_min and settings.auto_dl_interval_min > 0:
        scheduler.add_job(_auto_best_job, "interval", minutes=settings.auto_dl_interval_min,
                          id="auto_best", coalesce=True, max_instances=1)
    scheduler.start()


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
