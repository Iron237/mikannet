"""Pydantic 请求/响应模型(P1 最小集)。"""
from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionCreate(BaseModel):
    mikan_bangumi_id: int
    mikan_subgroup_id: str
    bangumi_title: str | None = Field(default=None, description="可省略,自动从 Mikan/bgm.tv 获取")
    subgroup_name: str | None = None
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    pinned_guids: list[str] = []        # 手动勾选:强制下载(优先于规则)
    blocked_guids: list[str] = []       # 手动勾选:强制排除
    exclude_batch: bool | None = None   # None → 按连载状态推导(P1 默认 True)
    backfill: bool = True
    save_path: str | None = None        # None → {download_root}/{番剧名}/


class SubscriptionOut(BaseModel):
    id: int
    bangumi_id: int
    mikan_bangumi_id: int | None = None   # 本地导入番剧无蜜柑 ID
    bangumi_title: str
    mikan_subgroup_id: str
    subgroup_name: str | None
    include_keywords: list
    exclude_keywords: list
    pinned_guids: list = []
    blocked_guids: list = []
    bangumi_eps_total: int | None = None
    episode_offset: int = 0
    last_poll_ok: bool = True
    last_poll_error: str | None = None
    exclude_batch: bool
    backfill: bool
    save_path: str
    enabled: bool
    last_checked_at: datetime | None

    model_config = {"from_attributes": True}


class TorrentOut(BaseModel):
    id: int
    subscription_id: int
    title_raw: str
    status: str
    is_batch: bool
    version: int
    episodes: list[float] = []
    size: int | None
    progress: float
    dlspeed: int
    upspeed: int = 0
    seeds: int = 0
    peers: int = 0
    eta: int | None = None
    bangumi_title: str | None = None
    season_number: int = 1
    error_message: str | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
