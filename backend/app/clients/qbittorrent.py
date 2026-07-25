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
# 用户/qB 主动暂停:不能按「坏种/无进度」处理(否则人工暂停的种子会被自动删除丢数据)
_QB_PAUSED_STATES = {"pausedDL", "stoppedDL", "pausedUP", "stoppedUP"}
# 下载中类状态:坏种/无进度判定只对这些做;也是错误任务自愈恢复的依据
_QB_DL_ACTIVE_STATES = {"downloading", "stalledDL", "metaDL", "forcedDL", "forcedMetaDL",
                        "queuedDL", "checkingDL", "allocating"}
_LEGACY_QB_CATEGORY = "mikanarr"   # 改名前的分类名;首启时把旧种子迁到新分类


class QbClient:
    def __init__(self) -> None:
        self._client: qbittorrentapi.Client | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> qbittorrentapi.Client:
        with self._lock:
            if self._client is None:
                c = qbittorrentapi.Client(
                    host=settings.qb_host, port=settings.qb_port,
                    username=settings.qb_username, password=settings.qb_password,
                    REQUESTS_ARGS={"timeout": 15},
                )
                c.auth_log_in()          # 登录成功后才提升为单例;失败不留下未认证的坏客户端
                self._client = c
            return self._client

    def healthy(self) -> dict:
        c = self.client
        return {"backend": "qbittorrent", "version": c.app.version, "api": c.app.web_api_version}

    def ensure_ready(self) -> None:
        if settings.qb_category not in self.client.torrent_categories.categories:
            self.client.torrent_categories.create_category(name=settings.qb_category)
        self._migrate_legacy_category()

    def _migrate_legacy_category(self) -> None:
        """项目改名 mikanarr→mikannet:把旧分类下的所有种子重新归到新分类,再删空的旧分类。
        tracker 只按当前 category 列任务,不迁的话已下载的种子会全部"消失"。幂等:旧分类
        不存在即跳过;整块 try 兜底,迁移失败不拖垮启动。"""
        legacy = _LEGACY_QB_CATEGORY
        if legacy == settings.qb_category:
            return
        try:
            if legacy not in self.client.torrent_categories.categories:
                return
            old = self.client.torrents.info(category=legacy)
            if old:
                self.client.torrents.set_category(
                    category=settings.qb_category, torrent_hashes=[t.hash for t in old])
                log.info("qB 分类迁移:%d 个种子 %s → %s",
                         len(old), legacy, settings.qb_category)
            self.client.torrent_categories.remove_categories(categories=legacy)
        except Exception as e:  # noqa: BLE001
            log.warning("qB 旧分类迁移失败(跳过): %s", e)

    def add_torrent(self, torrent_bytes: bytes, save_path: str) -> str:
        """添加种子,返回 info_hash。已存在同 hash 时按幂等处理。

        qB 5.x 对重复 add 返回 409 Conflict(而非旧版的静默 "Ok.")→ 捕获后视为已添加,
        否则手动加过/补番重复会卡在 SUBMIT_FAILED,重试也一直失败。
        """
        ih = info_hash_of(torrent_bytes)
        try:
            self.client.torrents.add(torrent_files=torrent_bytes,
                                     category=settings.qb_category, save_path=save_path)
        except qbittorrentapi.exceptions.Conflict409Error:
            log.info("种子已存在于 qB(409 Conflict),按幂等成功处理 %s", ih[:12])
        return ih

    def list_tasks(self) -> list[DlTask]:
        out: list[DlTask] = []
        for t in self.client.torrents.info(category=settings.qb_category):
            out.append(DlTask(
                hash=t.hash, name=t.name, progress=float(t.progress),
                dlspeed=int(t.dlspeed), size=int(t.size), state=t.state,
                eta=getattr(t, "eta", None),
                upspeed=int(getattr(t, "upspeed", 0) or 0),
                seeds=int(getattr(t, "num_seeds", 0) or 0),
                peers=int(getattr(t, "num_leechs", 0) or 0),
                done=t.progress >= 1.0 or t.state in _QB_DONE_STATES,
                error=t.state in _QB_ERROR_STATES,
                paused=t.state in _QB_PAUSED_STATES,
                dl_active=t.state in _QB_DL_ACTIVE_STATES))
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

    def rename_file(self, info_hash: str, old_path: str, new_path: str) -> None:
        """原地重命名/移动种子内文件(整理成 Jellyfin 结构;qB 仍按新路径做种)。"""
        self.client.torrents.rename_file(torrent_hash=info_hash,
                                         old_path=old_path, new_path=new_path)


qb_client = QbClient()
