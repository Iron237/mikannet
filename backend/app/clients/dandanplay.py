"""弹弹play开放弹幕网络。仅在用户对单个视频发起操作时访问。"""
import base64
import hashlib
import math
from functools import lru_cache
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx
from fastapi import HTTPException

from app.clients.http import make_client
from app.config import settings

ORIGIN = "https://api.dandanplay.net"


def configured() -> bool:
    return bool(settings.dandanplay_app_id.strip() and settings.dandanplay_app_secret.strip())


def _request(method: str, path: str, **kwargs) -> dict:
    if not configured():
        raise HTTPException(409, "请先在设置的「网络与元数据 → 弹幕」填写弹弹play应用凭据")
    stamp = str(int(time.time()))
    app_id = settings.dandanplay_app_id.strip()
    data = (app_id + stamp + path + settings.dandanplay_app_secret.strip()).encode()
    headers = {"X-AppId": app_id, "X-Timestamp": stamp,
               "X-Signature": base64.b64encode(hashlib.sha256(data).digest()).decode()}
    try:
        with make_client("dandanplay", timeout=25) as client:
            response = client.request(method, ORIGIN + path, headers=headers, follow_redirects=False, **kwargs)
            # 弹幕可能由 CDN 提供。跳转请求只取数据，应用鉴权头只发给原 API。
            for _ in range(3):
                if method != "GET" or not response.is_redirect:
                    break
                target = urljoin(str(response.url), response.headers.get("location", ""))
                parsed = urlsplit(target)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username:
                    raise HTTPException(502, "弹幕服务返回了无效的下载地址")
                response = client.get(target, follow_redirects=False)
            if response.status_code in (401, 403):
                raise HTTPException(502, "弹弹play鉴权失败，请检查应用凭据及服务器时间")
            if response.status_code == 429:
                raise HTTPException(429, "弹弹play请求额度受限，请稍后再试")
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "弹弹play暂时不可用，请稍后重试") from exc
    if not isinstance(result, dict) or result.get("success") is False:
        raise HTTPException(502, "弹弹play未能完成本次请求")
    return result


def _choice(item: dict, title: str) -> dict:
    return {"episode_id": item["episodeId"], "anime": title, "episode": item.get("episodeTitle") or "",
            "shift": item.get("shift") or 0}


def match(path: Path, original_name: str | None) -> dict:
    if not configured():
        return {"configured": False, "exact": False, "choices": []}
    with path.open("rb") as video:
        file_hash = hashlib.md5(video.read(16 * 1024 * 1024)).hexdigest()
    filename = Path((original_name or path.name).replace("\\", "/")).stem
    data = _request("POST", "/api/v2/match", json={"fileName": filename, "fileHash": file_hash,
                    "fileSize": path.stat().st_size, "matchMode": "hashAndFileName"})
    choices = [_choice(item, item.get("animeTitle") or "") for item in data.get("matches") or []]
    return {"configured": True, "exact": bool(data.get("isMatched")) and len(choices) == 1, "choices": choices}


def search(anime: str, episode: str) -> list[dict]:
    result = _request("GET", "/api/v2/search/episodes", params={"anime": anime, "episode": episode, "v2": "true"})
    return [_choice(item, work.get("animeTitle") or "") for work in result.get("animes") or []
            for item in work.get("episodes") or []]


_INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def download(episode_id: int, shift: float) -> tuple[bytes, int]:
    if not configured():
        raise HTTPException(409, "请先在设置中填写弹弹play应用凭据")
    # 保存到库和随后从浏览器下载可以共用同一版 XML。
    return _download_xml(episode_id, shift, settings.dandanplay_app_id, int(time.time()) // 21600)


@lru_cache(maxsize=8)
def _download_xml(episode_id: int, shift: float, app_id: str, cache_period: int) -> tuple[bytes, int]:
    data = _request("GET", f"/api/v2/comment/{episode_id}", params={"withRelated": "true"})
    root = ET.Element("i")
    ET.SubElement(root, "chatid").text = str(episode_id)
    count = 0
    for item in data.get("comments") or []:
        try:
            seconds, mode, color, sender = item["p"].split(",", 3)
            when = float(seconds) + shift
            mode, color = int(mode), int(color)
            message = _INVALID_XML.sub("", item["m"])
            if not math.isfinite(when) or when < 0 or not message or not 0 <= color <= 0xffffff:
                continue
            comment_id = int(item.get("cid") or 0)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        params = f"{when:.3f},{mode},25,{color},0,0,{_INVALID_XML.sub('', sender)},{comment_id}"
        ET.SubElement(root, "d", p=params).text = message
        count += 1
    if not count:
        raise HTTPException(404, "本集暂无可保存的弹幕")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), count
