"""种子活跃度探测:UDP tracker scrape(BEP-15),下载前获知做种/下载人数。

流程:经代理取回 .torrent → 算 info_hash → 并行 scrape 数个稳定公共 tracker → 取最大值。
结果按 torrent_url 进程内缓存(TTL 1h)。
"""
from __future__ import annotations

import logging
import random
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from app.clients.bencode import info_hash_of
from app.clients.mikan import mikan_client

log = logging.getLogger(__name__)

# 稳定的 UDP 公共 tracker(scrape 支持好)
SCRAPE_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.gmi.gd:6969/announce",
]

_MAGIC = 0x41727101980
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600


def _udp_scrape(tracker: str, info_hash: bytes, timeout: float = 4.0) -> dict | None:
    u = urlparse(tracker)
    try:
        addr = (socket.gethostbyname(u.hostname), u.port)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        # connect
        txid = random.randint(0, 0x7FFFFFFF)
        s.sendto(struct.pack(">QII", _MAGIC, 0, txid), addr)
        data = s.recv(16)
        action, rtx, conn_id = struct.unpack(">IIQ", data)
        if action != 0 or rtx != txid:
            return None
        # scrape
        txid = random.randint(0, 0x7FFFFFFF)
        s.sendto(struct.pack(">QII", conn_id, 2, txid) + info_hash, addr)
        data = s.recv(8 + 12)
        action, rtx = struct.unpack(">II", data[:8])
        if action != 2 or rtx != txid:
            return None
        seeders, completed, leechers = struct.unpack(">III", data[8:20])
        return {"seeders": seeders, "leechers": leechers, "completed": completed}
    except Exception:  # noqa: BLE001 — 单 tracker 失败正常
        return None
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def scrape_torrent(torrent_url: str) -> dict:
    """返回 {seeders, leechers, completed} 或 {error}。带缓存。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(torrent_url)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        data = mikan_client.download_torrent(torrent_url)
        ih = bytes.fromhex(info_hash_of(data))
    except Exception as e:  # noqa: BLE001
        return {"error": f"取种子失败: {e}"}

    best: dict | None = None
    with ThreadPoolExecutor(max_workers=len(SCRAPE_TRACKERS)) as pool:
        futs = [pool.submit(_udp_scrape, t, ih) for t in SCRAPE_TRACKERS]
        for f in as_completed(futs):
            r = f.result()
            if r and (best is None or r["seeders"] > best["seeders"]):
                best = r
    result = best if best else {"seeders": 0, "leechers": 0, "completed": 0, "no_response": True}
    with _cache_lock:
        _cache[torrent_url] = (now, result)
    return result


def scrape_many(torrent_urls: list[str], max_workers: int = 6) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(scrape_torrent, u): u for u in torrent_urls}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
    return out
