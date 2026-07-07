"""下载器抽象:统一 qBittorrent / BitComet 两种后端,按 settings.downloader 切换。

调用方一律 `from app.clients.downloader import downloader`,拿到归一化的 DlTask/文件字典,
与具体后端解耦。qB 保留做后备,设置切换即生效(facade 每次按配置选后端)。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass
class DlTask:
    """归一化的下载任务快照(两后端统一字段)。"""
    hash: str            # 40-hex info_hash(关联 DB 的 info_hash)
    name: str
    progress: float      # 0..1
    dlspeed: int         # B/s
    size: int            # 字节
    state: str           # 后端原始状态串(展示用)
    eta: int | None = None
    upspeed: int = 0     # B/s
    seeds: int = 0       # 已连接做种数
    peers: int = 0       # 已连接下载者数
    done: bool = False   # 已完成(可进入后处理)
    error: bool = False  # 出错
    # 状态语义由各后端归一化(原始 state 串两后端拼写完全不同,调用方勿直接比对):
    paused: bool = False     # 用户/下载器主动暂停 → 不参与坏种/无进度判定(防误删丢数据)
    dl_active: bool = False  # 确认处于下载中类状态 → 才做坏种/无进度判定;也是错误任务自愈依据


class Downloader:
    """门面:把调用透传到当前启用的后端。"""

    @property
    def _backend(self):
        if settings.downloader == "bitcomet":
            from app.clients.bitcomet import bitcomet_client
            return bitcomet_client
        from app.clients.qbittorrent import qb_client
        return qb_client

    @property
    def name(self) -> str:
        return settings.downloader

    def ensure_ready(self) -> None:
        self._backend.ensure_ready()

    def healthy(self) -> dict:
        return self._backend.healthy()

    def add_torrent(self, torrent_bytes: bytes, save_path: str) -> str:
        return self._backend.add_torrent(torrent_bytes, save_path)

    def list_tasks(self) -> list[DlTask]:
        return self._backend.list_tasks()

    def files(self, info_hash: str) -> list[dict]:
        """种子内文件:[{name(相对 save_path 的路径), size}]。"""
        return self._backend.files(info_hash)

    def pause(self, info_hash: str) -> None:
        self._backend.pause(info_hash)

    def resume(self, info_hash: str) -> None:
        self._backend.resume(info_hash)

    def delete(self, info_hash: str, delete_files: bool) -> None:
        self._backend.delete(info_hash, delete_files)

    def set_global_dl_limit(self, bytes_per_sec: int) -> None:
        self._backend.set_global_dl_limit(bytes_per_sec)

    def rename_file(self, info_hash: str, old_path: str, new_path: str) -> None:
        """种子内文件原地重命名/移动(整理用,路径相对 save_path)。"""
        self._backend.rename_file(info_hash, old_path, new_path)


downloader = Downloader()
