"""元数据服务:Mikan 番剧页 → bgm.tv 关联 → TMDB 背景图 → 图片本地缓存。

三级降级(元数据失败不阻塞订阅创建):
  bgm.tv 全量元数据 → 仅 Mikan 标题/封面 → 手动绑定(API 提供 rebind)。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients.bgmtv import bgmtv_client
from app.clients.mikan import mikan_client
from app.clients.tmdb import tmdb_client
from app.config import settings
from app.models import AiringStatus, Bangumi

log = logging.getLogger(__name__)

IMAGES_DIR = settings.data_dir / "images"


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
            path.write_bytes(downloader(url))
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
            if s.date:
                try:   # 首播日的星期即每周放送日(放送日历用)
                    bangumi.air_weekday = datetime.fromisoformat(s.date).weekday()
                except ValueError:
                    pass
            bangumi.studio = s.studio
            bangumi.summary = s.summary
            bangumi.score = s.score
            bangumi.eps_total = s.eps
            bangumi.airing_status = _infer_airing_status(s.date, s.eps)
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
                if hit := tmdb_client.find_backdrop(q):
                    tmdb_id, url = hit
                    bangumi.tmdb_id = tmdb_id
                    bangumi.backdrop_path = _cache_image(
                        "backdrop", f"tmdb{tmdb_id}", tmdb_client.download_image, url)
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("TMDB 背景图获取失败 %s: %s", bangumi.title, e)

    db.flush()
    return bangumi
