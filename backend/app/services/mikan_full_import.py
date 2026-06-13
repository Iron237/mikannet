"""批量导入蜜柑「我的番组」全部历史订阅(需登录 cookie)。

机制(已逆向实测):带 cookie 抓 /Home/MyBangumi → 解析季度下拉(data-year/data-season)→
逐季度 GET /Home/BangumiCoverFlow?year=&seasonStr= 收集订阅番剧 id(去重,含跨季长篇)→
每部番剧选"种子最多"的字幕组建 mikanarr 订阅(已存在跳过),补元数据 + 首轮轮询。
"""
from __future__ import annotations

import html as _html
import logging
import re
import threading

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Bangumi

log = logging.getLogger(__name__)

state = {"running": False, "phase": "", "done": 0, "total": 0,
         "created": [], "skipped": 0, "errors": 0, "error": None}

_QUARTER_RE = re.compile(r'data-year="(\d+)"\s+data-season="([^"]+)"')
_BANGUMI_ID_RE = re.compile(r"/Home/Bangumi/(\d+)")


def _parse_cookie(raw: str) -> dict:
    """支持粘贴完整 Cookie 头(name=value; …)或仅 .AspNetCore.Identity.Application 的值。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if "=" in raw:
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {".AspNetCore.Identity.Application": raw}


def _client() -> httpx.Client:
    base = settings.mikan_base_url.rstrip("/")
    return httpx.Client(
        proxy=settings.proxy_for("mikan"), trust_env=False, timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Mikanarr/0.1)",
                 "Referer": base + "/Home/MyBangumi"},
        cookies=_parse_cookie(settings.mikan_cookie), follow_redirects=True)


def _collect_subscribed_ids(c: httpx.Client) -> list[int]:
    base = settings.mikan_base_url.rstrip("/")
    r = c.get(base + "/Home/MyBangumi")
    # 蜜柑页面把中文做了 HTML 实体编码(&#x6211;…),先 unescape 再判标记
    text = _html.unescape(r.text)
    if "/Account/Login" in str(r.url) or "我的番组" not in text:
        raise RuntimeError("cookie 无效或已过期(进不去「我的番组」),请重新粘贴 cookie")
    seen_q, quarters = set(), []
    for y, s in _QUARTER_RE.findall(r.text):
        s = _html.unescape(s)
        if (y, s) not in seen_q:
            seen_q.add((y, s))
            quarters.append((y, s))
    state.update(phase="遍历季度", total=len(quarters), done=0)
    ids: dict[int, None] = {}
    for idx, (y, s) in enumerate(quarters, 1):
        state["done"] = idx
        try:
            rr = c.get(base + "/Home/BangumiCoverFlow", params={"year": y, "seasonStr": s})
            for m in _BANGUMI_ID_RE.findall(rr.text):
                ids[int(m)] = None
        except Exception as e:  # noqa: BLE001
            log.warning("季度 %s%s 拉取失败: %s", y, s, e)
    return list(ids)


def _import_bangumi(mid: int) -> str | None:
    """只把番剧加入番剧库(建 Bangumi + 补元数据 + 续作季号);不建 RSS 订阅、不下载。
    已在库中返回 None(跳过)。RSS 订阅由用户后续手动选择字幕组/画质再建。"""
    from app.clients.mikan import mikan_client
    from app.services.metadata_service import enrich_bangumi
    from app.services.organize import detect_season

    with db_session() as db:
        bangumi = db.execute(select(Bangumi).where(
            Bangumi.mikan_bangumi_id == mid)).scalar_one_or_none()
        if bangumi is not None:
            return None   # 已在库,跳过
        detail = mikan_client.get_bangumi(mid)   # 公网番剧页,无需 cookie
        bangumi = Bangumi(mikan_bangumi_id=mid, title=detail.title or f"bangumi {mid}")
        db.add(bangumi)
        db.flush()
        enrich_bangumi(db, bangumi)
        bangumi.season_number = detect_season(bangumi.title)
        return bangumi.title


def _run(cookie: str | None) -> None:
    if cookie:
        from app.services import settings_service
        settings_service.update({"mikan_cookie": cookie})
    state.update(running=True, phase="读取我的番组", done=0, total=0,
                 created=[], skipped=0, errors=0, error=None)
    try:
        with _client() as c:
            ids = _collect_subscribed_ids(c)
        state.update(phase="加入番剧库", done=0, total=len(ids))
        for idx, mid in enumerate(ids, 1):
            state["done"] = idx
            try:
                desc = _import_bangumi(mid)
                if desc:
                    state["created"].append(desc)
                else:
                    state["skipped"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("导入番剧 %s 失败: %s", mid, e)
                state["errors"] += 1
        log.info("蜜柑全量导入完成:新建 %s,跳过 %s,失败 %s",
                 len(state["created"]), state["skipped"], state["errors"])
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        log.exception("蜜柑全量导入失败")
    finally:
        state.update(running=False, phase="完成")


def start(cookie: str | None) -> None:
    if state["running"]:
        return
    threading.Thread(target=_run, args=(cookie,), daemon=True).start()
