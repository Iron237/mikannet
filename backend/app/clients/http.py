"""httpx 客户端工厂:按服务决定是否走代理(PROBE-NOTES:外部服务必须走代理)。"""
import httpx

from app.config import settings

UA = {"User-Agent": "Mozilla/5.0 (compatible; Mikanarr/0.1)"}


def make_client(service: str, **kwargs) -> httpx.Client:
    """service ∈ {mikan, bgmtv, tmdb, telegram, ...};本地服务不要经过这里。"""
    return httpx.Client(
        proxy=settings.proxy_for(service),
        timeout=kwargs.pop("timeout", 30),
        follow_redirects=True,
        trust_env=False,          # 隔离系统代理环境变量,代理路由完全由配置决定
        headers={**UA, **kwargs.pop("headers", {})},
        **kwargs,
    )
