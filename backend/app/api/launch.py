"""原生启动:协议处理器安装包下载 + 配置状态。"""
from fastapi import APIRouter
from fastapi.responses import Response

from app.config import settings
from app.services import launch

router = APIRouter(prefix="/api/launch", tags=["launch"])


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
def handler_installer(origin: str | None = None):
    """下载自安装 .bat:写入本机协议处理器 + 注册 mikannet:// 协议 +(带 origin 时)写浏览器
    免询问策略。双击运行即可。origin 由前端传 window.location.origin,免每次播放弹窗。"""
    bat = launch.installer_bat(origin)
    return Response(
        content=bat.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="mikannet-handler-install.bat"'},
    )
