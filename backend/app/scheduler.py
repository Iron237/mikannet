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


def start() -> None:
    scheduler.add_job(_rss_job, "interval", minutes=settings.poll_interval_min,
                      id="rss_poll", coalesce=True, max_instances=1)
    scheduler.start()


def reschedule(minutes: int) -> None:
    """RSS 间隔改动后即时重排(设置页 apply 钩子调用)。"""
    if scheduler.running:
        scheduler.reschedule_job("rss_poll", trigger="interval", minutes=minutes)
        log.info("RSS 轮询间隔已改为 %s 分钟", minutes)


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
