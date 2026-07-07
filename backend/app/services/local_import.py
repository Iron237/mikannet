"""本地番剧归类导入:扫描已有视频 → 按作品分组 → 匹配 Mikan/bgm.tv →
移动到 {download_root}/{番剧名}/ → ffprobe → 剧集映射入库。

导入的文件不参与做种,移动不违反 ADR-0001(其针对的是 qB 下载产物)。
入库形态:每部番剧一个停用的「本地导入」订阅 + 一个合成 Torrent 行(ARCHIVED),
文件挂在其下,与正常下载共用库视图/详情页/缺集逻辑。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import anitopy
from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Bangumi, Subscription, Torrent, TorrentStatus, VideoFile
from app.parsers.title_parser import parse
from app.services import media_probe

log = logging.getLogger(__name__)

LOCAL_SUBGROUP_ID = "local"
state = {"running": False, "phase": "", "done": 0, "total": 0,
         "imported": [], "errors": []}
# 扫描进度(异步,前端轮询):走目录 → 逐组匹配蜜柑
scan_state = {"running": False, "phase": "", "files_found": 0, "done": 0, "total": 0,
              "current": "", "result": None, "error": None}


# 结构性子目录(非作品名):碰到则上溯一层取作品文件夹
_STRUCT_DIR = re.compile(
    r"^(season ?\d+|s\d+|disc ?\d+|cd\d*|scans?|bdmv|stream|specials?|sps?|extras?|映像特典|特典|花絮)$",
    re.I)


def _guess_series(path: Path, root: Path) -> str:
    """作品名:用户按「作品名/[发布]/文件」组织,作品名即视频的作品文件夹(直接父目录;
    父目录是 Season/Disc/特典 等结构性目录时再上溯一层)。这比解析文件名稳——文件名常是
    罗马音发布名(Yorimoi、Yoru no Kurage…),蜜柑按中文/日文标题索引,匹配不到;而用户给
    文件夹起的(多为中文)名能直接命中。散落在扫描根的裸文件才退回 anitopy 标题。"""
    d = path.parent
    while d != root and d.parent != d and _STRUCT_DIR.match(d.name.strip()):
        d = d.parent
    if d != root:
        return d.name
    ani = anitopy.parse(path.name) or {}
    title = (ani.get("anime_title") or "").strip()
    return title if len(title) >= 2 else path.stem


# 容器内可扫描的挂载点(/import=本机磁盘源,/import-nas=NAS 源,/downloads=NAS 番剧库)
_CONTAINER_MOUNTS = ("/import-nas", "/import", "/downloads", "/config")


def _resolve_source(p: str) -> str:
    """把用户输入的路径解析成容器内路径:容器路径原样;Windows/NAS 主机路径翻译到对应挂载点。"""
    s = (p or "").strip().replace("\\", "/").rstrip("/") or "/import"
    for m in _CONTAINER_MOUNTS:
        if s == m or s.startswith(m + "/"):
            return s
    for host, mnt in ((settings.import_nas_host, "/import-nas"),
                      (settings.import_win_host, "/import")):
        h = (host or "").replace("\\", "/").rstrip("/")
        if h and (s == h or s.startswith(h + "/")):
            return mnt + s[len(h):]
    raise FileNotFoundError(
        f"无法访问 {p}。本机磁盘源用 /import(.env LOCAL_IMPORT_PATH),"
        f"NAS 源用 /import-nas(.env NAS_IMPORT_PATH);也可直接粘贴已配置的 Windows/NAS 路径。")


def _match_bgm(title: str) -> dict | None:
    """作品名 → bgm.tv 番剧。蜜柑搜索只认罗马音(中文/日文名 0 命中),本地文件夹多为中文 →
    一律走 bgm.tv(原生索引中文 name_cn + 日文 name)。失败/未命中返回 None。"""
    from app.clients.bgmtv import bgmtv_client
    try:
        hit = bgmtv_client.search_best(title)
        if hit:
            return {"bgmtv_subject_id": hit.subject_id,
                    "title": hit.name_cn or hit.name, "name": hit.name}
    except Exception as e:  # noqa: BLE001
        log.warning("bgm.tv 匹配失败 %s: %s", title, e)
    return None


def scan(source: str) -> list[dict]:
    """扫描目录,按作品分组并尝试匹配 bgm.tv。返回分组预览(不动文件)。"""
    root = Path(_resolve_source(source))
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在或未挂载: {source}(容器内解析为 {root})")

    skips = _skip_roots()
    groups: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if _is_skipped(p, skips):
            continue
        if p.is_file() and media_probe.is_video(p):
            groups.setdefault(_guess_series(p, root), []).append(p)

    out = []
    for title, files in groups.items():
        out.append({
            "guess_title": title,
            "files": [str(f) for f in files],
            "episodes": sorted({e for f in files for e in parse(f.name).episodes}),
            "bgm": _match_bgm(title),
        })
    return sorted(out, key=lambda g: -len(g["files"]))


def _run_scan(source: str) -> None:
    """异步扫描,边走边更新 scan_state(供前端进度条轮询)。"""
    scan_state.update(running=True, phase="扫描文件", files_found=0, done=0, total=0,
                      current="", result=None, error=None)
    try:
        root = Path(_resolve_source(source))
        if not root.is_dir():
            raise FileNotFoundError(f"目录不存在或未挂载: {source}(容器内解析为 {root})")
        skips = _skip_roots()
        groups: dict[str, list[Path]] = {}
        for p in root.rglob("*"):
            if _is_skipped(p, skips):
                continue
            if p.is_file() and media_probe.is_video(p):
                groups.setdefault(_guess_series(p, root), []).append(p)
                scan_state["files_found"] += 1
                scan_state["current"] = p.name
        scan_state.update(phase="匹配 bgm.tv", total=len(groups), done=0, current="")
        out = []
        for title, files in groups.items():
            scan_state["current"] = title
            out.append({
                "guess_title": title,
                "files": [str(f) for f in files],
                "episodes": sorted({e for f in files for e in parse(f.name).episodes}),
                "bgm": _match_bgm(title),
            })
            scan_state["done"] += 1
        scan_state["result"] = sorted(out, key=lambda g: -len(g["files"]))
    except Exception as e:  # noqa: BLE001
        scan_state["error"] = str(e)
        log.exception("本地扫描失败")
    finally:
        scan_state.update(running=False, phase="完成", current="")


def start_scan(source: str) -> None:
    if scan_state["running"]:
        return
    threading.Thread(target=_run_scan, args=(source,), daemon=True).start()


_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


def _nas_target_root() -> str | None:
    """若 /import-nas 挂载的 NAS 目录包含 mikannet 下载目录,返回"经该挂载看到的 mikannet 路径"
    (如 /import-nas/mikannet),否则 None。用于让 NAS→NAS 导入落在同一挂载 → 服务器端 rename。"""
    host = (settings.import_nas_host or "").replace("\\", "/").rstrip("/")
    target = (settings.nas_smb_path or "").replace("\\", "/").rstrip("/")
    if host and target and target.startswith(host + "/"):
        return "/import-nas/" + target[len(host) + 1:].lstrip("/")
    return None


def _skip_roots() -> list[str]:
    """扫描时要跳过的目录:已被 mikannet 管理的下载目录(/downloads 及经 NAS 挂载看到的同一目录),
    否则扫 /import-nas 会把里面的 mikannet 子目录(已导入文件)当新文件重扫 → 重名冲突。"""
    roots = ["/downloads/"]
    nas_root = _nas_target_root()
    if nas_root:
        roots.append(nas_root.replace("\\", "/").rstrip("/") + "/")
    return roots


def _is_skipped(p: Path, skips: list[str]) -> bool:
    s = str(p).replace("\\", "/")
    return any(s.startswith(r) for r in skips)


def _safe_move(src: Path, dest: Path) -> None:
    """跨 CIFS 挂载点移动:先试 rename(同卷秒级),否则只流式复制字节(不 copystat,
    避开 CIFS 上 copystat 拷时间戳/权限/xattr 触发的 Errno 5 I/O error)再删源。"""
    try:
        os.replace(str(src), str(dest))   # 同挂载点:秒级 rename
        return
    except OSError:
        pass
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
    try:
        os.remove(src)
    except OSError as e:  # noqa: BLE001 — 删源失败不致命(目标已就位)
        log.warning("导入:删除源文件失败 %s: %s", src, e)


def _import_group(group: dict) -> str:
    from app.services.metadata_service import enrich_bangumi
    bgm = group.get("bgm") or {}
    subject_id = bgm.get("bgmtv_subject_id")
    if not subject_id:
        raise ValueError(f"{group['guess_title']}: 未匹配到 bgm.tv 番剧,跳过")

    with db_session() as db:
        # 按 bgm.tv subject 归一:已存在的番剧(含蜜柑订阅建的)直接挂上,不建重复
        bangumi = db.execute(select(Bangumi).where(
            Bangumi.bgmtv_subject_id == subject_id)).scalar_one_or_none()
        if bangumi is None:
            bangumi = Bangumi(mikan_bangumi_id=None, bgmtv_subject_id=subject_id,
                              title=bgm.get("title") or group["guess_title"])
            db.add(bangumi)
            db.flush()
            enrich_bangumi(db, bangumi, bgmtv_subject_id=subject_id)

        sub = db.execute(select(Subscription).where(
            Subscription.bangumi_id == bangumi.id,
            Subscription.mikan_subgroup_id == LOCAL_SUBGROUP_ID)).scalar_one_or_none()
        safe = _ILLEGAL.sub(" ", bangumi.title).strip() or f"bangumi {subject_id}"
        if sub is None:
            sub = Subscription(bangumi_id=bangumi.id, mikan_subgroup_id=LOCAL_SUBGROUP_ID,
                               subgroup_name="本地导入", enabled=False, backfill=False,
                               save_path=f"{settings.download_root}/{safe}")
            db.add(sub)
            db.flush()

        guid = f"local:{subject_id}:{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        # 官方开播日之前导入的内容必然是先行(上季度网络先行放送等)→ 自动归先行流,
        # 开播后 RSS 追的正式版与之并存(详情页两阶段切换),完结判定不受先行内容干扰
        from app.services.phase import before_official_air
        torrent = Torrent(subscription_id=sub.id, guid=guid,
                          title_raw=f"[本地导入] {bangumi.title}({len(group['files'])} 个文件)",
                          parsed_json={}, torrent_url="", is_batch=True,
                          is_preview=before_official_air(bangumi.air_date),
                          status=TorrentStatus.ARCHIVED,
                          completed_at=datetime.now(timezone.utc))
        db.add(torrent)
        db.flush()

        # 目标目录:若源在 NAS 挂载下、且该挂载含 mikannet 目标 → 走同挂载(_safe_move 的 os.replace
        # 会触发 NAS 服务器端 rename,零网络传输);否则(本机盘源)落 download_root_local(字节复制)。
        nas_root = _nas_target_root()
        src_on_nas = bool(group["files"]) and \
            str(group["files"][0]).replace("\\", "/").startswith("/import-nas/")
        if nas_root and src_on_nas:
            dest_dir = Path(nas_root) / safe
            log.info("导入 %s:NAS 内服务器端移动 → %s", bangumi.title, dest_dir)
        else:
            dest_dir = settings.download_root_local / safe
        dest_dir.mkdir(parents=True, exist_ok=True)
        ok, failed = 0, 0
        seen_rel: set[str] = set()
        touched_eps: set[int] = set()
        for fp in group["files"]:
            src = Path(fp)
            rel = f"{safe}/{src.name}"
            if rel in seen_rel:
                continue   # 同组内多个源映射到同一目标名 → 去重,避免 UNIQUE 冲突
            seen_rel.add(rel)
            # 跨次导入去重:同 relative_path 已登记过则跳过,避免产生多行(不同 torrent_id 绕过
            # UNIQUE)→ 后续库扫描 scalar_one_or_none() 抛 MultipleResultsFound
            if db.execute(select(VideoFile.id).where(
                    VideoFile.relative_path == rel)).first():
                continue
            dest = dest_dir / src.name
            try:
                if not dest.exists():
                    _safe_move(src, dest)
            except Exception as e:  # noqa: BLE001 — 单文件移动失败不拖垮整组
                log.warning("导入:移动失败 %s: %s", src, e)
                failed += 1
                continue
            vf = VideoFile(torrent_id=torrent.id, relative_path=rel,
                           size=dest.stat().st_size)
            db.add(vf)
            db.flush()
            p = parse(src.name)
            vf.subgroup = p.group
            # 片源:文件名优先,判不出则继承源文件夹名上下文(如 [VCB-Studio]…[Ma10p_1080p]
            # 整夹是 BD,内部文件名可能不带 BD 标记)→ 否则 source=None 会排在 Web 之后顶不掉 Web
            from app.parsers.title_parser import detect_source
            vf.source = p.source or detect_source(src.parent.name)
            # 按 ep_type 归类(影片/OVA 不归集留作影片本体;SP/特典走对应类型,不占正片集号)。
            # 复用库扫描的同一映射逻辑,三个导入入口行为一致。
            from app.services.library_scan import _map_episode
            vf.episode_id = _map_episode(db, bangumi, torrent, p)
            if vf.episode_id:
                touched_eps.add(vf.episode_id)
            try:
                r = media_probe.probe(dest)
                vf.resolution = r.resolution or parse(src.name).resolution
                vf.video_codec = r.video_codec
                vf.color_depth = r.color_depth
                vf.hdr = r.hdr
                vf.bitrate = r.bitrate
                vf.audio_tracks = r.audio_tracks
                vf.subtitle_tracks = r.subtitle_tracks
                vf.probed_at = datetime.now(timezone.utc)
            except Exception as e:  # noqa: BLE001
                vf.resolution = vf.resolution or parse(src.name).resolution
                log.warning("导入探测失败 %s: %s", dest, e)
            ok += 1
            db.flush()
        # 导入后重算受影响集的 active(同集若已有 RSS/其它来源文件,保证唯一最优,防多 active)
        from app.services.postprocess import _apply_version_switch
        for ep_id in touched_eps:
            _apply_version_switch(db, ep_id)
        # 统一存储标准:把导入的 active 正片整理进「Season NN/番名 SxxExx.ext」(文件系统 move)。
        # 与 RSS 下载同一落盘结构;非 active(被更优源顶替)不动,留原名原地。
        try:
            from app.services.organize import organize_torrent
            organize_torrent(db, torrent)
        except Exception as e:  # noqa: BLE001 — 整理失败不影响导入结果(文件已入库)
            log.warning("导入后整理失败 %s: %s", bangumi.title, e)
        return f"{bangumi.title}: {ok} 个文件" + (f"({failed} 个失败)" if failed else "")


def run_import(groups: list[dict]) -> None:
    state.update(running=True, phase="导入中", done=0, total=len(groups),
                 imported=[], errors=[])
    try:
        for g in groups:
            state["done"] += 1
            try:
                state["imported"].append(_import_group(g))
            except Exception as e:  # noqa: BLE001
                log.exception("导入分组失败")
                state["errors"].append(str(e))
    finally:
        state.update(running=False, phase="完成")


def start_import(groups: list[dict]) -> None:
    threading.Thread(target=run_import, args=(groups,), daemon=True).start()
