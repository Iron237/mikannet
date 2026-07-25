"""元数据服务:Mikan 番剧页 → bgm.tv 关联 → TMDB 背景图 → 图片本地缓存。

三级降级(元数据失败不阻塞订阅创建):
  bgm.tv 全量元数据 → 仅 Mikan 标题/封面 → 手动绑定(API 提供 rebind)。
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.bgmtv import bgmtv_client
from app.clients.mikan import mikan_client
from app.clients.tmdb import tmdb_client
from app.config import settings
from app.database import db_session
from app.models import AiringStatus, Bangumi, Kind

log = logging.getLogger(__name__)

IMAGES_DIR = settings.data_dir / "images"

# 批量重拉元数据/封面(迁移到新机后图片文件没带过来时用):前端轮询此状态
refresh_state: dict = {"running": False, "done": 0, "total": 0, "current": "",
                       "fixed_covers": 0, "errors": 0}


def backfill_ep_start_once() -> None:
    """一次性回填存量番剧的 bgm.tv 章节数据(ep_start 功能上线前建的番剧没有时机拉取)。

    每部走完整章节同步(sync_bgmtv_episodes):首话编号 + 旧 1-based 行整体平移
    + 每集精确放送日/中文标题/章节 id。v2:v1 只写 ep_start 不平移既有剧集行,
    造成详情页两套编号并存 → 换标记键重跑一遍(平移幂等,已对齐的空操作)。
    完成写 Setting 标记不再重复;全部失败(如断网)不写标记,下次启动重试。"""
    from time import sleep

    from app.models import Setting
    KEY = "ep_start_backfilled_v3"   # v3:平移撞号改为合并(历史混编两套编号并行合一)
    with db_session() as db:
        if db.get(Setting, KEY):
            return
        targets = [(b.id, b.title) for b in db.execute(
            select(Bangumi).where(Bangumi.bgmtv_subject_id.is_not(None),
                                  Bangumi.kind == Kind.TV)).scalars()]
    fails = 0
    for bid, title in targets:
        try:
            from app.services.bgm_sync import sync_bgmtv_episodes
            with db_session() as db:
                b = db.get(Bangumi, bid)
                if b is not None:
                    sync_bgmtv_episodes(db, b)
        except Exception as e:  # noqa: BLE001
            fails += 1
            log.warning("章节数据回填失败 %s: %s", title, e)
        sleep(0.5)   # 对 bgm.tv 客气点
    if targets and fails == len(targets):
        log.warning("章节数据回填全部失败(网络?),不写标记,下次启动重试")
        return
    with db_session() as db:
        if db.get(Setting, KEY) is None:
            db.add(Setting(key=KEY, value={"v": True}))
    log.info("章节数据回填完成:检查 %s 部,失败 %s", len(targets), fails)


def start_ep_start_backfill() -> None:
    """启动期调用:后台线程跑一次性回填,不阻塞启动。"""
    threading.Thread(target=backfill_ep_start_once, daemon=True,
                     name="ep-start-backfill").start()


def refresh_air_dates(notify_changes: bool = False) -> dict:
    """重拉所有连载中 TV 番剧的 bgm.tv 放送信息,检测放送延期/提档。

    同步 air_date + 放送星期(顺带静默刷新 eps_total/score,同一次 API 保持新鲜)。
    「变动」= 旧日期非空且新日期不同 —— 首次填充只落库不算变动、不提醒。
    notify_changes=True(定时任务)→ 变动推送通知;手动刷新在 UI 展示结果,不推。
    返回 {"checked": n, "failed": n, "changed": [{id,title,old,new}]}。
    """
    from time import sleep
    with db_session() as db:
        targets = [(b.id, b.bgmtv_subject_id, b.title) for b in db.execute(
            select(Bangumi).where(Bangumi.airing_status == AiringStatus.AIRING,
                                  Bangumi.kind == Kind.TV,
                                  Bangumi.bgmtv_subject_id.is_not(None))).scalars()]
    checked, failed = 0, 0
    changed: list[dict] = []
    for bid, sid, title in targets:
        try:
            s = bgmtv_client.get_subject(sid)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("放送信息刷新失败 %s(subject %s): %s", title, sid, e)
            continue
        checked += 1
        with db_session() as db:
            b = db.get(Bangumi, bid)
            if b is None:
                continue
            new_date = s.date or None
            if new_date and new_date != b.air_date:
                if b.air_date:   # 旧值非空才算「变动」(延期/提档)
                    changed.append({"id": bid, "title": b.title, "number": None,
                                    "old": b.air_date, "new": new_date,
                                    "poster_path": b.poster_path})
                    log.info("放送日期变动:%s %s → %s", b.title, b.air_date, new_date)
                b.air_date = new_date
                try:
                    b.air_weekday = datetime.fromisoformat(new_date).weekday()
                except ValueError:
                    pass
            elif new_date and b.air_weekday is None:
                try:   # 日期没变但星期缺失(老数据)→ 补上,放送表才排得进
                    b.air_weekday = datetime.fromisoformat(new_date).weekday()
                except ValueError:
                    pass
            if s.eps:
                b.eps_total = s.eps
            if s.score:
                b.score = s.score
            try:
                # 每集精确放送日同步:未来集的日期变动 = 按集延期/提档(比整部首播日精确)
                from app.services.bgm_sync import sync_bgmtv_episodes
                for ch in sync_bgmtv_episodes(db, b):
                    changed.append({"id": bid, "title": b.title, "number": ch["number"],
                                    "old": ch["old"], "new": ch["new"],
                                    "poster_path": b.poster_path})
                    log.info("单集放送日变动:%s 第 %s 话 %s → %s",
                             b.title, ch["number"], ch["old"], ch["new"])
            except Exception as e:  # noqa: BLE001
                log.warning("章节同步失败 %s: %s", title, e)
        sleep(0.4)   # 对 bgm.tv 客气点
    if notify_changes and changed:
        from app.services.events import notify
        for ch in changed:
            poster = None
            if ch["poster_path"] and (settings.data_dir / ch["poster_path"]).exists():
                poster = str(settings.data_dir / ch["poster_path"])
            what = (f"第 {ch['number']:g} 话放送日" if ch.get("number") is not None
                    else "放送开始")
            try:
                notify("on_new", f"{ch['title']} · 放送日期变动",
                       f"{what}:{ch['old']} → {ch['new']}(bgm.tv 数据变更,可能延期/提档)",
                       poster)
            except Exception:  # noqa: BLE001 — 推送失败不影响刷新
                log.debug("放送变动推送失败", exc_info=True)
    log.info("放送信息刷新完成:%s 部,失败 %s,日期变动 %s", checked, failed, len(changed))
    return {"checked": checked, "failed": failed,
            "changed": [{k: c.get(k) for k in ("id", "title", "number", "old", "new")}
                        for c in changed]}


def _kind_from_platform(platform: str | None) -> Kind:
    """bgm.tv platform → 番剧形态。剧场版=电影,OVA/OAD=OVA,其余(TV/WEB/…)=TV。"""
    p = (platform or "").strip()
    if "剧场版" in p or "劇場版" in p or p.lower() == "movie":
        return Kind.MOVIE
    if p.upper() in ("OVA", "OAD", "OAV"):
        return Kind.OVA
    return Kind.TV


def _season_str(date: str) -> str | None:
    """'2025-01-10' → '2025冬'(1/4/7/10 月新番季)。"""
    try:
        y, m = int(date[:4]), int(date[5:7])
    except (ValueError, IndexError):
        return None
    season = ("冬", "春", "夏", "秋")[(m - 1) // 3]
    return f"{y}{season}"


def _cache_image(kind: str, key: str, downloader, url: str) -> str | None:
    """下载并缓存图片,返回相对 data_dir 的路径;失败返回 None。"""
    try:
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in {"jpg", "jpeg", "png", "webp"}:
            ext = "jpg"
        name = f"{kind}_{hashlib.md5(key.encode()).hexdigest()[:12]}.{ext}"
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        path = IMAGES_DIR / name
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")   # 原子落地:写一半中断不留残缺文件
            tmp.write_bytes(downloader(url))
            tmp.replace(path)
        return f"images/{name}"
    except Exception as e:  # noqa: BLE001
        log.warning("图片缓存失败 %s: %s", url, e)
        return None


def _infer_airing_status(date: str | None, eps: int | None) -> AiringStatus:
    """无官方字段,以放送开始+集数粗推:开播超过 eps 周 + 4 周缓冲视为完结。"""
    if not date or not eps:
        return AiringStatus.AIRING
    try:
        start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    except ValueError:
        return AiringStatus.AIRING
    weeks = (datetime.now(timezone.utc) - start).days / 7
    return AiringStatus.FINISHED if weeks > eps + 4 else AiringStatus.AIRING


def enrich_bangumi(db: Session, bangumi: Bangumi,
                   bgmtv_subject_id: int | None = None) -> Bangumi:
    """拉取并填充番剧元数据。bgmtv_subject_id 显式传入时为手动绑定。"""
    detail = None
    if bangumi.mikan_bangumi_id:   # 本地导入按 bgm.tv 匹配,可无蜜柑 ID → 跳过蜜柑页
        try:
            detail = mikan_client.get_bangumi(bangumi.mikan_bangumi_id)
            if not bangumi.title or bangumi.title.startswith("bangumi "):
                bangumi.title = detail.title
            if detail.cover_url and not bangumi.poster_path:
                bangumi.poster_path = _cache_image(
                    "poster", f"mikan{bangumi.mikan_bangumi_id}",
                    mikan_client.download_image, detail.cover_url)
        except Exception as e:  # noqa: BLE001
            log.warning("Mikan 番剧页获取失败 %s: %s", bangumi.mikan_bangumi_id, e)

    subject_id = bgmtv_subject_id or (detail.bgmtv_subject_id if detail else None) \
        or bangumi.bgmtv_subject_id
    if subject_id:
        try:
            s = bgmtv_client.get_subject(subject_id)
            bangumi.bgmtv_subject_id = subject_id
            bangumi.title = s.name_cn or s.name or bangumi.title
            bangumi.title_original = s.name
            bangumi.year = int(s.date[:4]) if s.date else None
            bangumi.season_str = _season_str(s.date) if s.date else None
            bangumi.air_date = s.date or None   # 精确首播日(NFO <premiered>,Jellyfin 时间线)
            if s.date:
                try:   # 首播日的星期即每周放送日(放送日历用)
                    bangumi.air_weekday = datetime.fromisoformat(s.date).weekday()
                except ValueError:
                    pass
            bangumi.studio = s.studio
            bangumi.summary = s.summary
            bangumi.score = s.score
            bangumi.eps_total = s.eps
            try:
                # 每集精确数据(放送日/中文标题/章节 id)+ 顺带回填 ep_start
                # (续作从上季续数,第2期章节 13-25 → ep_start=13,字幕组随此编号)
                from app.services.bgm_sync import sync_bgmtv_episodes
                sync_bgmtv_episodes(db, bangumi)
            except Exception as e:  # noqa: BLE001
                log.warning("bgm.tv 章节同步失败 subject=%s: %s", subject_id, e)
            bangumi.airing_status = _infer_airing_status(s.date, s.eps)
            # 形态:AniDB 已绑时由 AniDB 同步主导(更可靠),否则用 bgm.tv platform 推
            if not bangumi.anidb_aid:
                bangumi.kind = _kind_from_platform(s.platform)
            if s.cover_url:   # bgm.tv 封面质量优于 Mikan 缩略图,覆盖
                if p := _cache_image("poster", f"bgmtv{subject_id}",
                                     bgmtv_client.download_image, s.cover_url):
                    bangumi.poster_path = p
        except Exception as e:  # noqa: BLE001
            log.warning("bgm.tv 元数据获取失败 subject=%s: %s", subject_id, e)

    if tmdb_client.enabled and not bangumi.backdrop_path:
        try:
            # 多候选:原名 → 中文名 → 去掉季/期后缀(TMDB 按"剧集"建条目,不带季号)
            candidates = []
            for q in (bangumi.title_original, bangumi.title):
                if q:
                    candidates.append(q)
                    stripped = re.sub(r"\s*(第?\s*[0-9一二三四五六七八九十]+\s*[期季部]|Season\s*\d+|S\d+)\s*$",
                                      "", q).strip()
                    if stripped and stripped != q:
                        candidates.append(stripped)
            for q in dict.fromkeys(candidates):
                try:   # 单候选失败(429/5xx/超时)不该中止其余候选
                    hit = tmdb_client.find_backdrop(q)
                except Exception as e:  # noqa: BLE001
                    log.debug("TMDB 候选 %r 失败: %s", q, e)
                    continue
                if hit:
                    tmdb_id, url = hit
                    bangumi.tmdb_id = tmdb_id
                    bangumi.backdrop_path = _cache_image(
                        "backdrop", f"tmdb{tmdb_id}", tmdb_client.download_image, url)
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("TMDB 背景图获取失败 %s: %s", bangumi.title, e)

    db.flush()
    return bangumi


def _refresh_all() -> None:
    """批量重拉:对每部番剧,若封面/背景图文件已不在(如迁移到新机后没带图片),
    清掉对应路径再 enrich 重新下载;文件还在的保持不动。后台线程,前端轮询 refresh_state。"""
    with db_session() as db:
        ids = db.execute(select(Bangumi.id)).scalars().all()
    refresh_state.update(running=True, done=0, total=len(ids), current="",
                         fixed_covers=0, errors=0)
    try:
        for bid in ids:
            try:
                with db_session() as db:
                    b = db.get(Bangumi, bid)
                    if not b:
                        continue
                    refresh_state["current"] = b.title
                    before = (b.poster_path, b.backdrop_path)
                    for attr in ("poster_path", "backdrop_path"):
                        p = getattr(b, attr)
                        if p and not (settings.data_dir / p).exists():
                            setattr(b, attr, None)   # 文件丢了 → 清路径,让 enrich 重下
                    enrich_bangumi(db, b)
                    if (b.poster_path, b.backdrop_path) != before or \
                            (not before[0] and b.poster_path) or (not before[1] and b.backdrop_path):
                        refresh_state["fixed_covers"] += 1
            except Exception as e:  # noqa: BLE001 — 单部失败不拖垮整批
                log.warning("重拉元数据 #%s 失败: %s", bid, e)
                refresh_state["errors"] += 1
            finally:
                refresh_state["done"] += 1
    finally:
        refresh_state.update(running=False, current="")


def start_refresh_all() -> bool:
    if refresh_state["running"]:
        return False
    threading.Thread(target=_refresh_all, daemon=True).start()
    return True
