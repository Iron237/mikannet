"""BitComet 后端:容器化 BitComet 的 WebUI JSON API(v2.13)。

鉴权握手(已逆向实测,见 memory/bitcomet-webui-api.md):
  login(AES 加密 {user,pass}) → invite_token → device_token(32 字符,Bearer)。
对外暴露与 qB 后端相同的归一化接口(add_torrent / list_tasks / files / pause / resume / delete)。
BitComet 任务用 task_id 标识,但 task_guid == "bt_"+info_hash,据此把 info_hash 映射到 task_id。
本地服务,不走代理。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import uuid

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.clients.bencode import info_hash_of
from app.clients.downloader import DlTask
from app.config import settings

log = logging.getLogger(__name__)

_HEADERS = {"Client-Type": "BitComet WebUI", "Content-Type": "application/json"}


def _aes_encrypt(plaintext: str, password: str) -> str:
    """复刻 webui yu.AES_Encrypt:\\x03\\x01 + 盐(8)+盐(8)+IV(16)+AES256-CBC密文 + HMAC-SHA256(32),整体 base64。"""
    pt, pw = plaintext.encode(), password.encode()
    salt_enc, salt_mac, iv = os.urandom(8), os.urandom(8), os.urandom(16)
    key_enc = hashlib.pbkdf2_hmac("sha1", pw, salt_enc, 10000, 32)
    key_mac = hashlib.pbkdf2_hmac("sha1", pw, salt_mac, 10000, 32)
    pad = 16 - len(pt) % 16
    enc = Cipher(algorithms.AES(key_enc), modes.CBC(iv)).encryptor()
    ct = enc.update(pt + bytes([pad]) * pad) + enc.finalize()
    head = bytes([3, 1]) + salt_enc + salt_mac + iv + ct
    return base64.b64encode(head + hmac.new(key_mac, head, hashlib.sha256).digest()).decode()


class BitCometClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._client_id = str(uuid.uuid4())

    # ---- 连接 / 鉴权 -------------------------------------------------------
    @property
    def _http(self) -> httpx.Client:
        base = f"http://{settings.bitcomet_host}:{settings.bitcomet_port}"
        return httpx.Client(base_url=base, trust_env=False, timeout=20, headers=_HEADERS)

    def _login(self) -> str:
        user, pw = settings.bitcomet_username, settings.bitcomet_password
        auth = _aes_encrypt(json.dumps({"username": user, "password": pw}, separators=(",", ":")),
                            self._client_id)
        with self._http as c:
            r = c.post("/api/webui/login", json={"client_id": self._client_id, "authentication": auth})
            r.raise_for_status()
            invite = r.json().get("invite_token")
            if not invite:
                raise RuntimeError(f"BitComet 登录失败: {r.text}")
            r = c.post("/api/device_token/get",
                       json={"invite_token": invite, "device_id": self._client_id,
                             "device_name": "mikannet", "platform": "webui"},
                       headers={"Authorization": "Bearer " + invite})
            r.raise_for_status()
            token = r.json().get("device_token")
            if not token:
                raise RuntimeError(f"BitComet 取 device_token 失败: {r.text}")
        return token

    def _ensure_token(self) -> str:
        with self._lock:
            if self._token is None:
                self._token = self._login()
            return self._token

    def _post(self, path: str, body: dict) -> dict:
        """带 Bearer 的 POST;遇 INVALID_TOKEN/401 自动重登一次。"""
        for attempt in range(2):
            token = self._ensure_token()
            with self._http as c:
                r = c.post(path, json=body, headers={"Authorization": "Bearer " + token})
            try:   # 重启中/反代可能回 content-type=json 但 body 是 HTML → json() 抛错
                jb = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            except ValueError:
                jb = {}
            if r.status_code == 401 or jb.get("error_code") == "INVALID_TOKEN":
                with self._lock:
                    self._token = None
                if attempt == 0:
                    continue
                raise RuntimeError(f"BitComet 鉴权失败: {path}")
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"BitComet 请求失败: {path}")

    # ---- 归一化接口 -------------------------------------------------------
    def healthy(self) -> dict:
        self._ensure_token()
        r = self._post("/api/config/new_task/get", {})
        return {"backend": "bitcomet", "version": r.get("version"),
                "save_folders": [f.get("path") for f in r.get("save_folders", [])]}

    def ensure_ready(self) -> None:
        self._ensure_token()

    def add_torrent(self, torrent_bytes: bytes, save_path: str) -> str:
        # BitComet 只接受其配置的下载根作为 save_folder(子目录会被拒「save_folder invalid」),
        # 故统一存到根目录,由 BitComet 按种子名自动建内容子目录(ADR-0001:物理布局不重要)。
        ih = info_hash_of(torrent_bytes)
        b64 = base64.b64encode(torrent_bytes).decode()
        r = self._post("/api/task/bt/add",
                       {"torrent_url": "", "torrent_file": b64,
                        "save_folder": settings.bitcomet_download_root.rstrip("/")})
        ec = str(r.get("error_code", "")).lower()
        if ec not in ("ok", "") and "exist" not in str(r.get("error_message", "")).lower():
            raise RuntimeError(f"BitComet 加种失败: {r.get('error_message') or r}")
        return ih

    def list_tasks(self) -> list[DlTask]:
        r = self._post("/api_v2/task_list/get",
                       {"group_state": "ALL", "sort_key": "", "sort_order": "unsorted"})
        out: list[DlTask] = []
        for t in r.get("tasks", []):
            if t.get("type") != "BT":
                continue
            guid = t.get("task_guid", "")
            if not guid.startswith("bt_"):
                continue
            status = str(t.get("status", "")).lower()
            permil = int(t.get("permillage", 0) or 0)
            done = permil >= 1000 or "seed" in status or "finish" in status
            error = "error" in status or bool(t.get("error_code"))
            out.append(DlTask(
                hash=guid[3:], name=t.get("task_name", ""),
                progress=permil / 1000.0, dlspeed=int(t.get("download_rate", 0) or 0),
                size=int(t.get("total_size", 0) or 0), state=t.get("status", ""),
                upspeed=int(t.get("upload_rate", 0) or 0),
                done=done, error=error,
                # BitComet 状态串与 qB 完全不同拼写,按关键词归一化(action 是 stop/start,
                # 停止态含 stop/pause)。识别不出的状态两者皆 False → tracker 保守跳过
                # 坏种/无进度判定(宁可不自动清理,绝不误删手动暂停的任务)。
                paused="stop" in status or "pause" in status,
                dl_active=(not done and not error
                           and ("download" in status or "running" in status
                                or "queue" in status or "connect" in status))))
        return out

    def _task_id_for(self, info_hash: str) -> str | None:
        # summary/files/action 接口要求 task_id 为字符串(WebUI 调用前会 .toString())
        r = self._post("/api_v2/task_list/get",
                       {"group_state": "ALL", "sort_key": "", "sort_order": "unsorted"})
        for t in r.get("tasks", []):
            if t.get("task_guid") == "bt_" + info_hash:
                return str(t.get("task_id"))
        return None

    def files(self, info_hash: str) -> list[dict]:
        """返回相对 download_root 的文件路径(与 qB 后端一致,供后处理直接用)。"""
        tid = self._task_id_for(info_hash)
        if tid is None:
            return []
        summ = self._post("/api/task/summary/get", {"task_id": tid}).get("task_detail", {})
        save_folder = (summ.get("save_folder") or "").replace("\\", "/").rstrip("/")
        # save_folder 是该任务实际存放目录(= 下载根[/内容子目录]);相对下载根的部分即文件名前缀
        broot = settings.bitcomet_download_root.replace("\\", "/").rstrip("/")
        rel = save_folder[len(broot):].lstrip("/") if save_folder.startswith(broot) else ""
        prefix = rel + "/" if rel else ""
        r = self._post("/api/task/files/get", {"task_id": tid})
        return [{"name": prefix + f.get("name", ""), "size": f.get("size")}
                for f in r.get("files", [])]

    def _action(self, info_hash: str, action: str, delete: bool = False) -> None:
        tid = self._task_id_for(info_hash)
        if tid is None:
            return
        path = "/api_v2/tasks/delete" if delete else "/api_v2/tasks/action"
        self._post(path, {"task_ids": [tid], "action": action})

    def pause(self, info_hash: str) -> None:
        self._action(info_hash, "stop")

    def resume(self, info_hash: str) -> None:
        self._action(info_hash, "start")

    def delete(self, info_hash: str, delete_files: bool) -> None:
        self._action(info_hash, "delete_all" if delete_files else "delete_task", delete=True)

    def set_global_dl_limit(self, bytes_per_sec: int) -> None:
        self._post("/api/config/connection/set",
                   {"connection_config.max_download_speed": bytes_per_sec})

    def rename_file(self, info_hash: str, old_path: str, new_path: str) -> None:
        # BitComet WebUI 改名能力未知,整理功能仅对 qB 保证(organize 会按 downloader 名跳过)
        raise NotImplementedError("BitComet 不支持文件重命名整理")


bitcomet_client = BitCometClient()
