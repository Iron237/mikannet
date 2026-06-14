"""ffprobe 封装:提取分辨率/编码/码率/色深/HDR/音轨(含声道)/字幕轨(含 sidecar 外挂)。

SMB 上禁止并发探测 → 调用方(postprocess 队列)保证串行;单文件 60s 超时。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 60
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".webm", ".mov", ".flv", ".wmv", ".m2ts"}
SUB_EXTS = {".ass", ".ssa", ".srt", ".sup", ".vtt", ".sub", ".idx", ".pgs"}


@dataclass
class ProbeResult:
    resolution: str | None = None        # "1920x1080"
    video_codec: str | None = None
    color_depth: str | None = None       # "8bit" / "10bit" / "12bit"
    hdr: str | None = None               # "HDR10" / "HLG" / "DV";None=SDR
    bitrate: int | None = None           # bps
    audio_tracks: list[dict] = field(default_factory=list)     # {codec, lang, title, channels}
    subtitle_tracks: list[dict] = field(default_factory=list)  # {codec, lang, title, source}


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


# ---- 字段推导 --------------------------------------------------------------
_PIXFMT_DEPTH = re.compile(r"p(\d{2})(?:le|be)?$|(\d{2})le$|(\d{2})be$")
_CHAN_BY_LAYOUT = {"mono": "1.0", "stereo": "2.0", "2.1": "2.1", "quad": "4.0",
                   "5.0": "5.0", "5.0(side)": "5.0", "5.1": "5.1", "5.1(side)": "5.1",
                   "6.1": "6.1", "7.1": "7.1", "7.1(wide)": "7.1"}
_CHAN_BY_COUNT = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 6: "5.1", 8: "7.1"}


def _color_depth(s: dict) -> str | None:
    """色深:优先 bits_per_raw_sample,否则从 pix_fmt(yuv420p10le→10bit)推。"""
    if (bprs := s.get("bits_per_raw_sample")) and str(bprs).isdigit() and int(bprs) > 0:
        return f"{int(bprs)}bit"
    pix = s.get("pix_fmt") or ""
    if m := _PIXFMT_DEPTH.search(pix):
        d = next(g for g in m.groups() if g)
        return f"{int(d)}bit"
    return "8bit" if pix else None


def _hdr(s: dict) -> str | None:
    """HDR:Dolby Vision(side_data)> PQ(smpte2084)=HDR10 > HLG(arib-std-b67);否则 SDR。"""
    for sd in s.get("side_data_list") or []:
        blob = json.dumps(sd).lower()
        if "dolby vision" in blob or "dv_profile" in blob or "dovi" in blob:
            return "DV"
    ct = (s.get("color_transfer") or "").lower()
    if ct == "smpte2084":
        return "HDR10"
    if ct == "arib-std-b67":
        return "HLG"
    return None


def _channels(s: dict) -> str | None:
    layout = (s.get("channel_layout") or "").lower()
    if layout in _CHAN_BY_LAYOUT:
        return _CHAN_BY_LAYOUT[layout]
    ch = s.get("channels")
    if isinstance(ch, int) and ch > 0:
        return _CHAN_BY_COUNT.get(ch, f"{ch}ch")
    return None


# ---- 外挂字幕(sidecar)----------------------------------------------------
# 文件名语言码 → 标签(从高特异性到低;扫到第一个即用)
_SIDECAR_LANG = [
    ("简日双语", r"\b(?:sc|chs|gb)[\s._&+-]*jp|chs_jpn|sc&jp"),
    ("繁日双语", r"\b(?:tc|cht|big5)[\s._&+-]*jp|cht_jpn|tc&jp"),
    ("简繁", r"\bsc&tc\b|chs&cht|jpsc|简繁"),
    ("简体", r"\b(?:sc|chs|gb|gbk|zh-hans|zhcn)\b|简体|简中"),
    ("繁体", r"\b(?:tc|cht|big5|zh-hant|zhtw|zhhk)\b|繁体|繁體|繁中"),
    ("日语", r"\b(?:jp|jpn|ja)\b|日语|日本語"),
    ("英语", r"\b(?:en|eng)\b|英语"),
]


def _sidecar_lang(token: str) -> str | None:
    for label, pat in _SIDECAR_LANG:
        if re.search(pat, token, re.I):
            return label
    return None


def scan_sidecar_subs(video: Path) -> list[dict]:
    """同目录里与该视频同名(stem 前缀)的外挂字幕 → 轨条目。失败返回 []。

    例 `Show - 01.sc.ass` / `Show - 01.chs&jpn.ass`:取视频 stem 与字幕 stem 之间的差异段推语言。
    """
    out: list[dict] = []
    try:
        stem = video.stem
        for f in video.parent.iterdir():
            if not f.is_file() or f.suffix.lower() not in SUB_EXTS:
                continue
            if not f.stem.lower().startswith(stem.lower()):
                continue
            token = f.stem[len(stem):] or f.stem   # stem 之后的语言段(.sc / .chs&jp …)
            out.append({"codec": f.suffix.lstrip(".").lower(),
                        "lang": _sidecar_lang(token), "title": f.name, "source": "external"})
    except OSError as e:
        log.debug("扫描外挂字幕失败 %s: %s", video, e)
    return out


def probe(path: Path) -> ProbeResult:
    """探测失败抛异常,由调用方标记重试。自动并入同目录外挂字幕。"""
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
        match s.get("codec_type"):
            case "video":
                if result.video_codec is None and s.get("disposition", {}).get("attached_pic") != 1:
                    result.video_codec = s.get("codec_name")
                    if s.get("width") and s.get("height"):
                        result.resolution = f"{s['width']}x{s['height']}"
                    result.color_depth = _color_depth(s)
                    result.hdr = _hdr(s)
            case "audio":
                result.audio_tracks.append({
                    "codec": s.get("codec_name"), "lang": tags.get("language"),
                    "title": tags.get("title"), "channels": _channels(s)})
            case "subtitle":
                result.subtitle_tracks.append({
                    "codec": s.get("codec_name"), "lang": tags.get("language"),
                    "title": tags.get("title"), "source": "embedded"})

    # 外挂字幕并入(去重:同 codec+lang 的内封已存在则不重复加)
    seen = {(t.get("codec"), t.get("lang")) for t in result.subtitle_tracks}
    for sc in scan_sidecar_subs(path):
        if (sc["codec"], sc["lang"]) not in seen:
            result.subtitle_tracks.append(sc)
    return result
