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
    proxy_services: set[str] = {"mikan", "bgmtv", "tmdb", "telegram", "nyaa", "dmhy", "anidb"}

    # Mikan(域名可配,镜像切换)
    mikan_base_url: str = "https://mikanani.me"
    # 蜜柑登录 cookie(批量导入「我的番组」全部历史订阅用;WebUI 粘贴,DB 存,打码)
    mikan_cookie: str = ""
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

    # 本地导入源:主机路径前缀 → 容器挂载点(把用户粘贴的 Win/NAS 路径翻译成容器内可见路径)
    import_win_host: str = ""     # = LOCAL_IMPORT_PATH(Windows 磁盘源,挂到 /import)
    import_nas_host: str = ""     # = NAS_IMPORT_PATH(NAS 源 UNC,挂到 /import-nas)
    # mikanarr 下载目录的 NAS 路径(= NAS_SMB_PATH);若它在 import_nas_host 之下,
    # NAS→NAS 导入可经 /import-nas 同挂载做服务器端 rename(零网络传输)
    nas_smb_path: str = ""

    # RSS 轮询间隔(分钟)
    poll_interval_min: int = 15

    # 元数据
    tmdb_api_key: str = ""

    # 文件整理(qB 原地重命名成 Jellyfin 结构)+ NFO/封面落盘(改 ADR-0001)
    organize_enabled: bool = True
    nfo_enabled: bool = True

    # 坏种清理:DOWNLOADING 且 0 做种 + 卡住无进度超过 N 小时 → 移除并换备选源
    dead_torrent_enabled: bool = True
    dead_torrent_hours: int = 6

    # AniDB 剧集级元数据(ADR-0003;默认关,需注册 client 名)
    anidb_enabled: bool = False
    # 官方 HTTP API 要求每个客户端注册一个名字+版本(https://anidb.net/software/add)
    anidb_client_name: str = ""
    anidb_client_ver: int = 1
    # 番剧→aid 第三方搜索(MIT,中文/拼音;base 可改为自托管)
    anidb_search_base: str = "https://anidb.rotcool.me"
    # 剧集名首选语言(zh-Hans→中文;判不出退romaji/英文)
    anidb_lang: str = "zh-Hans"
    # 可选 UDP 账号(仅歧义文件 ed2k 精配用;留空=永不哈希)
    anidb_udp_user: str = ""
    anidb_udp_pass: str = ""

    # LLM 兜底解析(OpenAI 兼容;仅低置信度时调用)
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mikanarr.db"

    def proxy_for(self, service: str) -> str | None:
        return self.proxy_url if service in self.proxy_services and self.proxy_url else None


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
