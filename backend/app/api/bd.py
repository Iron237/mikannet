"""BD 发行管理(ADR-0004):收藏总览 / 购买状态 / 绑番剧 / 扫描 / 特典文件串流。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Bangumi, BdExtra, BdRelease

router = APIRouter(prefix="/api/bd", tags=["bd"])

# 类别展示名(前端分组标题)
CATEGORY_LABELS = {
    "sp_anime": "特别动画", "short_drama": "短剧", "credits": "映像特典(NC)",
    "menu": "菜单", "pv": "PV / 预告", "audio": "音频", "gallery": "图集",
    "scans": "扫描 / 书子", "other": "其他",
}
_CATEGORY_ORDER = ["sp_anime", "short_drama", "credits", "pv", "menu",
                   "audio", "gallery", "scans", "other"]


def _owned_discs(r: BdRelease) -> list[dict]:
    """自购原盘:枚举含 BDMV 的碟子目录 → PowerDVD 蓝光播放 / 资源管理器定位目标(按碟)。"""
    from pathlib import Path

    from app.services import launch
    if r.source_kind != "raw_disc" or not (r.root_path or "").startswith("@owned/"):
        return []
    folder = r.root_path[len("@owned/"):]
    mount = Path(settings.bd_owned_mount) / folder
    if not mount.is_dir():
        return []
    out: list[dict] = []
    for d in sorted([x for x in mount.iterdir() if x.is_dir()], key=lambda x: x.name):
        if (d / "BDMV").is_dir():
            host = launch.owned_host_path(f"{folder}/{d.name}")
            out.append({"name": d.name, "bd_url": launch.launch_url("bd", host),
                        "reveal_url": launch.launch_url("reveal", host)})
    if not out and (mount / "BDMV").is_dir():   # 单碟直接在发行根
        host = launch.owned_host_path(folder)
        out.append({"name": r.title, "bd_url": launch.launch_url("bd", host),
                    "reveal_url": launch.launch_url("reveal", host)})
    return out


def bd_release_out(r: BdRelease) -> dict:
    """一套 BD 发行 → 展示结构(特典按类别分组;自购原盘附逐碟 PowerDVD 启动)。"""
    from app.services import launch
    by_cat: dict[str, list] = {}
    for e in r.extras:
        by_cat.setdefault(e.category, []).append({
            "id": e.id, "name": e.name, "media_kind": e.media_kind,
            "size": e.size, "resolution": e.resolution,
            "url": f"/api/bd/extra/{e.id}/raw",
            "play_url": launch.media_launch("play", e.relative_path) if e.media_kind == "video"
            else None,
            "reveal_url": launch.media_launch("reveal", e.relative_path),
        })
    groups = [{"category": c, "label": CATEGORY_LABELS.get(c, c), "items": by_cat[c]}
              for c in _CATEGORY_ORDER if c in by_cat]
    return {
        "id": r.id, "title": r.title, "source_kind": r.source_kind, "owned": r.owned,
        "disc_count": r.disc_count, "total_size": r.total_size,
        "bangumi_id": r.bangumi_id, "extra_count": len(r.extras), "groups": groups,
        "discs": _owned_discs(r),
    }


@router.get("/releases")
def list_releases(db: Session = Depends(get_db)):
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


@router.get("/extra/{extra_id}/raw")
def extra_raw(extra_id: int, db: Session = Depends(get_db)):
    """串流/下载一个特典文件(图片预览、音频播放、视频)。文件不动(ADR-0001)。"""
    e = db.get(BdExtra, extra_id)
    if not e:
        raise HTTPException(404)
    base = Path(settings.download_root_local).resolve()
    try:
        fp = (base / e.relative_path).resolve()
    except (OSError, ValueError):
        raise HTTPException(404) from None
    if base not in fp.parents or not fp.is_file():   # 防目录穿越 + 存在性
        raise HTTPException(404)
    return FileResponse(str(fp), filename=e.name)
