"""完成后整理(改 ADR-0001,可开关):qB 原地重命名成 Jellyfin 结构 + 写 NFO/封面。

布局:订阅 save_path 文件夹(= download_root/番剧名)直接作为 Jellyfin 剧集文件夹,
内部 `Season SS/番剧名 SxxExx.ext`,根目录放 tvshow.nfo + poster.jpg + fanart.jpg。
qB 用 renameFile 原地移动 → 仍按新路径做种(SMB 上仅一次改名,不复制)。
仅对 qB 后端生效(BitComet 改名能力未知)。
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.clients.downloader import downloader
from app.config import settings
from app.models import Episode, EpisodeType, Torrent

log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*\n\r\t]')
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _safe(name: str | None) -> str:
    return _ILLEGAL.sub(" ", name or "").strip() or "未命名"


def _cn_to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_CN.get(a, 1) if a else 1) * 10 + (_CN.get(b, 0) if b else 0)
    return _CN.get(s, 1)


def detect_season(title: str | None) -> int:
    """从标题猜续作季号:第二季/2nd Season/Season 3/Ⅱ/Ⅲ…;猜不到返回 1。"""
    t = title or ""
    if m := re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*[季期部]", t):
        return _cn_to_int(m.group(1))
    if m := (re.search(r"\b(\d+)\s*(?:nd|rd|th|st)\s+season\b", t, re.I)
             or re.search(r"\bseason\s+(\d+)\b", t, re.I)
             or re.search(r"(?<![A-Za-z])S(\d+)(?![A-Za-z0-9])", t)):
        return int(m.group(1))
    if re.search(r"Ⅲ|\bIII\b", t):
        return 3
    if re.search(r"Ⅱ|\bII\b", t):
        return 2
    return 1


def _epfmt(n: float | None) -> str:
    if n is None:
        return "00"
    return f"{int(n):02d}" if float(n).is_integer() else f"{n:g}"


def organize_torrent(db: Session, t: Torrent) -> None:
    """对一个已完成种子:重命名其(active 且已定集的)文件 + 写番剧 NFO/封面。幂等。"""
    if not settings.organize_enabled:
        return
    if downloader.name != "qb":
        log.info("整理跳过:下载器 %s 不支持原地重命名", downloader.name)
        return
    sub = t.subscription
    b = sub.bangumi
    show = _safe(b.title)
    root = settings.download_root.replace("\\", "/").rstrip("/")
    sp = (sub.save_path or "").replace("\\", "/").rstrip("/")
    prefix = sp[len(root):].lstrip("/") if sp.startswith(root) else ""   # 番剧名(相对下载根)

    for vf in t.files:
        if not vf.is_active or not vf.episode_id:
            continue
        ep = db.get(Episode, vf.episode_id)
        if ep is None:
            continue
        season_n = (b.season_number or 1) if ep.type == EpisodeType.EP else 0
        ext = Path(vf.relative_path).suffix
        new_name = f"{show} S{season_n:02d}E{_epfmt(ep.number)}{ext}"
        new_rel_sp = f"Season {season_n:02d}/{new_name}"
        old_rel_root = vf.relative_path.replace("\\", "/")
        old_rel_sp = (old_rel_root[len(prefix) + 1:]
                      if prefix and old_rel_root.startswith(prefix + "/") else old_rel_root)
        if old_rel_sp == new_rel_sp:
            continue   # 已整理
        try:
            downloader.rename_file(t.info_hash, old_rel_sp, new_rel_sp)
            vf.relative_path = f"{prefix}/{new_rel_sp}" if prefix else new_rel_sp
            db.flush()
            log.info("整理 #%s → %s", t.id, vf.relative_path)
        except Exception as e:  # noqa: BLE001 — 单文件失败不阻断其余
            log.warning("重命名失败 %s → %s: %s", old_rel_sp, new_rel_sp, e)

    if settings.nfo_enabled and prefix:
        try:
            _write_nfo(b, Path(settings.download_root_local) / prefix)
        except Exception as e:  # noqa: BLE001
            log.warning("写 NFO/封面失败 %s: %s", prefix, e)


def _write_nfo(b, folder: Path) -> None:
    """写 tvshow.nfo + 拷贝竖版/横版封面到剧集文件夹(供 Jellyfin 离线显示 + id 精准匹配)。"""
    if not folder.exists():
        return
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<tvshow>"]
    lines.append(f"  <title>{escape(b.title or '')}</title>")
    if b.title_original:
        lines.append(f"  <originaltitle>{escape(b.title_original)}</originaltitle>")
    if b.year:
        lines.append(f"  <year>{b.year}</year>")
        lines.append(f"  <premiered>{b.year}-01-01</premiered>")
    if b.summary:
        lines.append(f"  <plot>{escape(b.summary)}</plot>")
    if b.studio:
        lines.append(f"  <studio>{escape(b.studio)}</studio>")
    if b.score:
        lines.append(f"  <rating>{b.score}</rating>")
    if b.tmdb_id:
        lines.append(f'  <uniqueid type="tmdb" default="true">{b.tmdb_id}</uniqueid>')
    if b.bgmtv_subject_id:
        lines.append(f'  <uniqueid type="bangumi">{b.bgmtv_subject_id}</uniqueid>')
    lines.append("</tvshow>\n")
    (folder / "tvshow.nfo").write_text("\n".join(lines), encoding="utf-8")

    for src_rel, dest in ((b.poster_path, "poster.jpg"), (b.backdrop_path, "fanart.jpg")):
        if not src_rel:
            continue
        src = Path(settings.data_dir) / src_rel
        if src.exists():
            try:
                shutil.copyfile(src, folder / dest)
            except Exception as e:  # noqa: BLE001
                log.warning("拷贝封面失败 %s: %s", dest, e)
