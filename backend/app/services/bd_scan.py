"""BD 发行扫描(ADR-0004):把 BD 当「发行实体 + 特典」管理,与剧集/VideoFile 解耦。

两类来源:
- 下载根里 片源=BD 的合集(BDRip)→ bd_release(bdrip, owned=false)+ 逐项分类特典。
- 已购原盘目录 /bd-owned(MAKEMKV 原盘)→ bd_release(raw_disc, owned=true),v1 仅占位
  (碟数+总大小),不拆 m2ts。

正片不进特典(仍走剧集网格);特典含非视频(FLAC 音频 / JPG 图集·扫描)。
番剧绑定:仅匹配「已存在」的番剧(按净化标题),匹配不到留空待手动绑(不自动 Mikan 建,避免错绑)。
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Bangumi, BdExtra, BdRelease
from app.parsers.title_parser import detect_source, parse
from app.services import media_probe

log = logging.getLogger(__name__)

state: dict = {"running": False, "phase": "", "done": 0, "total": 0, "current": "",
               "releases": 0, "extras": 0, "error": None}

_VIDEO_EXT = {".mkv", ".mp4", ".m2ts", ".ts", ".avi", ".wmv", ".mov", ".flv", ".m4v"}
_AUDIO_EXT = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ape", ".dsf", ".dts", ".tak", ".wv"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}

# 特典子目录名 → 类别(顺序敏感:先具体后宽泛)
_CAT_DIR: list[tuple[re.Pattern, str]] = [
    (re.compile(r"特别动画|特別動画|SP[\s_]*Anime|Special[s]?\b|^SPs?$|OAD", re.I), "sp_anime"),
    (re.compile(r"短剧|短劇|Short[\s_]*Drama|Mini[\s_]*Anime|ミニ", re.I), "short_drama"),
    (re.compile(r"^menu$|菜单|選單", re.I), "menu"),
    (re.compile(r"NCOP|NCED|Creditless|无字幕|無字幕|片头|片尾|Clean[\s_]*(?:OP|ED)", re.I), "credits"),
    (re.compile(r"\bPV\b|\bCM\b|Trailer|预告|預告|Teaser|Promotion", re.I), "pv"),
    (re.compile(r"图集|圖集|Images?|Gallery|Pictures?|Pixiv|插画|插畫", re.I), "gallery"),
    (re.compile(r"Scans?|扫描|掃描|书子|書子|Booklet|BK\b", re.I), "scans"),
    (re.compile(r"^CDs?$|原声|原聲|Soundtrack|\bOST\b|サウンドトラック|角色歌|Audio|Music", re.I), "audio"),
]
# 文件名标记 → 类别(不在归类子目录里时用)
_CAT_FILE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"NCOP|NCED|Creditless|无字幕|無字幕|\bNC(?:OP|ED)\b", re.I), "credits"),
    (re.compile(r"\bPV\d*\b|\bCM\d*\b|Trailer|预告|預告|Teaser", re.I), "pv"),
    (re.compile(r"\[\s*menu\s*\]|\bmenu\b|菜单", re.I), "menu"),
    (re.compile(r"SP[\s_]*Anime|\[\s*SP\b|Special", re.I), "sp_anime"),
    (re.compile(r"Short[\s_]*Drama|短剧|短劇", re.I), "short_drama"),
    (re.compile(r"图集|圖集|\bImages?\b|Gallery", re.I), "gallery"),
]


def is_extra_dir(name: str) -> bool:
    """该目录名是否属于 BD 特典子目录(供库扫描跳过,避免特典被当剧集登记)。"""
    return any(rx.search(name or "") for rx, _ in _CAT_DIR)


def _media_kind(ext: str) -> str | None:
    e = ext.lower()
    if e in _VIDEO_EXT:
        return "video"
    if e in _AUDIO_EXT:
        return "audio"
    if e in _IMAGE_EXT:
        return "image"
    return None


def _classify(rel: PurePosixPath) -> tuple[str, str] | None:
    """发行内相对路径 → (类别, 媒体种类);返回 None 表示「正片/非媒体」应跳过。"""
    kind = _media_kind(rel.suffix)
    if kind is None:
        return None
    # 先看所在子目录(发行根下任一层)
    for seg in rel.parts[:-1]:
        for rx, cat in _CAT_DIR:
            if rx.search(seg):
                return cat, kind
    # 再看文件名标记
    for rx, cat in _CAT_FILE:
        if rx.search(rel.name):
            return cat, kind
    # 顶层视频:正片(单话正片)→ 跳过,交剧集网格;其余视频/图片归 other
    if kind == "video":
        p = parse(rel.name)
        if p.ep_type == "regular" and not p.is_batch and len(p.episodes) == 1:
            return None        # 正片,跳过
        return "other", "video"
    if kind == "image":
        return "scans", "image"   # 散落图片当扫描/图集
    return "other", kind


# ---- 番剧绑定 -----------------------------------------------------------------
_NORM = re.compile(r"[\s·:：!！?？,，.。\-—_~、【】\[\]()()]+")


def _norm(s: str) -> str:
    return _NORM.sub("", (s or "").strip()).lower()


def _match_bangumi(db, name: str) -> Bangumi | None:
    """按净化标题匹配「已存在」番剧;匹配不到返回 None(不自动创建)。"""
    target = _norm(name)
    if not target:
        return None
    for b in db.execute(select(Bangumi)).scalars():
        if _norm(b.title) == target or (b.title_original and _norm(b.title_original) == target):
            return b
    return None


# ---- 扫描 ---------------------------------------------------------------------
def _upsert_release(db, *, bangumi_id, title, source_kind, root_rel, owned,
                    disc_count, total_size) -> BdRelease:
    r = db.execute(select(BdRelease).where(BdRelease.root_path == root_rel)).scalar_one_or_none()
    if r is None:
        r = BdRelease(root_path=root_rel)
        db.add(r)
    r.bangumi_id = bangumi_id if bangumi_id is not None else r.bangumi_id
    r.title = title
    r.source_kind = source_kind
    r.disc_count = disc_count
    r.total_size = total_size
    if r.id is None:           # 新建才用扫描推断的拥有状态;已存在保留用户手动改过的
        r.owned = owned
    db.flush()
    return r


def _scan_bdrip_release(db, b, release_dir: Path, root: Path) -> int:
    """登记一个 BDRip 发行 + 分类其特典。返回新增特典数。"""
    root_rel = str(release_dir.relative_to(root)).replace("\\", "/")
    files = [p for p in release_dir.rglob("*") if p.is_file()]
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    rel_obj = _upsert_release(
        db, bangumi_id=(b.id if b else None), title=release_dir.name,
        source_kind="bdrip", root_rel=root_rel, owned=False, disc_count=1, total_size=total)
    existing = {e.relative_path for e in rel_obj.extras}
    added = 0
    for p in files:
        within = p.relative_to(release_dir)
        cat = _classify(PurePosixPath(within.as_posix()))
        if cat is None:
            continue
        category, kind = cat
        file_rel = str(p.relative_to(root)).replace("\\", "/")
        if file_rel in existing:
            continue
        ex = BdExtra(bd_release_id=rel_obj.id, category=category, media_kind=kind,
                     name=p.name, relative_path=file_rel)
        try:
            ex.size = p.stat().st_size
        except OSError:
            pass
        if kind == "video":
            try:
                ex.resolution = media_probe.probe(p).resolution or parse(p.name).resolution
            except Exception:  # noqa: BLE001 — 探测失败不阻塞
                ex.resolution = parse(p.name).resolution
        db.add(ex)
        added += 1
    db.flush()
    return added


def _scan_download_root(db) -> None:
    root = Path(settings.download_root_local)
    if not root.is_dir():
        return
    for folder in sorted([d for d in root.iterdir() if d.is_dir()], key=lambda d: d.name):
        state["current"] = folder.name
        # 发行候选:番剧文件夹本身是 BD 命名,或其下 BD 命名子目录
        candidates: list[Path] = []
        if detect_source(folder.name) == "BD":
            candidates.append(folder)
        for sub in folder.iterdir():
            if sub.is_dir() and detect_source(sub.name) == "BD":
                candidates.append(sub)
        if not candidates:
            continue
        b = _match_bangumi(db, folder.name)
        for rel_dir in candidates:
            has_media = any(_media_kind(p.suffix) for p in rel_dir.rglob("*") if p.is_file())
            if not has_media:
                continue
            try:
                state["extras"] += _scan_bdrip_release(db, b, rel_dir, root)
                state["releases"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("BD 扫描 %s 失败: %s", rel_dir.name, e)


def _scan_owned_discs(db) -> None:
    """已购原盘目录:每个顶层文件夹 = 一套自购原盘(owned),v1 仅占位(碟数+总大小)。"""
    mount = Path(settings.bd_owned_mount)
    if not mount.is_dir():
        return
    for folder in sorted([d for d in mount.iterdir() if d.is_dir()], key=lambda d: d.name):
        state["current"] = folder.name
        try:
            subdirs = [d for d in folder.iterdir() if d.is_dir()]
            disc_count = sum(1 for d in subdirs if re.search(r"DISC\s*\d|BDMV", d.name, re.I)) or 1
            total = 0
            for p in folder.rglob("*.m2ts"):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
            b = _match_bangumi(db, folder.name)
            _upsert_release(db, bangumi_id=(b.id if b else None), title=folder.name,
                            source_kind="raw_disc", root_rel=f"@owned/{folder.name}",
                            owned=True, disc_count=disc_count, total_size=total)
            if b is not None:
                b.bd_owned = True   # 自购原盘 → 番剧排除自动下载
            state["releases"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("BD 原盘扫描 %s 失败: %s", folder.name, e)
    db.flush()


def _run() -> None:
    state.update(running=True, phase="扫描 BD", done=0, total=0, current="",
                 releases=0, extras=0, error=None)
    try:
        with db_session() as db:
            state["phase"] = "扫描下载根 BDRip"
            _scan_download_root(db)
            state["phase"] = "扫描已购原盘"
            _scan_owned_discs(db)
        log.info("BD 扫描完成:发行 %s 套,特典 %s 项", state["releases"], state["extras"])
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)
        log.exception("BD 扫描失败")
    finally:
        state.update(running=False, phase="完成", current="")


def start() -> bool:
    if state["running"]:
        return False
    threading.Thread(target=_run, daemon=True).start()
    return True
