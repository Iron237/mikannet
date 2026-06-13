"""导入蜜柑个人订阅:解析聚合 RSS → 逐条目反查番剧+字幕组 → 批量建订阅。

后台执行(每个条目要抓一次 Episode 页);前端轮询订阅列表看进度。
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from app.clients.mikan import mikan_client
from app.config import settings
from app.database import db_session
from app.models import Bangumi, Subscription

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/import", tags=["import"])

MAX_EPISODE_FETCHES = 80
_state = {"running": False, "done": 0, "total": 0, "created": [], "errors": 0}


def _import_job(rss_url: str, backfill: bool) -> None:
    import re

    from app.api.subscriptions import _safe_dirname
    from app.services.metadata_service import enrich_bangumi
    from app.services.rss_engine import poll_subscription

    _state.update(running=True, done=0, total=0, created=[], errors=0)
    try:
        items = mikan_client.fetch_rss_url(rss_url)
        # 同一番剧+字幕组的条目大量重复,按 Episode hash 去重后逐个反查
        seen_pairs: set[tuple[int, str]] = set()
        urls = list(dict.fromkeys(i.guid for i in items))[:MAX_EPISODE_FETCHES]
        _state["total"] = len(urls)
        for url in urls:
            _state["done"] += 1
            try:
                pair = mikan_client.get_episode_origin(url)
            except Exception as e:  # noqa: BLE001
                log.warning("导入:反查失败 %s: %s", url, e)
                _state["errors"] += 1
                continue
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            bangumi_id, subgroup_id = pair
            with db_session() as db:
                bangumi = db.execute(select(Bangumi).where(
                    Bangumi.mikan_bangumi_id == bangumi_id)).scalar_one_or_none()
                if bangumi is None:
                    bangumi = Bangumi(mikan_bangumi_id=bangumi_id, title=f"bangumi {bangumi_id}")
                    db.add(bangumi)
                    db.flush()
                    enrich_bangumi(db, bangumi)
                exists = db.execute(select(Subscription).where(
                    Subscription.bangumi_id == bangumi.id,
                    Subscription.mikan_subgroup_id == subgroup_id)).scalar_one_or_none()
                if exists:
                    continue
                detail = None
                try:
                    detail = mikan_client.get_bangumi(bangumi_id)
                except Exception:  # noqa: BLE001
                    pass
                subgroup_name = next((g.name for g in (detail.subgroups if detail else [])
                                      if g.subgroup_id == subgroup_id), None)
                sub = Subscription(
                    bangumi_id=bangumi.id, mikan_subgroup_id=subgroup_id,
                    subgroup_name=subgroup_name, backfill=backfill,
                    exclude_batch=bangumi.airing_status.value == "airing",
                    save_path=f"{settings.download_root}/{_safe_dirname(bangumi.title)}",
                )
                db.add(sub)
                db.flush()
                _state["created"].append(f"{bangumi.title} × {subgroup_name or subgroup_id}")
                poll_subscription(db, sub)   # 立即首轮(backfill=False 时只建基线)
        log.info("导入完成:新建 %s 个订阅", len(_state["created"]))
    except Exception:  # noqa: BLE001
        log.exception("导入任务异常")
        _state["errors"] += 1
    finally:
        _state["running"] = False


@router.post("/mikan")
def import_mikan(payload: dict, background: BackgroundTasks):
    rss_url = (payload.get("rss_url") or "").strip()
    if "/RSS/" not in rss_url:
        raise HTTPException(400, "请填写蜜柑的 RSS 链接(mikanani.me/RSS/MyBangumi?token=…)")
    if _state["running"]:
        raise HTTPException(409, "已有导入任务在进行中")
    backfill = bool(payload.get("backfill", False))
    background.add_task(_import_job, rss_url, backfill)
    return {"started": True}


@router.get("/mikan/status")
def import_status():
    return _state


# ---- 本地番剧导入 --------------------------------------------------------------

@router.post("/local/scan")
def local_scan(payload: dict):
    """扫描容器内目录(需挂载),返回识别分组预览,不动文件。"""
    from app.services.local_import import scan
    path = (payload.get("path") or "/import").strip()
    try:
        return scan(path)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/local/run")
def local_run(payload: dict):
    """执行导入:移动文件 + 元数据 + ffprobe 入库。payload: {"groups": [scan 返回的分组]}"""
    from app.services.local_import import start_import, state
    groups = payload.get("groups") or []
    if not groups:
        raise HTTPException(400, "没有要导入的分组")
    if state["running"]:
        raise HTTPException(409, "已有导入任务进行中")
    start_import(groups)
    return {"started": True, "total": len(groups)}


@router.get("/local/status")
def local_status():
    from app.services.local_import import state
    return state
