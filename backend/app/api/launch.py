"""原生启动:协议处理器、动态路径校验与 Windows 文件夹选择回调。"""
import re
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.services import launch

router = APIRouter(prefix="/api/launch", tags=["launch"])
_selections: dict[str, tuple[float, str]] = {}
_selection_lock = threading.Lock()
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{12,80}$")


@router.get("/status")
def status():
    """前端据此判断按钮是否可用(根未配置时隐藏播放/打开按钮并给提示)。"""
    return {
        "configured": launch.configured(),
        "media_host_root": settings.media_host_root,
        "bd_owned_host_root": settings.bd_owned_host_root,
        "powerdvd_path": settings.powerdvd_path,
    }


@router.get("/handler.bat")
def handler_installer(origin: str | None = None, quiet: bool = False):
    """下载自安装 .bat:写入本机协议处理器 + 注册 mikannet:// 协议 +(带 origin 时)写浏览器
    免询问策略。双击运行即可。origin 由前端传 window.location.origin,免每次播放弹窗。"""
    bat = launch.installer_bat(origin, quiet=quiet)
    return Response(
        content=bat.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="mikannet-handler-install.bat"'},
    )


@router.get("/validate")
def validate_path(token: str, path: str):
    """JScript 每次启动前向服务端校验当前白名单，路径配置变化后无需重装处理器。"""
    if token != launch.get_token() or not launch.path_allowed(path):
        raise HTTPException(403, "路径未获允许")
    return {"ok": True}


@router.get("/picker")
def picker(request_id: str):
    if not _REQUEST_ID.match(request_id):
        raise HTTPException(400, "request_id 非法")
    return {
        "url": (
            f"mikannet://pick?request_id={request_id}"
            f"&token={launch.get_token()}"
        )
    }


@router.post("/selection")
def selection_callback(token: str, request_id: str, path: str):
    """仅协议处理器持有 token；选择结果短暂保存在内存供同源设置页轮询。"""
    if token != launch.get_token() or not _REQUEST_ID.match(request_id):
        raise HTTPException(403, "无效回调")
    normalized = path.strip().strip('"')
    if not normalized:
        raise HTTPException(400, "路径为空")
    now = time.monotonic()
    with _selection_lock:
        for key, (created, _value) in list(_selections.items()):
            if now - created > 120:
                _selections.pop(key, None)
        _selections[request_id] = (now, normalized)
    return {"ok": True}


@router.get("/selection/{request_id}")
def get_selection(request_id: str):
    if not _REQUEST_ID.match(request_id):
        raise HTTPException(400, "request_id 非法")
    with _selection_lock:
        item = _selections.pop(request_id, None)
    return {"ready": bool(item), "path": item[1] if item else None}
