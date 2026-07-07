"""发现:bgm.tv 每日放送(当季全站新番)+「在看」导入。"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Bangumi

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discover", tags=["discover"])

_cal_cache: dict = {"ts": 0.0, "data": None}   # bgm.tv 每日放送,6h TTL


@router.get("/calendar")
def discover_calendar(db: Session = Depends(get_db)):
    """当季每日放送(bgm.tv 全站数据,不只已订阅的)。已入库的带 local_id。"""
    from app.clients.bgmtv import bgmtv_client
    if _cal_cache["data"] is None or time.time() - _cal_cache["ts"] > 6 * 3600:
        try:
            raw = bgmtv_client.weekly_calendar()
        except Exception as e:  # noqa: BLE001
            if _cal_cache["data"] is None:
                raise HTTPException(502, f"bgm.tv 每日放送获取失败: {e}") from None
            raw = None   # 拉取失败但有旧缓存 → 用旧的
        if raw is not None:
            days: list[list[dict]] = [[] for _ in range(7)]
            for grp in raw:
                wd = int((grp.get("weekday") or {}).get("id") or 0) - 1   # bgm 1=周一 → 0
                if not 0 <= wd <= 6:
                    continue
                for it in grp.get("items") or []:
                    days[wd].append({
                        "subject_id": it.get("id"),
                        "title": it.get("name_cn") or it.get("name") or "",
                        "name": it.get("name") or "",
                        "score": (it.get("rating") or {}).get("score"),
                        "image": (it.get("images") or {}).get("large"),
                        "air_date": it.get("air_date"),
                        "eps": it.get("eps") or it.get("eps_count"),
                    })
            _cal_cache["data"] = days
            _cal_cache["ts"] = time.time()
    days = _cal_cache["data"]
    # 库内标记每次现查(订阅动作后即时反映)
    ids = [x["subject_id"] for d in days for x in d if x.get("subject_id")]
    local = {row[1]: row[0] for row in db.execute(
        select(Bangumi.id, Bangumi.bgmtv_subject_id)
        .where(Bangumi.bgmtv_subject_id.in_(ids))).all()} if ids else {}
    return {"days": [[{**x, "local_id": local.get(x.get("subject_id"))} for x in d]
                     for d in days]}


@router.post("/import-watching")
def import_watching():
    """把 bgm.tv「在看」列表导入为库内番剧(需设置页配置个人令牌)。"""
    from app.services.bgm_sync import import_watching as run
    try:
        return {"ok": True, **run()}
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
