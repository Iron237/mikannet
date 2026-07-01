"""完成后整理(改 ADR-0001,可开关):把文件规整成 Jellyfin 结构 + 写 NFO/封面。

布局:订阅 save_path 文件夹(= download_root/番剧名)直接作为 Jellyfin 剧集文件夹,
内部 `Season SS/番剧名 SxxExx.ext`,根目录放 tvshow.nfo + poster.jpg + fanart.jpg。
统一存储标准:两条整理路径落同一目录结构 ——
- **下载器托管**(有 info_hash + qB 后端):qB renameFile 原地移动 → 仍按新路径做种(SMB 上仅一次改名)。
- **非托管**(本地导入,无 info_hash):文件系统层面 move(os.replace,同一挂载内即改名,零复制)。
改名前把原始文件名存进 VideoFile.original_name,保留字幕组/版本等信息可回溯、详情页展示。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path, PurePosixPath
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


def _fs_move(old_rel_root: str, new_rel_root: str) -> None:
    """非托管文件(本地导入)在挂载内直接 move:download_root_local 相对路径 → 同左。

    os.replace 同一挂载即服务器端改名(零复制);目标已存在则报错交上层跳过。
    """
    base = Path(settings.download_root_local)
    src = base / old_rel_root
    dst = base / new_rel_root
    if not src.exists():
        raise FileNotFoundError(str(src))
    if dst.exists():
        raise FileExistsError(str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _rename_one(t: Torrent, old_rel_sp: str, new_rel_sp: str,
                old_rel_root: str, new_rel_root: str) -> None:
    """把一个文件从旧位置整理到新位置。托管种子走下载器改名,非托管走文件系统 move。"""
    if t.info_hash and downloader.name == "qb":
        downloader.rename_file(t.info_hash, old_rel_sp, new_rel_sp)   # 相对种子根(save_path)
    elif not t.info_hash:
        _fs_move(old_rel_root, new_rel_root)                          # 相对 download_root
    else:
        raise RuntimeError(f"下载器 {downloader.name} 托管的种子不支持整理改名")


def organize_torrent(db: Session, t: Torrent) -> None:
    """对一个已完成种子:整理其(active 且已定集的)文件到 Season 结构 + 写番剧 NFO/封面。幂等。"""
    if not settings.organize_enabled:
        return
    sub = t.subscription
    b = sub.bangumi
    show = _safe(b.title)
    root = settings.download_root.replace("\\", "/").rstrip("/")
    sp = (sub.save_path or "").replace("\\", "/").rstrip("/")
    prefix = sp[len(root):].lstrip("/") if sp.startswith(root) else ""   # 番剧名(相对下载根)

    # 已被本种子占用的相对路径集合 → 预判撞名,避免 UNIQUE(torrent_id, relative_path) 冲突
    # (BD 合集里 SP/菜单/多版本可能算出同一 SxxExx 目标;撞名就跳过,绝不让 flush 报错毒化事务)
    used = {(f.relative_path or "").replace("\\", "/") for f in t.files}
    for vf in t.files:
        if not vf.is_active or not vf.episode_id:
            continue
        ep = db.get(Episode, vf.episode_id)
        # 只整理正片 + 带集号的特别篇;菜单/OP·ED/PV/无集号特典留原名(Jellyfin 当 extras)
        if ep is None or ep.number is None or ep.type not in (
                EpisodeType.REGULAR, EpisodeType.SPECIAL):
            continue
        season_n = (b.season_number or 1) if ep.type == EpisodeType.REGULAR else 0
        ext = Path(vf.relative_path).suffix
        new_name = f"{show} S{season_n:02d}E{_epfmt(ep.number)}{ext}"
        # 先行(抢先版)单独归到「先行版」子目录(不进 Season,Jellyfin 不当正片集扫描);
        # 正式版照常落 Season SS。同集先行/正式各留一份,互不覆盖。
        new_rel_sp = (f"先行版/{new_name}" if t.is_preview
                      else f"Season {season_n:02d}/{new_name}")
        new_full = (f"{prefix}/{new_rel_sp}" if prefix else new_rel_sp)
        old_rel_root = vf.relative_path.replace("\\", "/")
        old_rel_sp = (old_rel_root[len(prefix) + 1:]
                      if prefix and old_rel_root.startswith(prefix + "/") else old_rel_root)
        if old_rel_sp == new_rel_sp:
            continue   # 已整理
        if new_full in used:
            log.warning("整理 #%s 跳过撞名目标 %s(%s)", t.id, new_full, old_rel_sp)
            continue
        try:
            _rename_one(t, old_rel_sp, new_rel_sp, old_rel_root, new_full)
            if not vf.original_name:   # 首次整理前的原始名(保留字幕组/版本等全部信息)
                vf.original_name = PurePosixPath(old_rel_root).name
            used.discard(old_rel_root)
            vf.relative_path = new_full
            used.add(new_full)
            db.flush()
            log.info("整理 #%s → %s", t.id, vf.relative_path)
        except Exception as e:  # noqa: BLE001 — 单文件失败不阻断其余
            log.warning("整理失败 %s → %s: %s", old_rel_sp, new_rel_sp, e)

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
    premiered = getattr(b, "air_date", None) or (f"{b.year}-01-01" if b.year else None)
    if premiered:
        lines.append(f"  <premiered>{escape(premiered)}</premiered>")
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
