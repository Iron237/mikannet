"""qBittorrent 后端:固定 category,只触碰自己的任务。

info_hash 由 .torrent 字节本地计算(qB add 接口不返回 hash),见 app.clients.bencode。
对外返回归一化的 DlTask/文件字典(见 app.clients.downloader),与 BitComet 后端可互换。
"""
import logging
import threading

import qbittorrentapi

from app.clients.bencode import info_hash_of
from app.clients.downloader import DlTask
from app.config import settings

log = logging.getLogger(__name__)

__all__ = ["qb_client", "info_hash_of"]

_QB_ERROR_STATES = {"error", "missingFiles"}
_QB_DONE_STATES = {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP",
                   "forcedUP", "checkingUP"}


class QbClient:
    def __init__(self) -> None:
        self._client: qbittorrentapi.Client | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> qbittorrentapi.Client:
        with self._lock:
            if self._client is None:
                self._client = qbittorrentapi.Client(
                    host=settings.qb_host, port=settings.qb_port,
                    username=settings.qb_username, password=settings.qb_password,
                    REQUESTS_ARGS={"timeout": 15},
                )
                self._client.auth_log_in()
            return self._client

    def healthy(self) -> dict:
        c = self.client
        return {"backend": "qbittorrent", "version": c.app.version, "api": c.app.web_api_version}

    def ensure_ready(self) -> None:
        if settings.qb_category not in self.client.torrent_categories.categories:
            self.client.torrent_categories.create_category(name=settings.qb_category)

    def add_torrent(self, torrent_bytes: bytes, save_path: str) -> str:
        """添加种子,返回 info_hash。已存在同 hash 时 qB 幂等。"""
        ih = info_hash_of(torrent_bytes)
        self.client.torrents.add(torrent_files=torrent_bytes,
                                 category=settings.qb_category, save_path=save_path)
        return ih

    def list_tasks(self) -> list[DlTask]:
        out: list[DlTask] = []
        for t in self.client.torrents.info(category=settings.qb_category):
            out.append(DlTask(
                hash=t.hash, name=t.name, progress=float(t.progress),
                dlspeed=int(t.dlspeed), size=int(t.size), state=t.state,
                eta=getattr(t, "eta", None),
                done=t.progress >= 1.0 or t.state in _QB_DONE_STATES,
                error=t.state in _QB_ERROR_STATES))
        return out

    def files(self, info_hash: str) -> list[dict]:
        """种子内文件列表;name 为相对 download_root 的路径(与 BitComet 后端一致)。"""
        info = self.client.torrents.info(torrent_hashes=info_hash)
        save_path = info[0].save_path if info else settings.download_root
        root = settings.download_root.replace("\\", "/").rstrip("/")
        sp = (save_path or "").replace("\\", "/").rstrip("/")
        rel = sp[len(root):].lstrip("/") if sp.startswith(root) else ""
        prefix = rel + "/" if rel else ""
        return [{"name": prefix + f["name"], "size": f.get("size")}
                for f in self.client.torrents.files(torrent_hash=info_hash)]

    def pause(self, info_hash: str) -> None:
        self.client.torrents.pause(torrent_hashes=info_hash)

    def resume(self, info_hash: str) -> None:
        self.client.torrents.resume(torrent_hashes=info_hash)

    def delete(self, info_hash: str, delete_files: bool) -> None:
        self.client.torrents.delete(delete_files=delete_files, torrent_hashes=info_hash)

    def set_global_dl_limit(self, bytes_per_sec: int) -> None:
        """0 = 不限速。"""
        self.client.transfer.set_download_limit(bytes_per_sec)


qb_client = QbClient()
