"""ORM 模型。术语见 CONTEXT.md;「文件不动+虚拟库」见 ADR-0001。

单文件承载全部模型:表之间外键/关系密集,拆包反而增加阅读跳转。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiringStatus(str, enum.Enum):
    AIRING = "airing"        # 连载中:默认排除合集
    FINISHED = "finished"    # 已完结:补番时合集为首选


class Kind(str, enum.Enum):
    """番剧整体形态(作品级),决定详情页布局。来源:AniDB anime type > bgm.tv platform > 手动。"""
    TV = "tv"          # 连载剧集:详情页渲染剧集网格
    MOVIE = "movie"    # 整部即剧场版电影(无 TV 集):影片本体 + 版本列表
    OVA = "ova"        # 整部为 OVA/OAD


class EpisodeType(str, enum.Enum):
    """剧集类型(番剧内单集的性质),对齐 AniDB 剧集类型。见 CONTEXT.md「剧集类型」。

    迁移:旧 EP→REGULAR、SP→SPECIAL;旧 OVA/MOVIE 是作品级,归 Kind(数据迁移在 database._migrate_columns)。
    """
    REGULAR = "regular"   # 正片
    SPECIAL = "special"   # 特别篇/特典/番外(总集篇 recap 归此)
    CREDITS = "credits"   # OP/ED,含无字幕 NCOP/NCED
    TRAILER = "trailer"   # PV/CM/预告
    OTHER = "other"       # 其他映像特典(parody/MAD 归此)


class TorrentStatus(str, enum.Enum):
    PENDING = "pending"                  # 已接受,待提交 qB
    DOWNLOADING = "downloading"
    SUBMIT_FAILED = "submit_failed"      # 提交 qB 失败(重试耗尽)
    DOWNLOAD_ERROR = "download_error"    # qB 侧错误,可手动重试
    COMPLETED = "completed"              # 下载完成,待后处理
    ARCHIVED = "archived"                # 后处理完成(ffprobe+映射)
    SKIPPED = "skipped"                  # 过滤/去重淘汰,留痕


class Bangumi(Base):
    """番剧:一季动画作品,展示与管理的核心对象。"""
    __tablename__ = "bangumi"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 可空:本地导入按 bgm.tv 匹配(蜜柑搜索只索引罗马音标题,中文/日文文件夹名搜不到),
    # 这类番剧无对应蜜柑 ID。唯一索引允许多个 NULL。
    mikan_bangumi_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    bgmtv_subject_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    anidb_aid: Mapped[int | None] = mapped_column(Integer)   # AniDB anime id(剧集级元数据,ADR-0003)
    anidb_synced_at: Mapped[datetime | None] = mapped_column(DateTime)  # 上次 AniDB 剧集同步(≥24h 缓存)

    kind: Mapped[Kind] = mapped_column(Enum(Kind), default=Kind.TV)   # 作品形态:tv/movie/ova

    title: Mapped[str] = mapped_column(String(255))            # 官方中文译名优先
    title_original: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer)
    season_str: Mapped[str | None] = mapped_column(String(32))  # 如 "2026春"
    studio: Mapped[str | None] = mapped_column(String(255))     # 制作公司
    summary: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    eps_total: Mapped[int | None] = mapped_column(Integer)
    airing_status: Mapped[AiringStatus] = mapped_column(Enum(AiringStatus),
                                                        default=AiringStatus.AIRING)
    poster_path: Mapped[str | None] = mapped_column(String(512))    # 本地缓存相对路径
    backdrop_path: Mapped[str | None] = mapped_column(String(512))
    air_weekday: Mapped[int | None] = mapped_column(Integer)         # 0=周一 … 6=周日(放送日历)
    season_number: Mapped[int] = mapped_column(Integer, default=1)    # 续作季号(Jellyfin Season N),可手改

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="bangumi")
    episodes: Mapped[list[Episode]] = relationship(back_populates="bangumi")


class Subscription(Base):
    """订阅:番剧 + 选定字幕组 + 过滤规则(CONTEXT.md)。"""
    __tablename__ = "subscription"
    __table_args__ = (UniqueConstraint("bangumi_id", "mikan_subgroup_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bangumi_id: Mapped[int] = mapped_column(ForeignKey("bangumi.id"))
    mikan_subgroup_id: Mapped[str] = mapped_column(String(32))
    subgroup_name: Mapped[str | None] = mapped_column(String(255))

    include_keywords: Mapped[list] = mapped_column(JSON, default=list)   # AND 语义
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list)   # 任一命中即排除
    # 手动勾选偏差(向导/编辑里对单个源的强制包含/排除,优先级高于关键词规则)
    pinned_guids: Mapped[list] = mapped_column(JSON, default=list)
    blocked_guids: Mapped[list] = mapped_column(JSON, default=list)
    # 集数偏移:Mikan 跨季连续编号(如二季 25-48)→ 减去偏移得本季集号;0=无偏移,自动检测可改
    episode_offset: Mapped[int] = mapped_column(Integer, default=0)
    # RSS 健康:上次轮询结果
    last_poll_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    last_poll_error: Mapped[str | None] = mapped_column(Text)
    exclude_batch: Mapped[bool] = mapped_column(Boolean, default=True)   # 默认按连载状态推导
    backfill: Mapped[bool] = mapped_column(Boolean, default=True)        # 补齐 / 只追新
    save_path: Mapped[str] = mapped_column(String(512))                  # qB 视角容器内路径
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bangumi: Mapped[Bangumi] = relationship(back_populates="subscriptions")
    torrents: Mapped[list[Torrent]] = relationship(back_populates="subscription")


class Episode(Base):
    """剧集:番剧的单集。一集可有多版本文件,库视图取 is_active 文件。"""
    __tablename__ = "episode"
    __table_args__ = (UniqueConstraint("bangumi_id", "type", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bangumi_id: Mapped[int] = mapped_column(ForeignKey("bangumi.id"))
    number: Mapped[float | None] = mapped_column(Float)   # 支持 12.5;None=未解析出
    type: Mapped[EpisodeType] = mapped_column(Enum(EpisodeType), default=EpisodeType.REGULAR)
    anidb_eid: Mapped[int | None] = mapped_column(Integer)   # AniDB episode id(同步后回填,ADR-0003)
    title: Mapped[str | None] = mapped_column(String(255))
    air_date: Mapped[str | None] = mapped_column(String(32))

    bangumi: Mapped[Bangumi] = relationship(back_populates="episodes")


class Torrent(Base):
    """种子任务:一行 = 一个提交给 qB 的种子(或被淘汰留痕的条目)。"""
    __tablename__ = "torrent"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscription.id"))
    guid: Mapped[str] = mapped_column(String(512), unique=True, index=True)  # Episode 页 URL
    info_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    title_raw: Mapped[str] = mapped_column(String(1024))
    parsed_json: Mapped[dict] = mapped_column(JSON, default=dict)   # ParsedTitle 缓存
    torrent_url: Mapped[str] = mapped_column(String(1024))
    is_batch: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)        # v2/v3
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    status: Mapped[TorrentStatus] = mapped_column(Enum(TorrentStatus), index=True,
                                                  default=TorrentStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text)
    # tracker 回写快照(重启后恢复展示用;实时值走 WS)
    size: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    dlspeed: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    stalled_since: Mapped[datetime | None] = mapped_column(DateTime)   # 坏种检测:开始无做种卡住的时刻

    subscription: Mapped[Subscription] = relationship(back_populates="torrents")
    episodes: Mapped[list[Episode]] = relationship(secondary="torrent_episode")
    files: Mapped[list[VideoFile]] = relationship(back_populates="torrent")


class TorrentEpisode(Base):
    """种子 ↔ 剧集 多对多(合集种子覆盖多集)。"""
    __tablename__ = "torrent_episode"

    torrent_id: Mapped[int] = mapped_column(ForeignKey("torrent.id"), primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episode.id"), primary_key=True)


class VideoFile(Base):
    """下载产物文件 + ffprobe 信息。文件永不移动(ADR-0001)。"""
    __tablename__ = "video_file"
    __table_args__ = (UniqueConstraint("torrent_id", "relative_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    torrent_id: Mapped[int] = mapped_column(ForeignKey("torrent.id"))
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episode.id"))  # None=待人工确认

    relative_path: Mapped[str] = mapped_column(String(1024))   # 相对 download_root
    size: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(String(32))
    subgroup: Mapped[str | None] = mapped_column(String(128))   # 字幕组(从文件名解析)
    source: Mapped[str | None] = mapped_column(String(32))      # 片源 Web/BD(从文件名解析)
    video_codec: Mapped[str | None] = mapped_column(String(32))
    color_depth: Mapped[str | None] = mapped_column(String(8))   # "8bit" / "10bit"(从 pix_fmt 推)
    hdr: Mapped[str | None] = mapped_column(String(16))          # "HDR10"/"HLG"/"DV";None=SDR
    bitrate: Mapped[int | None] = mapped_column(Integer)
    # 每条音轨/字幕轨 dict:{codec, lang, title, channels?};字幕含 sidecar(source="external")
    audio_tracks: Mapped[list] = mapped_column(JSON, default=list)
    subtitle_tracks: Mapped[list] = mapped_column(JSON, default=list)
    probed_at: Mapped[datetime | None] = mapped_column(DateTime)   # None=未探测/失败可重试
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # v2 切换翻转

    torrent: Mapped[Torrent] = relationship(back_populates="files")


class NotificationConfig(Base):
    """推送通道配置(P5)。"""
    __tablename__ = "notification_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), unique=True)  # telegram/serverchan/pushplus
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    credentials: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[dict] = mapped_column(JSON, default=dict)  # {on_new,on_start,on_complete,on_fail}
    use_proxy: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    """KV 设置(WebUI 可改的运行时配置,覆盖 env 默认)。"""
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)   # {"v": ...} 包装任意值
