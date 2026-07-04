"""BD 发行扫描(ADR-0004):把 BD 当「发行实体」管理,与剧集/VideoFile 解耦。

两类来源:
- 下载根里 片源=BD 的合集(BDRip)→ bd_release(bdrip, owned=false)。
- 已购原盘目录 /bd-owned(MAKEMKV 原盘)→ bd_release(raw_disc, owned=true),v1 仅占位
  (碟数+总大小),不拆 m2ts。

去特典分支:正片(纯集号)走剧集网格替换 web;特典(带描述标签的视频 / 音频 / 图集·扫描)不再
入库编目、不在网页展示——留在发行目录里,经详情页「打开目录」(mikannet://reveal)用资源管理器浏览。
番剧绑定:仅匹配「已存在」的番剧(按净化标题),匹配不到留空待手动绑(不自动 Mikan 建,避免错绑)。
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Bangumi, BdRelease
from app.parsers.title_parser import detect_source, parse

log = logging.getLogger(__name__)

state: dict = {"running": False, "phase": "", "done": 0, "total": 0, "current": "",
               "releases": 0, "error": None}

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
    (re.compile(r"特典映像|映像特典|特典|Bonus|Extras?\b", re.I), "other"),
]
# 文件名标记 → 类别(不在归类子目录里时用)
_CAT_FILE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"NCOP|NCED|Creditless|无字幕|無字幕|\bNC(?:OP|ED)\b", re.I), "credits"),
    (re.compile(r"\bPV\d*\b|\bCM\d*\b|Trailer|预告|預告|Teaser", re.I), "pv"),
    (re.compile(r"Lyric|\bMV\b|Music[\s_]*Video|音乐视频|音樂視頻", re.I), "music_video"),
    (re.compile(r"\bmenu\b|menu\s*\d|菜单|選單", re.I), "menu"),   # 兼容 Menu01(原 \bmenu\b 漏)
    (re.compile(r"SP[\s_]*Anime|\[\s*SP\b|Special", re.I), "sp_anime"),
    (re.compile(r"Short[\s_]*Drama|短剧|短劇", re.I), "short_drama"),
    (re.compile(r"图集|圖集|\bImages?\b|Gallery", re.I), "gallery"),
    # 纯字母标签特典(无数字、bd_extra_label 不据纯字母判,故在此用专名关键词兜底)
    (re.compile(r"\bIV\b|Interview|访谈|訪談|Making[\s_]*of|Audio[\s_]*Commentary|"
                r"Picture[\s_]*Drama|Drama[\s_]*CD|Sneak[\s_]*Peek", re.I), "other"),
]


def is_extra_dir(name: str) -> bool:
    """该目录名是否属于 BD 特典子目录(供库扫描跳过,避免特典被当剧集登记)。"""
    return any(rx.search(name or "") for rx, _ in _CAT_DIR)


# 裸盘结构夹(其名也命中 detect_source 的 BDMV),不能当成「BD 发行子目录」
_RAW_DISC_NAMES = {"BDMV", "STREAM", "VIDEO_TS", "CERTIFICATE", "BDAV"}


def bd_subfolders(folder: Path) -> list[Path]:
    """folder 的直接子目录中「本身是 BD 命名的发行目录」(排除裸盘结构夹)。

    非空 → folder 是**容器**(BD 库目录如 `BD/`,或作品夹下放了 BD 子目录),其 BD 发行就是
    这些子目录;folder 自身不该再被当作一套发行(否则各发行的 CD/扫描会混进一个巨型发行,
    且整体绑不到番剧)。库扫描与 BD 扫描共用此判断,保证「正片」与「特典」按同一粒度切分。"""
    try:
        return sorted([s for s in folder.iterdir() if s.is_dir()
                       and s.name.upper() not in _RAW_DISC_NAMES
                       and detect_source(s.name) == "BD"], key=lambda s: s.name)
    except OSError:
        return []


def _media_kind(ext: str) -> str | None:
    e = ext.lower()
    if e in _VIDEO_EXT:
        return "video"
    if e in _AUDIO_EXT:
        return "audio"
    if e in _IMAGE_EXT:
        return "image"
    return None


# ---- BD 正片/特典判别(不靠 web 集号:纯集号=正片,带描述标签=特典)----------------
# 技术标签 token(分辨率/编码/profile/音频/封装/CRC/版本);整段全是这类的方括号是技术标签,忽略
_TECH_TOKEN = re.compile(
    r"^(?:\d{3,4}[pi]|\d{3,4}x\d{3,4}|4k|x26[45]|h\.?26[45]|hevc|avc|av1|"
    r"ma10p|hi10p|hi444pp|ma444|main10?|yuv\w*|10bit|8bit|"
    r"flac|aac|ac3|eac3|dts(?:-hd)?|truehd|opus|pcm|mp3|wav|"
    r"bdrip|bd|bluray|blu-ray|web-?dl|webrip|remux|hdr10?|hlg|dv|sdr|"
    r"[0-9a-f]{8}|v\d+)$", re.I)
_LETTER = re.compile(r"[A-Za-z぀-ヿ一-鿿]")   # 拉丁/日文假名/汉字


def _is_tech_bracket(s: str) -> bool:
    toks = [t for t in re.split(r"[\s_&+\-]+", s.strip()) if t]
    return bool(toks) and all(_TECH_TOKEN.match(t) for t in toks)


def bd_extra_label(name: str) -> str | None:
    """BD 发行内单个视频:纯集号正片 → None;带描述标签特典 → 返回标签串。

    依 VCB 式结构 `[字幕组] 作品标题 [<集号或标签>][技术标签]…`:首个方括号=字幕组、技术标签方括号
    (Ma10p_1080p / x265_flac / CRC 等)忽略;剩下「内容方括号」含字母/假名/汉字(BOCCHI THE TALK!/
    Menu01/NCED01/IV…)即特典,只剩纯数字([01]/[12.5])即正片。无内容方括号(整部影片/裸数字)按正片。
    """
    stem = name.rsplit(".", 1)[0]
    for b in re.findall(r"\[([^\]]*)\]", stem)[1:]:   # 去掉首个=字幕组
        bs = b.strip()
        if _is_tech_bracket(bs):
            continue                       # 技术标签忽略
        if not _LETTER.search(bs):
            continue                       # 无字母(纯集号 [01]/[12.5]/[01-12]/[01v2])→ 不是标签
        # 含「字母+数字」的内容方括号 = 标签+序号(BOCCHI THE TALK! 01 / Menu01 / Road to… 01)→ 特典。
        # 纯字母方括号既可能是标签(IV/SP…)也可能是「标题方括号」(DBD-Raws 式 [作品名]),不在此据纯字母
        # 误判(否则 [DBD-Raws][深夜重拳][01] 的标题方括号会把正片误当特典),交 _CAT_FILE 关键词兜底。
        if re.search(r"\d", bs):
            return bs
    return None


def bd_is_extra_video(name: str) -> bool:
    """视频文件名是否为 BD 特典(关键词命中 或 带描述标签);纯集号正片返回 False。

    供库扫描用同一口径判定,确保特典不被当正片登记。
    """
    if any(rx.search(name) for rx, _ in _CAT_FILE):
        return True
    return bd_extra_label(name) is not None


def is_bd_release_dir(folder: Path) -> bool:
    """文件夹内视频多数解析为 source=BD(VCB/Beatrice/Ma10p 等)→ 按 BD 发行处理(无视中文夹名)。"""
    try:
        vids = [p for p in folder.rglob("*")
                if p.is_file() and _media_kind(p.suffix) == "video"]
    except OSError:
        return False
    sample = vids[:50]
    if not sample:
        return False
    bd = sum(1 for p in sample if parse(p.name).source == "BD")
    return bd * 2 >= len(sample)


# ---- 番剧绑定 -----------------------------------------------------------------
_NORM = re.compile(r"[\s·:：!！?？,，.。\-—_~、【】\[\]()()]+")
_BRACKET = re.compile(r"\[[^\]]*\]")


def _norm(s: str) -> str:
    return _NORM.sub("", (s or "").strip()).lower()


def _clean_title(name: str) -> str:
    """去掉 [字幕组]/[Ma10p_1080p] 等方括号段,取核心标题(VCB 式发行夹名才能匹配到番剧)。"""
    return _BRACKET.sub(" ", name or "")


def _match_bangumi(db, name: str) -> Bangumi | None:
    """按净化标题匹配「已存在」番剧;匹配不到返回 None(不自动创建)。"""
    target = _norm(_clean_title(name))
    if not target:
        return None
    for b in db.execute(select(Bangumi)).scalars():
        if _norm(b.title) == target or (b.title_original and _norm(b.title_original) == target):
            return b
    return None


def _auto_bind(db, name: str) -> Bangumi | None:
    """本地匹配不到 → bgm.tv 搜原名/中文名兜底(VCB 等英文/罗马音发行夹名,本地中文番剧匹配不到时)。

    命中先用 bgm.tv 规范名再匹配一次已有番剧(防重复),没有才据 subject 建一个 BD-only 番剧。
    仅在「首次见到该发行」时调用(见 _scan_download_root),避免重扫反复打 bgm.tv。
    """
    from app.clients.bgmtv import bgmtv_client
    from app.services.metadata_service import enrich_bangumi
    try:
        hit = bgmtv_client.search_best(_clean_title(name))
    except Exception as e:  # noqa: BLE001
        log.warning("BD 自动绑定 bgm.tv 搜索 %s 失败: %s", name, e)
        return None
    if not hit:
        return None
    # 已绑同一 subject 的番剧 → 复用;再用规范名匹配已有标题(防与本地番剧重复)
    b = db.execute(select(Bangumi).where(
        Bangumi.bgmtv_subject_id == hit.subject_id)).scalar_one_or_none()
    if b is None:
        for canon in (hit.name_cn, hit.name):
            if canon and (b := _match_bangumi(db, canon)):
                break
    if b is None:
        b = Bangumi(title=hit.name_cn or hit.name or name, bgmtv_subject_id=hit.subject_id)
        db.add(b)
        db.flush()
        try:
            enrich_bangumi(db, b, bgmtv_subject_id=hit.subject_id)
        except Exception as e:  # noqa: BLE001 — 元数据失败不阻塞绑定
            log.warning("BD 自动绑定 enrich 失败 %s: %s", name, e)
    log.info("BD 自动绑定:%s → %s(bgm.tv %s)", name, b.title, hit.subject_id)
    return b


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


def _scan_bdrip_release(db, b, release_dir: Path, root: Path) -> None:
    """登记一个 BDRip 发行(发行实体 + 总大小);去特典分支:不再编目特典。

    特典(带描述标签的视频 / 音频 / 图集)不入库、不在网页展示,留在发行目录里经「打开目录」
    浏览;这里顺带清掉历史扫描可能留下的 BdExtra 行,使库与新口径一致。
    """
    root_rel = str(release_dir.relative_to(root)).replace("\\", "/")
    total = 0
    for p in release_dir.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    rel_obj = _upsert_release(
        db, bangumi_id=(b.id if b else None), title=release_dir.name,
        source_kind="bdrip", root_rel=root_rel, owned=False, disc_count=1, total_size=total)
    for e in list(rel_obj.extras):   # 清历史特典编目(改由「打开目录」浏览)
        db.delete(e)
    db.flush()


def _cleanup_container_release(db, folder: Path, root: Path) -> None:
    """容器文件夹此前可能被误登记成一套「巨型混合发行」(root_path=容器路径,各发行 CD/扫描全混
    其下)→ 删掉它(BdExtra 经 cascade 连带删),让其下每个真实发行重新各自登记。"""
    rel = str(folder.relative_to(root)).replace("\\", "/")
    old = db.execute(select(BdRelease).where(BdRelease.root_path == rel)).scalar_one_or_none()
    if old is not None:
        db.delete(old)
        db.flush()
        log.info("BD 扫描:清理被误登记为发行的容器目录 %s", rel)


def _scan_download_root(db) -> None:
    root = Path(settings.download_root_local)
    if not root.is_dir():
        return
    for folder in sorted([d for d in root.iterdir() if d.is_dir()], key=lambda d: d.name):
        state["current"] = folder.name
        # 容器感知:夹下有 BD 命名子目录 → 它是容器(BD 库目录/作品夹),逐子目录当独立发行,
        # 夹本身不登记(否则各发行混成一个巨型发行);否则夹名本身是 BD → 夹自己一套发行。
        subs = bd_subfolders(folder)
        if subs:
            _cleanup_container_release(db, folder, root)
            releases, parent_name = subs, folder.name
        elif detect_source(folder.name) == "BD" or is_bd_release_dir(folder):
            # 夹名是 BD 标记 或 内容多数是 BD(中文作品名夹里放 VCB 发行)→ 整夹一套发行
            releases, parent_name = [folder], None
        else:
            continue
        for rel_dir in releases:
            has_media = any(_media_kind(p.suffix) for p in rel_dir.rglob("*") if p.is_file())
            if not has_media:
                continue
            # 绑番剧:① 该发行已绑过(手动/上次自动)→ 沿用,不再搜;② 按发行名/父目录名本地匹配;
            # ③ 仍无 且 首次见到该发行 → bgm.tv 联网兜底搜(避免重扫反复打网)。
            root_rel = str(rel_dir.relative_to(root)).replace("\\", "/")
            existing = db.execute(select(BdRelease).where(
                BdRelease.root_path == root_rel)).scalar_one_or_none()
            if existing and existing.bangumi_id:
                b = db.get(Bangumi, existing.bangumi_id)
            else:
                b = _match_bangumi(db, rel_dir.name) or (
                    _match_bangumi(db, parent_name) if parent_name else None)
                if b is None and existing is None:
                    b = _auto_bind(db, rel_dir.name)
            try:
                _scan_bdrip_release(db, b, rel_dir, root)
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
                 releases=0, error=None)
    try:
        with db_session() as db:
            state["phase"] = "扫描下载根 BDRip"
            _scan_download_root(db)
            state["phase"] = "扫描已购原盘"
            _scan_owned_discs(db)
        log.info("BD 扫描完成:发行 %s 套", state["releases"])
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
