"""通知器抽象:通道可插拔,按事件开关过滤,失败不影响下载链路。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)

EVENTS = ("on_new", "on_start", "on_complete", "on_fail")
EVENT_LABELS = {"on_new": "检测到更新", "on_start": "开始下载",
                "on_complete": "下载完成", "on_fail": "下载失败"}


@dataclass
class Notification:
    event: str                   # EVENTS 之一
    title: str                   # 番剧名
    message: str                 # 正文(剧集/错误信息等)
    poster_path: str | None = None   # 本地封面文件路径(可选,通道自行决定用不用)


class Notifier(ABC):
    channel: str = ""

    def __init__(self, credentials: dict, use_proxy: bool) -> None:
        self.credentials = credentials
        self.use_proxy = use_proxy

    @abstractmethod
    def send(self, n: Notification) -> None:
        """同步发送,失败抛异常(调用方捕获记日志)。"""


_REGISTRY: dict[str, type[Notifier]] = {}


def register(cls: type[Notifier]) -> type[Notifier]:
    _REGISTRY[cls.channel] = cls
    return cls


def create(channel: str, credentials: dict, use_proxy: bool) -> Notifier:
    if channel not in _REGISTRY:
        raise ValueError(f"未知通知通道: {channel}")
    return _REGISTRY[channel](credentials, use_proxy)


def known_channels() -> list[str]:
    return list(_REGISTRY)
