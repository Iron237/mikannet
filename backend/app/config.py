"""应用配置:环境变量 / .env 两层。

路径约定(ADR 部署形态):所有持久化路径用容器内挂载点表达;
开发期直接跑 uvicorn 时通过 .env 覆盖为本机路径。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="MIKANNET_",
                                      env_file_encoding="utf-8")

    # 数据目录(SQLite、图片缓存、日志)
    data_dir: Path = BASE_DIR / "data"
    # 自更新:可写代码卷根(wrapper 从 <code>/current 跑应用;updater 往这里落新版本)
    code_dir: Path = Path("/code")
    # 自更新发布源仓库(GitHub Releases API,公开免认证)
    update_repo: str = "Iron237/mikannet"
    # 通道:包含预发布(当前仅发 pre-release → 默认开;切正式稳定后改默认)
    update_channel_prerelease: bool = True
    # 完整更新(换镜像):经 docker socket + 一次性 helper 跑 `docker compose up -d` 重建本容器
    compose_project: str = "mikannet"        # compose 项目名(helper -p 用)
    compose_host_dir: str = ""               # 宿主上 compose 目录(helper 绑定挂载源;compose 注入 ${PWD})

    # 外部网络:本机所有外部服务必须走代理(见 PROBE-NOTES)
    proxy_url: str = "http://127.0.0.1:10808"
    # 走代理的服务名单;qB 等本地服务永远直连
    proxy_services: set[str] = {"mikan", "bgmtv", "tmdb", "telegram", "nyaa", "dmhy", "anidb",
                                "github"}

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
    qb_password: str = "mikannet-dev"
    qb_category: str = "mikannet"

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

    # 存储(首次向导配置;App 在容器内把 NAS/SMB 自挂载到 download_root_local)
    # storage_mode: "" = compose 托管(旧/dev,App 不挂);"smb" = App 用 mount() 挂载;"local" = 容器内本地路径
    storage_mode: str = ""
    smb_host_path: str = ""       # //ip/share
    smb_username: str = ""
    smb_password: str = ""
    smb_vers: str = "3.0"
    setup_done: bool = False      # 首次配置向导是否完成

    # 本地导入源:主机路径前缀 → 容器挂载点(把用户粘贴的 Win/NAS 路径翻译成容器内可见路径)
    import_win_host: str = ""     # = LOCAL_IMPORT_PATH(Windows 磁盘源,挂到 /import)
    import_nas_host: str = ""     # = NAS_IMPORT_PATH(NAS 源 UNC,挂到 /import-nas)
    # mikannet 下载目录的 NAS 路径(= NAS_SMB_PATH);若它在 import_nas_host 之下,
    # NAS→NAS 导入可经 /import-nas 同挂载做服务器端 rename(零网络传输)
    nas_smb_path: str = ""

    # RSS 轮询间隔(分钟)
    poll_interval_min: int = 15

    # 智能下载偏好(番剧库批量补全 / 定期扫所有字幕组挑最佳源)
    auto_dl_resolution: str = "1080p"   # 严格:只下该分辨率
    auto_dl_sub_lang: str = "简中"       # 严格:必须含该字幕语言(简中=含简体中文)
    auto_dl_prefer_bd: bool = True       # 片源优先级 BD>Web;据此把已有 Web 升级为 BD
    auto_dl_interval_min: int = 360      # 定期智能扫描间隔(分钟);0=关闭定期,仅手动

    # 元数据
    tmdb_api_key: str = ""

    # 文件整理(qB 原地重命名成 Jellyfin 结构)+ NFO/封面落盘(改 ADR-0001)
    organize_enabled: bool = True
    nfo_enabled: bool = True

    # 坏种清理:DOWNLOADING 且 0 做种 + 卡住无进度超过 N 小时 → 移除并换备选源
    dead_torrent_enabled: bool = True
    dead_torrent_hours: int = 6
    # 无进度暂停(温和档,与坏种清理并存):进度长期不增长超过 N 小时 → 暂停(不删,可手动恢复)
    stall_pause_enabled: bool = True
    stall_pause_hours: int = 12

    # 已购原盘目录(BD 收藏:MAKEMKV 原盘等;独立 CIFS 挂载到容器内 /bd-owned,缺失则跳过)
    bd_owned_mount: str = "/bd-owned"

    # 原生启动(自定义协议头 mikannet://;详见 docs/系统运行与判别逻辑.md)
    # 容器只存相对 download_root 的路径,这里配「你电脑上看到的根」用于映射成宿主机真实路径
    media_host_root: str = ""        # 番剧库根(如 Z:\番剧\mikannet)
    bd_owned_host_root: str = ""     # 已购原盘根(如 Z:\BD\已购BD翻录)
    data_host_root: str = ""         # data 目录根(如 C:\mikannet\data\mikannet);用于「打开 log 目录」
    powerdvd_path: str = ""          # PowerDVD.exe 路径(留空 → 处理器自动探常见安装位)
    launch_token: str = ""           # 协议头防滥用令牌(首次需要时自动生成并存 DB)

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
        return self.data_dir / "mikannet.db"

    def proxy_for(self, service: str) -> str | None:
        return self.proxy_url if service in self.proxy_services and self.proxy_url else None


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
