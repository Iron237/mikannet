"""BD 发行管理(ADR-0004):收藏总览 / 购买状态 / 绑番剧 / 扫描 / 打开目录。

去特典分支:特典不在网页展示。正片(纯集号)替换 web 正片走剧集网格;特典(带描述标签的
视频 / 音频 / 图集)留在发行目录里,经「打开目录」(mikannet://reveal)用资源管理器 / 本机应用浏览。
自购原盘(raw_disc)仍可逐碟 PowerDVD 蓝光播放。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Bangumi, BdRelease

router = APIRouter(prefix="/api/bd", tags=["bd"])


def _owned_discs(r: BdRelease) -> list[dict]:
    """自购原盘:枚举含 BDMV 的碟子目录 → PowerDVD 蓝光播放 / 资源管理器定位目标(按碟)。"""
    from pathlib import Path

    from app.services import launch
    if r.source_kind != "raw_disc" or not (r.root_path or "").startswith("@owned/"):
        return []
    folder = r.root_path[len("@owned/"):]
    mount = Path(settings.bd_owned_mount) / folder
    out: list[dict] = []
    try:    # NAS/CIFS 挂载抖动会抛 OSError;序列化时不可拖垮 /api/bd/releases 与详情页
        if not mount.is_dir():
            return []
        for d in sorted([x for x in mount.iterdir() if x.is_dir()], key=lambda x: x.name):
            if (d / "BDMV").is_dir():
                host = launch.owned_host_path(f"{folder}/{d.name}")
                out.append({"name": d.name, "bd_url": launch.launch_url("bd", host),
                            "reveal_url": launch.launch_url("reveal", host)})
        if not out and (mount / "BDMV").is_dir():   # 单碟直接在发行根
            host = launch.owned_host_path(folder)
            out.append({"name": r.title, "bd_url": launch.launch_url("bd", host),
                        "reveal_url": launch.launch_url("reveal", host)})
    except OSError:
        return []
    return out


def _open_url(r: BdRelease) -> str | None:
    """该发行目录的「打开目录」URL(mikannet://reveal):在资源管理器里定位发行 / 原盘文件夹,
    特典就在其中,用本机应用浏览。未配置宿主机根 → None(前端按钮置灰提示)。"""
    from app.services import launch
    if r.source_kind == "raw_disc" and (r.root_path or "").startswith("@owned/"):
        return launch.launch_url("reveal", launch.owned_host_path(r.root_path[len("@owned/"):]))
    return launch.media_launch("reveal", r.root_path)


def bd_release_out(r: BdRelease) -> dict:
    """一套 BD 发行 → 展示结构(无特典编目):绑定 / 类型 / 购买 / 大小 + 打开目录 URL。

    自购原盘逐碟 PowerDVD 列表按发行懒加载(GET /releases/{id},含 FS 枚举),不在此返。"""
    return {
        "id": r.id, "title": r.title, "source_kind": r.source_kind, "owned": r.owned,
        "disc_count": r.disc_count, "total_size": r.total_size, "bangumi_id": r.bangumi_id,
        "has_discs": r.source_kind == "raw_disc", "open_url": _open_url(r),
        "manual_import": r.manual_import,
    }


@router.get("/releases")
def list_releases(db: Session = Depends(get_db)):
    """发行列表:发行实体 + 打开目录 URL(特典不编目、不在网页展示)。"""
    rows = db.execute(select(BdRelease)).scalars().all()
    out = []
    for r in rows:
        d = bd_release_out(r)
        b = db.get(Bangumi, r.bangumi_id) if r.bangumi_id else None
        d["bangumi_title"] = b.title if b else None
        d["poster"] = f"/data/{b.poster_path}" if b and b.poster_path else None
        out.append(d)
    out.sort(key=lambda x: (x["bangumi_title"] is None, x["bangumi_title"] or x["title"]))
    return out


@router.get("/releases/{release_id}")
def release_detail(release_id: int, db: Session = Depends(get_db)):
    """自购原盘逐碟 PowerDVD 列表(FS 枚举);前端展开某套原盘发行时才拉。"""
    r = db.get(BdRelease, release_id)
    if not r:
        raise HTTPException(404)
    return {"id": r.id, "discs": _owned_discs(r)}


def _require_bdrip_bound(db: Session, release_id: int) -> BdRelease:
    r = db.get(BdRelease, release_id)
    if not r:
        raise HTTPException(404)
    if r.source_kind != "bdrip":
        raise HTTPException(400, "仅 BDRip 发行支持正片导入")
    if not r.bangumi_id:
        raise HTTPException(400, "请先绑定番剧再导入正片")
    return r


@router.get("/releases/{release_id}/candidates")
def import_candidates(release_id: int, db: Session = Depends(get_db)):
    """正片导入向导:列**整个番剧目录**内全部视频(含 Season 等子文件夹)+ 自动猜测 + 当前登记。

    扫整个番剧目录而非仅发行文件夹:整理器(organize)会把 BD 剧集改名移进 `番剧名/Season xx/`,
    与发行文件夹平级 → 只扫发行文件夹会漏掉剧集。返回 guess(按文件名序号)作预填,用户可改集号 /
    标为特典(不导入)。folder = 文件相对番剧目录的所在子目录,供前端区分(Season / 特典 / 散落)。
    """
    from pathlib import Path, PurePosixPath

    from app.parsers.title_parser import parse
    from app.models import Episode, EpisodeType, Subscription, Torrent, VideoFile
    from app.services import media_probe
    from app.services.bd_scan import bd_is_extra_video, is_extra_dir

    r = _require_bdrip_bound(db, release_id)
    b = db.get(Bangumi, r.bangumi_id)
    root = Path(settings.download_root_local)
    bangumi_dir = root / PurePosixPath(r.root_path).parts[0]   # 番剧目录(发行 root_path 首段)
    try:
        vids = sorted([p for p in bangumi_dir.rglob("*")
                       if p.is_file() and media_probe.is_video(p)],
                      key=lambda p: str(p.relative_to(bangumi_dir)))   # 按相对路径排 → 同目录聚拢
    except OSError:
        vids = []
    # 该番全部现有登记(显示「当前第几集」+ 预选;不再限定发行文件夹内)
    cur = {vf.relative_path: vf for vf in db.execute(
        select(VideoFile).join(Torrent).join(Subscription).where(
            Subscription.bangumi_id == b.id)).scalars()}
    files = []
    for p in vids:
        rel = str(p.relative_to(root)).replace("\\", "/")
        sub = p.relative_to(bangumi_dir)
        folder = "" if str(sub.parent) == "." else str(sub.parent).replace("\\", "/")
        parsed = parse(p.name)
        guess = parsed.episodes[0] if len(parsed.episodes) == 1 and parsed.episodes[0] > 0 else None
        in_extra_dir = any(is_extra_dir(seg) for seg in sub.parts[:-1])
        vf = cur.get(rel)
        cur_num = None
        if vf and vf.episode_id:
            ep = db.get(Episode, vf.episode_id)
            cur_num = ep.number if ep else None
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        files.append({
            "path": rel, "name": p.name, "folder": folder, "size": size,
            "guess_number": guess,
            "guess_extra": bd_is_extra_video(p.name) or in_extra_dir,
            "current_number": cur_num, "registered": vf is not None,
        })
    eps = db.execute(select(Episode).where(
        Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR)).scalars().all()
    return {
        "release": {"id": r.id, "title": r.title, "manual_import": r.manual_import},
        "bangumi": {"id": b.id, "title": b.title, "eps_total": b.eps_total,
                    "episodes": sorted(e.number for e in eps if e.number is not None)},
        "files": files,
    }


@router.post("/releases/{release_id}/import")
def import_main(release_id: int, payload: dict, db: Session = Depends(get_db)):
    """正片导入(向导,对该发行权威):assignments=[{path, episode_number}] 把选中的 BD 文件
    登记/重映射为该番正片(source=BD,version-switch 替换 web);该发行目录下未被选中的已登记
    BD 文件移出剧集网格(删登记,不动磁盘)。导入后标 manual_import,库扫描不再自动改这套发行。
    """
    from pathlib import Path, PurePosixPath

    from app.parsers.title_parser import parse
    from app.models import Episode, EpisodeType, Subscription, Torrent, TorrentEpisode, VideoFile
    from app.services.library_scan import _container_torrent, _probe_into
    from app.services.postprocess import _apply_version_switch

    r = _require_bdrip_bound(db, release_id)
    b = db.get(Bangumi, r.bangumi_id)
    root = Path(settings.download_root_local)

    want: dict[str, float] = {}   # rel_path -> 集号
    for a in (payload.get("assignments") or []):
        path, num = a.get("path"), a.get("episode_number")
        if not path or num in (None, ""):
            continue
        try:
            want[path] = float(num)
        except (TypeError, ValueError):
            raise HTTPException(400, f"集号非法:{num}") from None

    t = _container_torrent(db, b)
    rel_prefix = r.root_path
    # 该番全部现有登记:选中文件可能在 Season 等任意子目录 → 全量查,避免重复建。
    cur = {vf.relative_path: vf for vf in db.execute(
        select(VideoFile).join(Torrent).join(Subscription).where(
            Subscription.bangumi_id == b.id)).scalars()}

    touched: set[int] = set()
    imported = removed = 0
    for path, number in want.items():
        ep = db.execute(select(Episode).where(
            Episode.bangumi_id == b.id, Episode.type == EpisodeType.REGULAR,
            Episode.number == number)).scalars().first()
        if ep is None:
            ep = Episode(bangumi_id=b.id, number=number, type=EpisodeType.REGULAR)
            db.add(ep)
            db.flush()
        vf = cur.get(path)
        if vf is None:
            abspath = root / path
            vf = VideoFile(torrent_id=t.id, relative_path=path, source="BD",
                           subgroup=parse(PurePosixPath(path).name).group)
            try:
                vf.size = abspath.stat().st_size
            except OSError:
                pass
            db.add(vf)
            db.flush()
            _probe_into(vf, abspath, parse(PurePosixPath(path).name).resolution)
            imported += 1
        elif vf.source != "BD":
            vf.source = "BD"
        old_ep = vf.episode_id
        vf.episode_id = ep.id
        if not db.get(TorrentEpisode, (vf.torrent_id, ep.id)):
            db.add(TorrentEpisode(torrent_id=vf.torrent_id, episode_id=ep.id))
        touched.add(ep.id)
        if old_ep and old_ep != ep.id:
            touched.add(old_ep)
    # 权威移除只作用于 BD 发行文件夹内、未选中的登记(不碰 Season / web 里没选的)
    for path, vf in cur.items():
        if path in want:
            continue
        if not (path == rel_prefix or path.startswith(rel_prefix + "/")):
            continue
        if vf.episode_id:
            touched.add(vf.episode_id)
        db.delete(vf)
        removed += 1

    db.flush()
    for ep_id in touched:
        _apply_version_switch(db, ep_id)
    r.manual_import = True
    db.flush()
    try:   # 可能令该番全 BD 完成 → 收尾(停扫/停订阅/删冗余 web)
        from app.services.lifecycle import on_torrent_processed
        on_torrent_processed(db, b.id)
    except Exception:  # noqa: BLE001 — 生命周期失败不影响导入结果
        pass
    db.commit()
    return {"ok": True, "imported": imported, "remapped": len(want), "removed": removed}


@router.post("/scan")
def scan():
    from app.services import bd_scan
    if not bd_scan.start():
        raise HTTPException(409, "已有 BD 扫描在进行中")
    return {"started": True}


@router.get("/scan/status")
def scan_status():
    from app.services import bd_scan
    return bd_scan.state


@router.patch("/releases/{release_id}")
def update_release(release_id: int, payload: dict, db: Session = Depends(get_db)):
    """改购买状态 / 绑定番剧。owned + 已绑番剧 时,顺带把番剧设为 bd_owned(排除自动下载)。"""
    r = db.get(BdRelease, release_id)
    if not r:
        raise HTTPException(404)
    old_bid = r.bangumi_id
    if "bangumi_id" in payload:
        bid = payload["bangumi_id"]
        if bid is not None and not db.get(Bangumi, int(bid)):
            raise HTTPException(400, "番剧不存在")
        r.bangumi_id = int(bid) if bid is not None else None
    if "owned" in payload:
        r.owned = bool(payload["owned"])
    db.flush()
    # 重算受影响番剧的 bd_owned(新绑 + 旧绑/解绑都要):有 owned 发行 → 排除自动下载,否则解除
    for bid in {old_bid, r.bangumi_id} - {None}:
        b = db.get(Bangumi, bid)
        if b:
            b.bd_owned = any(x.owned for x in db.execute(select(BdRelease).where(
                BdRelease.bangumi_id == bid)).scalars())
    db.commit()
    return {"ok": True, "owned": r.owned, "bangumi_id": r.bangumi_id}


@router.delete("/releases/{release_id}", status_code=204)
def delete_release(release_id: int, db: Session = Depends(get_db)):
    """从库里移除该 BD 发行记录(不动磁盘文件)。"""
    r = db.get(BdRelease, release_id)
    if not r:
        raise HTTPException(404)
    db.delete(r)
    db.commit()
