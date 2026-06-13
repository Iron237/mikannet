"""应用配置:环境变量 / .env 两层。

路径约定(ADR 部署形态):所有持久化路径用容器内挂载点表达;
开发期直接跑 uvicorn 时通过 .env 覆盖为本机路径。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="MIKANARR_",
                                      env_file_encoding="utf-8")

    # 数据目录(SQLite、图片缓存、日志)
    data_dir: Path = BASE_DIR / "data"

    # 外部网络:本机所有外部服务必须走代理(见 PROBE-NOTES)
    proxy_url: str = "http://127.0.0.1:10808"
    # 走代理的服务名单;qB 等本地服务永远直连
    proxy_services: set[str] = {"mikan", "bgmtv", "tmdb", "telegram", "nyaa", "dmhy"}

    # Mikan(域名可配,镜像切换)
    mikan_base_url: str = "https://mikanani.me"
    # 多源搜索:nyaa / dmhy 站点(域名可配)
    nyaa_base_url: str = "https://nyaa.si"
    dmhy_base_url: str = "https://share.dmhy.org"

    # 下载器后端:qb | bitcomet(设置切换即生效,见 app.clients.downloader)
    downloader: str = "qb"

    # qBittorrent(开发:探针容器;生产:compose 内 http://qbittorrent:8080)
    qb_host: str = "localhost"
    qb_port: int = 18080
    qb_username: str = "admin"
    qb_password: str = "mikanarr-dev"
    qb_category: str = "mikanarr"

    # BitComet(容器化 WebUI;本地服务不走代理)
    bitcomet_host: str = "bitcomet"
    bitcomet_port: int = 18888
    bitcomet_username: str = "admin"
    bitcomet_password: str = "bcadmin"
    # BitComet 容器内的下载根(挂同一 NAS 卷,与 download_root 指向同一份文件)
    bitcomet_download_root: str = "/Downloads"
    # qB 视角的下载根目录(容器内路径);save_path 以此为前缀
    download_root: str = "/downloads"
    # 本应用视角的同一目录(开发期 Windows 路径;容器内与 download_root 相同)
    download_root_local: Path = Path("/downloads")

    # RSS 轮询间隔(分钟)
    poll_interval_min: int = 15

    # 元数据
    tmdb_api_key: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mikanarr.db"

    def proxy_for(self, service: str) -> str | None:
        return self.proxy_url if service in self.proxy_services and self.proxy_url else None


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
