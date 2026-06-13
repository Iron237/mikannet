"""ffprobe 封装:提取分辨率/编码/码率/音轨/字幕轨。

SMB 上禁止并发探测 → 调用方(postprocess 队列)保证串行;单文件 60s 超时。
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 60
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".webm", ".mov", ".flv", ".wmv", ".m2ts"}


@dataclass
class ProbeResult:
    resolution: str | None = None        # "1920x1080"
    video_codec: str | None = None
    bitrate: int | None = None           # bps
    audio_tracks: list[dict] = field(default_factory=list)     # {codec, lang, title}
    subtitle_tracks: list[dict] = field(default_factory=list)  # {codec, lang, title}


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def probe(path: Path) -> ProbeResult:
    """探测失败抛异常,由调用方标记重试。"""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT, check=True)
    data = json.loads(out.stdout)

    result = ProbeResult()
    fmt = data.get("format", {})
    if br := fmt.get("bit_rate"):
        result.bitrate = int(br)

    for s in data.get("streams", []):
        tags = s.get("tags", {}) or {}
        entry = {"codec": s.get("codec_name"),
                 "lang": tags.get("language"), "title": tags.get("title")}
        match s.get("codec_type"):
            case "video":
                if result.video_codec is None and s.get("disposition", {}).get("attached_pic") != 1:
                    result.video_codec = s.get("codec_name")
                    if s.get("width") and s.get("height"):
                        result.resolution = f"{s['width']}x{s['height']}"
            case "audio":
                result.audio_tracks.append(entry)
            case "subtitle":
                result.subtitle_tracks.append(entry)
    return result
