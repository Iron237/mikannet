"""批量导入蜜柑「我的番组」全部历史订阅(需登录 cookie)。

机制(已逆向实测):带 cookie 抓 /Home/MyBangumi → 解析季度下拉(data-year/data-season)→
逐季度 GET /Home/BangumiCoverFlow?year=&seasonStr= 收集订阅番剧 id(去重,含跨季长篇)→
每部番剧选"种子最多"的字幕组建 mikannet 订阅(已存在跳过),补元数据 + 首轮轮询。
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

# 季度时序排名:冬(1-3月)<春<夏<秋,据此把 (年,季) 映射成可比键 年*10+季
_SEASON_RANK = {"冬": 1, "春": 2, "夏": 3, "秋": 4,
                "winter": 1, "spring": 2, "summer": 3, "fall": 4, "autumn": 4}


def _season_rank(s: str | None) -> int:
    s = (s or "").strip()
    return _SEASON_RANK.get(s) or _SEASON_RANK.get(s.lower(), 0)


def _quarter_key(year, season) -> int:
    try:
        return int(year) * 10 + _season_rank(season)
    except (TypeError, ValueError):
        return 0


# 合法 cookie name(RFC6265 token);value 里去掉任何非法 header 字符(换行/引号等)
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_COOKIE_VAL_BAD = re.compile(r"[\x00-\x20\"';,\\\x7f]")


def _parse_cookie(raw: str) -> dict:
    """支持三种粘贴形式,统一抽成 {name: value}:
    1) 浏览器 Cookie 扩展(Cookie-Editor/EditThisCookie)导出的 JSON 数组/对象
       —— 形如 [{"name":..,"value":..}, …],自动抽 name/value;
    2) 完整 Cookie 头  name=value; name2=value2 …;
    3) 仅 .AspNetCore.Identity.Application 的裸值。
    只保留 name 合法、value 无非法 header 字符的项,避免拼进 Cookie 头时炸
    Illegal header value。"""
    import json

    raw = (raw or "").strip()
    if not raw:
        return {}

    # 形式 1:JSON(数组或单对象)——扩展导出格式
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if data is not None:
            items = data if isinstance(data, list) else [data]
            out = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name", "")).strip()
                val = str(it.get("value", "")).strip()
                if name and _COOKIE_NAME_RE.match(name) and not _COOKIE_VAL_BAD.search(val):
                    out[name] = val
            if out:
                return out

    # 形式 2:Cookie 头(单行 name=value; …)
    if "=" in raw and "\n" not in raw:
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and _COOKIE_NAME_RE.match(k) and not _COOKIE_VAL_BAD.search(v):
                    out[k] = v
        if out:
            return out

    # 形式 3:裸值(去掉可能被换行/空格截断的残留)
    return {".AspNetCore.Identity.Application": re.sub(r"\s+", "", raw)}


def _serialize_cookie(d: dict) -> str:
    """把解析后的 cookie 还原成规范的单行 Cookie 头,用于存进设置(不再存原始 JSON)。"""
    return "; ".join(f"{k}={v}" for k, v in d.items())


def _client() -> httpx.Client:
    base = settings.mikan_base_url.rstrip("/")
    return httpx.Client(
        proxy=settings.proxy_for("mikan"), trust_env=False, timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Mikannet/0.1)",
                 "Referer": base + "/Home/MyBangumi"},
        cookies=_parse_cookie(settings.mikan_cookie), follow_redirects=True)


def _collect_subscribed_ids(c: httpx.Client, since_key: int | None = None,
                            until_key: int | None = None) -> list[int]:
    base = settings.mikan_base_url.rstrip("/")
    r = c.get(base + "/Home/MyBangumi")
    # 蜜柑页面把中文做了 HTML 实体编码(&#x6211;…),先 unescape 再判标记
    text = _html.unescape(r.text)
    if "/Account/Login" in str(r.url) or "我的番组" not in text:
        raise RuntimeError("cookie 无效或已过期(进不去「我的番组」),请重新粘贴 cookie")
    seen_q, quarters = set(), []
    for y, s in _QUARTER_RE.findall(r.text):
        s = _html.unescape(s)
        if (y, s) in seen_q:
            continue
        qk = _quarter_key(y, s)                 # 时间范围筛选:季度键落在 [since, until] 内
        if qk and ((since_key and qk < since_key) or (until_key and qk > until_key)):
            continue
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


def _import_bangumi(mid: int) -> tuple[int, str] | None:
    """只把番剧加入番剧库(建 Bangumi + 补元数据 + 续作季号);不建 RSS 订阅、不下载。
    返回 (bangumi_id, title);已在库中返回 None(跳过)。RSS/下载由用户后续或 auto_dl 处理。"""
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
        return bangumi.id, bangumi.title


def _run(cookie: str | None, since_key: int | None, until_key: int | None,
         auto_dl: bool) -> None:
    if cookie:
        parsed = _parse_cookie(cookie)
        if not parsed:
            state.update(running=False, phase="完成",
                         error="没能从粘贴内容里解析出 cookie,请贴 .AspNetCore.Identity.Application 的值")
            return
        from app.services import settings_service
        settings_service.update({"mikan_cookie": _serialize_cookie(parsed)})
    state.update(running=True, phase="读取我的番组", done=0, total=0,
                 created=[], skipped=0, errors=0, error=None)
    created_ids: list[int] = []
    try:
        with _client() as c:
            ids = _collect_subscribed_ids(c, since_key, until_key)
        state.update(phase="加入番剧库", done=0, total=len(ids))
        for idx, mid in enumerate(ids, 1):
            state["done"] = idx
            try:
                res = _import_bangumi(mid)
                if res:
                    created_ids.append(res[0])
                    state["created"].append(res[1])
                else:
                    state["skipped"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("导入番剧 %s 失败: %s", mid, e)
                state["errors"] += 1
        log.info("蜜柑全量导入完成:新建 %s,跳过 %s,失败 %s",
                 len(state["created"]), state["skipped"], state["errors"])
        # 入库后可选「智能下载」补齐(用画质关卡挑最优源);仅对本次新建的番剧
        if auto_dl and created_ids:
            from app.services import auto_best
            if auto_best.start_scan(created_ids):
                state["phase"] = f"已触发智能下载({len(created_ids)} 部)"
                log.info("蜜柑导入后触发智能下载:%s 部", len(created_ids))
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        log.exception("蜜柑全量导入失败")
    finally:
        state.update(running=False)
        if not state["phase"].startswith("已触发"):
            state["phase"] = "完成"


def start(cookie: str | None, since_year=None, since_season="", until_year=None,
          until_season="", auto_dl: bool = False) -> None:
    if state["running"]:
        return
    # 季度边界 → 可比键。下界无季 → 该年初(含全年);上界无季 → 该年末(rank 9,含全年)
    since_key = (int(since_year) * 10 + _season_rank(since_season)) if since_year else None
    until_key = (int(until_year) * 10 + (_season_rank(until_season) or 9)) if until_year else None
    threading.Thread(target=_run, args=(cookie, since_key, until_key, auto_dl),
                     daemon=True).start()
