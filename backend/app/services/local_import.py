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
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.parsers.title_parser import parse
from app.services import media_probe

log = logging.getLogger(__name__)

LOCAL_SUBGROUP_ID = "local"
state = {"running": False, "phase": "", "done": 0, "total": 0,
         "imported": [], "errors": []}
# 扫描进度(异步,前端轮询):走目录 → 逐组匹配蜜柑
scan_state = {"running": False, "phase": "", "files_found": 0, "done": 0, "total": 0,
              "current": "", "result": None, "error": None}


def _guess_series(path: Path) -> str:
    """文件 → 作品名:anitopy anime_title,兜底用上级目录名。"""
    ani = anitopy.parse(path.name) or {}
    title = (ani.get("anime_title") or "").strip()
    if len(title) >= 2:
        return title
    parent = path.parent.name
    return re.sub(r"[\[\(【].*?[\]\)】]", "", parent).strip() or parent


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


def scan(source: str) -> list[dict]:
    """扫描目录,按作品分组并尝试匹配 Mikan。返回分组预览(不动文件)。"""
    from app.clients.mikan import mikan_client
    root = Path(_resolve_source(source))
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在或未挂载: {source}(容器内解析为 {root})")

    skips = _skip_roots()
    groups: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if _is_skipped(p, skips):
            continue
        if p.is_file() and media_probe.is_video(p):
            groups.setdefault(_guess_series(p), []).append(p)

    out = []
    for title, files in groups.items():
        mikan = None
        try:
            hits = mikan_client.search(title)
            if hits:
                mikan = {"mikan_bangumi_id": hits[0].mikan_bangumi_id, "title": hits[0].title}
        except Exception as e:  # noqa: BLE001
            log.warning("Mikan 匹配失败 %s: %s", title, e)
        out.append({
            "guess_title": title,
            "files": [str(f) for f in files],
            "episodes": sorted({e for f in files for e in parse(f.name).episodes}),
            "mikan": mikan,
        })
    return sorted(out, key=lambda g: -len(g["files"]))


def _run_scan(source: str) -> None:
    """异步扫描,边走边更新 scan_state(供前端进度条轮询)。"""
    from app.clients.mikan import mikan_client
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
                groups.setdefault(_guess_series(p), []).append(p)
                scan_state["files_found"] += 1
                scan_state["current"] = p.name
        scan_state.update(phase="匹配蜜柑", total=len(groups), done=0, current="")
        out = []
        for title, files in groups.items():
            scan_state["current"] = title
            mikan = None
            try:
                hits = mikan_client.search(title)
                if hits:
                    mikan = {"mikan_bangumi_id": hits[0].mikan_bangumi_id, "title": hits[0].title}
            except Exception as e:  # noqa: BLE001
                log.warning("Mikan 匹配失败 %s: %s", title, e)
            out.append({
                "guess_title": title,
                "files": [str(f) for f in files],
                "episodes": sorted({e for f in files for e in parse(f.name).episodes}),
                "mikan": mikan,
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
    """若 /import-nas 挂载的 NAS 目录包含 mikanarr 下载目录,返回"经该挂载看到的 mikanarr 路径"
    (如 /import-nas/mikanarr),否则 None。用于让 NAS→NAS 导入落在同一挂载 → 服务器端 rename。"""
    host = (settings.import_nas_host or "").replace("\\", "/").rstrip("/")
    target = (settings.nas_smb_path or "").replace("\\", "/").rstrip("/")
    if host and target and target.startswith(host + "/"):
        return "/import-nas/" + target[len(host) + 1:].lstrip("/")
    return None


def _skip_roots() -> list[str]:
    """扫描时要跳过的目录:已被 mikanarr 管理的下载目录(/downloads 及经 NAS 挂载看到的同一目录),
    否则扫 /import-nas 会把里面的 mikanarr 子目录(已导入文件)当新文件重扫 → 重名冲突。"""
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
    mid = group["mikan"]["mikan_bangumi_id"] if group.get("mikan") else None
    if not mid:
        raise ValueError(f"{group['guess_title']}: 未匹配到 Mikan 番剧,跳过")

    with db_session() as db:
        bangumi = db.execute(select(Bangumi).where(
            Bangumi.mikan_bangumi_id == mid)).scalar_one_or_none()
        if bangumi is None:
            bangumi = Bangumi(mikan_bangumi_id=mid, title=group["guess_title"])
            db.add(bangumi)
            db.flush()
            enrich_bangumi(db, bangumi)

        sub = db.execute(select(Subscription).where(
            Subscription.bangumi_id == bangumi.id,
            Subscription.mikan_subgroup_id == LOCAL_SUBGROUP_ID)).scalar_one_or_none()
        safe = _ILLEGAL.sub(" ", bangumi.title).strip() or f"bangumi {mid}"
        if sub is None:
            sub = Subscription(bangumi_id=bangumi.id, mikan_subgroup_id=LOCAL_SUBGROUP_ID,
                               subgroup_name="本地导入", enabled=False, backfill=False,
                               save_path=f"{settings.download_root}/{safe}")
            db.add(sub)
            db.flush()

        guid = f"local:{mid}:{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        torrent = Torrent(subscription_id=sub.id, guid=guid,
                          title_raw=f"[本地导入] {bangumi.title}({len(group['files'])} 个文件)",
                          parsed_json={}, torrent_url="", is_batch=True,
                          status=TorrentStatus.ARCHIVED,
                          completed_at=datetime.now(timezone.utc))
        db.add(torrent)
        db.flush()

        # 目标目录:若源在 NAS 挂载下、且该挂载含 mikanarr 目标 → 走同挂载(_safe_move 的 os.replace
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
        for fp in group["files"]:
            src = Path(fp)
            rel = f"{safe}/{src.name}"
            if rel in seen_rel:
                continue   # 同组内多个源映射到同一目标名 → 去重,避免 UNIQUE 冲突
            seen_rel.add(rel)
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
            vf.source = p.source
            if len(p.episodes) == 1:
                n = p.episodes[0]
                ep = db.execute(select(Episode).where(
                    Episode.bangumi_id == bangumi.id, Episode.type == EpisodeType.EP,
                    Episode.number == n)).scalar_one_or_none()
                if ep is None:
                    ep = Episode(bangumi_id=bangumi.id, number=n, type=EpisodeType.EP)
                    db.add(ep)
                    db.flush()
                vf.episode_id = ep.id
                if not db.get(TorrentEpisode, (torrent.id, ep.id)):
                    db.add(TorrentEpisode(torrent_id=torrent.id, episode_id=ep.id))
            try:
                r = media_probe.probe(dest)
                vf.resolution = r.resolution
                vf.video_codec = r.video_codec
                vf.bitrate = r.bitrate
                vf.audio_tracks = r.audio_tracks
                vf.subtitle_tracks = r.subtitle_tracks
                vf.probed_at = datetime.now(timezone.utc)
            except Exception as e:  # noqa: BLE001
                log.warning("导入探测失败 %s: %s", dest, e)
            ok += 1
            db.flush()
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
