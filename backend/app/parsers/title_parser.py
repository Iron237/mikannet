"""种子标题解析:标题 → 集数/版本/合集/分辨率/字幕组。

虚拟库(ADR-0001)、去重、v2 判断、推送文案的共同依赖。
策略:预处理 → 自定义规则(合集/范围/PART/版本)→ anitopy → 中文集数兜底。
解析失败不抛错,返回低 confidence,由调用方决定「下载但待人工确认」。

规则依据 P0 语料(backend/tests/fixtures/titles.txt, n=358)的失败模式:
- `六四位元字幕组★..★04★1920x1080★..` 星号分隔
- `[整理搬运] ..TV动画+OVA+剧场版..` 多部合集
- `[哆啦A梦新番][164PART1]` PART 子集
- `S01v2` 整季版本号
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import anitopy


@dataclass
class ParsedTitle:
    episodes: list[float] = field(default_factory=list)
    version: int = 1
    is_batch: bool = False
    resolution: str | None = None
    group: str | None = None
    source: str | None = None      # "Web" | "BD"(片源,见 _detect_source)
    # 剧集类型,对齐 EpisodeType 枚举值(regular/special/credits/trailer/other)。见 _detect_ep_type
    ep_type: str = "regular"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {"episodes": self.episodes, "version": self.version, "is_batch": self.is_batch,
                "resolution": self.resolution, "group": self.group, "source": self.source,
                "ep_type": self.ep_type, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedTitle":
        return cls(episodes=d.get("episodes") or [], version=d.get("version", 1),
                   is_batch=d.get("is_batch", False), resolution=d.get("resolution"),
                   group=d.get("group"), source=d.get("source"),
                   ep_type=d.get("ep_type", "regular"), confidence=d.get("confidence", 0.0))


# ---- 预处理 ----------------------------------------------------------------
_FULLWIDTH = str.maketrans("０１２３４５６７８９【】（）", "0123456789[]()")


def _preprocess(title: str) -> str:
    t = title.translate(_FULLWIDTH)
    t = t.replace("★", " ").replace("​", "")          # 星号分隔格式、零宽字符
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---- 自定义规则 -------------------------------------------------------------
# 多部整理合集 / 季度合集关键词(此类条目无单集概念)
_BATCH_KEYWORDS = re.compile(r"整理搬运|合集|全集|Batch|BOX(?:\d|\b)|Fin\b", re.I)
# 集数范围:[01-12]、[25-48END]、[13-18TV]、【01-24】→ 合集
_RANGE = re.compile(r"[\[\s](\d{1,4})\s*-\s*(\d{1,4})(?:\s*(?:END|Fin|完|TV|v\d))?[\]\s]", re.I)
# 版本:08v2 / [12 v2] / [V2] / S01v2
_EP_VERSION = re.compile(r"(?<=\d)v(\d)\b", re.I)
_TAG_VERSION = re.compile(r"[\[\s(]v(\d)[\])\s]", re.I)
# 哆啦A梦式 PART/子集:[164PART1]、[879-2]、[490B]、[467-B] → 主集数(子集粒度不建模)
_PART = re.compile(r"\[(\d{1,4})PART\d\]", re.I)
_SUB_EP = re.compile(r"\[(\d{2,4})\s*(?:-\s*[1-5]|-?[AB])\]")
# 整季打包:S01v2 / S2 等季标记且无单集号 → 合集
_SEASON_PACK = re.compile(r"\bS(\d{1,2})(?:v(\d))?\b(?!E\d)", re.I)
# 中文集数兜底:第08话 / 第8.5集 / [08话]
_CN_EP = re.compile(r"第\s*(\d{1,4}(?:\.\d)?)\s*[话話集]|[\[\s](\d{1,4}(?:\.\d)?)\s*[话話][\]\s]")
# 分辨率
_RESOLUTION = re.compile(r"(\d{3,4})[pP]\b|(?:1920|2560|3840)\s*[xX×]\s*(\d{3,4})|(4K)", re.I)
# 片源:BD(蓝光,更具体先判)/ Web(网络流媒体平台)
_SOURCE_BD = re.compile(r"BD-?Rip|BD-?MV|Blu-?Ray|\bBD(?:\d|\b)|REMUX|UHD-?BD", re.I)
_SOURCE_WEB = re.compile(
    r"WEB-?DL|WEB-?Rip|\bWEB\b|Baha|Bilibili|B-?Global|BGlobal|B站|哔哩|"
    r"Crunchyroll|\bCR\b|AMZN|Amazon|Netflix|\bNF\b|ABEMA|Sentai|Viu|iQIYI|爱奇艺|Funimation",
    re.I)
# 剧集类型识别(对齐 EpisodeType)。按特异性排序,命中即返回。
# CREDITS 最先(NCOP/NCED 里含 OP/ED,且最明确);TRAILER 次之;SPECIAL/剧场版;OTHER 兜底。
_EP_CREDITS = re.compile(
    r"\bNC[\s_]?(?:OP|ED)\d*\b|\b(?:OP|ED)\d{1,2}\b|\[\s*(?:OP|ED|NCOP|NCED)\s*\]|"
    r"Creditless|片头|片頭|片尾|無テロップ|ノンクレジット", re.I)
_EP_TRAILER = re.compile(r"\bPV\d*\b|\bCM\d*\b|Trailer|Teaser|预告|預告|\bPreview\b", re.I)
# 强特典标记:整条发布本身就是特典单元(数字是它自身序号,不是正片集号)
_EP_SPECIAL_STRONG = re.compile(
    r"\bSP\d{0,2}\b|\[\s*SP\s*\]|\bOVA\d*\b|\bOAD\d*\b|OAV|剧场版|劇場版|\bMovie\b|\bSpecial\b", re.I)
# 弱特典描述:可能只是修饰某一正片集(如哆啦A梦「[109]…特别篇…」仍是正片第 109 话)
_EP_SPECIAL_WEAK = re.compile(r"特别篇|特別篇|特典|番外|总集篇|總集篇|映像特典", re.I)
# 剧场版/Movie 在番剧内无正片集号 → 归 SPECIAL(番剧整体形态另由 Bangumi.kind 表达)
_EP_SPECIAL = re.compile(_EP_SPECIAL_STRONG.pattern + "|" + _EP_SPECIAL_WEAK.pattern, re.I)
_EP_OTHER = re.compile(
    r"\bMenu\b|\bLogo\b|\bBonus\b|\bSample\b|花絮|访谈|訪談|\bMAD\b|Interview|Web\s?预览", re.I)


def _detect_ep_type(t: str) -> str:
    """标题 → 剧集类型(regular/special/credits/trailer/other)。判不出 → regular。"""
    if _EP_CREDITS.search(t):
        return "credits"
    if _EP_TRAILER.search(t):
        return "trailer"
    if _EP_SPECIAL.search(t):
        return "special"
    if _EP_OTHER.search(t):
        return "other"
    return "regular"


# 字幕组兜底:首个方括号内容
_FIRST_BRACKET = re.compile(r"^\[([^\]]{2,40})\]")
# 方括号内的独立集号:[02]/[04]/[123](括号内只有 1-3 位数字)
_BRACKET_EP = re.compile(r"\[(\d{1,3})\]")

# 范围误判保护:看起来像日期(2016.12.31)/分辨率的数字对不算集数范围
_DATE_LIKE = re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}")


def _detect_resolution(t: str) -> str | None:
    m = _RESOLUTION.search(t)
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)}p"
    if m.group(2):
        return f"{m.group(2)}p"   # 1920x1080 → 1080p
    return "2160p"                 # 4K


def _detect_version(t: str) -> int:
    m = _EP_VERSION.search(t) or _TAG_VERSION.search(t)
    return int(m.group(1)) if m else 1


def _detect_source(t: str) -> str | None:
    """片源:蓝光 → "BD";各网络平台/WEB → "Web";判不出 → None。BD 更具体,先判。"""
    if _SOURCE_BD.search(t):
        return "BD"
    if _SOURCE_WEB.search(t):
        return "Web"
    return None


def detect_source(text: str) -> str | None:
    """公开版:对任意文本(文件名/路径段/文件夹名)判定片源。库扫描的文件夹上下文用。"""
    return _detect_source(_preprocess(text or ""))


def _parse_rules(title: str) -> ParsedTitle:
    raw = title
    t = _preprocess(raw)
    result = ParsedTitle(resolution=_detect_resolution(t), version=_detect_version(t),
                         source=_detect_source(t), ep_type=_detect_ep_type(t))

    # 字幕组:先用 anitopy,后面兜底
    ani = anitopy.parse(t) or {}
    result.group = ani.get("release_group") or (
        m.group(1) if (m := _FIRST_BRACKET.search(t)) else None)

    # 1) 合集判定(关键词 / 集数范围)
    range_m = None
    for m in _RANGE.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if a < b and b - a <= 300 and not _DATE_LIKE.search(m.group(0)):
            range_m = (a, b)
            break
    if _BATCH_KEYWORDS.search(t) or range_m:
        result.is_batch = True
        if not range_m:
            # 已确认是合集,放宽范围提取:允许范围后跟汉字等(如 [01-12修正合集])
            for m in re.finditer(r"(\d{1,4})\s*[-~]\s*(\d{1,4})", t):
                a, b = int(m.group(1)), int(m.group(2))
                if a < b and b - a <= 300 and not _DATE_LIKE.search(m.group(0)):
                    range_m = (a, b)
                    break
        if range_m:
            result.episodes = [float(n) for n in range(range_m[0], range_m[1] + 1)]
        result.confidence = 0.9 if range_m else 0.6   # 无范围的合集(整理搬运)集数未知
        return result

    # 2) PART / 子集格式
    if m := (_PART.search(t) or _SUB_EP.search(t)):
        result.episodes = [float(m.group(1))]
        result.confidence = 0.7
        return result

    # 2.5) 方括号内独立集号 [02]/[04]/[12]:字幕组"标题 N [集]"命名(如 Sakurato 续作季)里,
    #      anitopy 常把裸的季号 N 误当集号,而方括号里的裸数字才是真集号 → 优先采信。
    #      排除常见分辨率裸数字;范围 [01-12]/版本 [v2]/PART 不是纯数字方括号,不会命中。
    brackets = [x for x in _BRACKET_EP.findall(t) if x not in ("480", "720", "1080", "2160")]
    if len(brackets) == 1:
        result.episodes = [float(brackets[0])]
        result.confidence = 0.95
        return result

    # 3) anitopy 主解析
    ep = ani.get("episode_number")
    if ep:
        nums = ep if isinstance(ep, list) else [ep]
        try:
            result.episodes = [float(n) for n in nums]
            if ani.get("release_version"):
                rv = ani["release_version"]
                result.version = int(rv if isinstance(rv, str) else rv[0])
            if len(result.episodes) == 2 and result.episodes[1] - result.episodes[0] > 1:
                # anitopy 把范围解析成两个端点 → 合集
                a, b = result.episodes
                result.episodes = [float(n) for n in range(int(a), int(b) + 1)]
                result.is_batch = True
            result.confidence = 1.0
            return result
        except ValueError:
            pass

    # 4) 中文集数兜底
    if m := _CN_EP.search(t):
        result.episodes = [float(m.group(1) or m.group(2))]
        result.confidence = 0.9
        return result

    # 5) 整季打包兜底(无单集号但有 S01/S01v2 标记)
    if m := _SEASON_PACK.search(t):
        result.is_batch = True
        if m.group(2):
            result.version = int(m.group(2))
        result.confidence = 0.6
        return result

    result.confidence = 0.2
    return result


def parse(title: str) -> ParsedTitle:
    """规则解析;低置信度时(<0.5)尝试 LLM 兜底(需在设置里启用)。"""
    result = _parse_rules(title)
    # 解析后修正(压住误判):
    # 1) 仅弱特典描述(特别篇/特典/番外…)且带明确正片集号时,集号说了算 → 让位给正片
    #    (哆啦A梦「[109]…特别篇…」是正片第 109 话)。强标记(SP01/OVA/剧场版/OP·ED/PV)不让位。
    #    注意:只降级 special,**不碰 other**(menu/Logo/Bonus/MAD 等永远不是正片,
    #    否则 BD 合集里的 [menu][01] 会被当成正片第 1 话,整理时与真第 1 话撞名)。
    if result.ep_type == "special" and not result.is_batch \
            and len(result.episodes) == 1 and result.episodes[0] < 1900 \
            and not _EP_SPECIAL_STRONG.search(_preprocess(title)):
        result.ep_type = "regular"
    # 2) 非正片里抽到的「年份」不是集号(如「剧场版 2023」≠ EP2023)→ 丢弃,留空待归类。
    if result.ep_type != "regular" and not result.is_batch:
        result.episodes = [e for e in result.episodes if not 1900 <= e <= 2099]
    if result.confidence < 0.5:
        try:
            from app.clients.llm import parse_title as _llm
            data = _llm(title)
            if data:
                _merge_llm(result, data)
        except Exception:  # noqa: BLE001 — 兜底失败不影响规则结果
            pass
    return result


def _merge_llm(r: ParsedTitle, d: dict) -> None:
    eps = d.get("episodes")
    if eps and not r.episodes:
        try:
            r.episodes = [float(x) for x in eps if isinstance(x, (int, float))]
        except (TypeError, ValueError):
            pass
    if d.get("is_batch"):
        r.is_batch = True
    if d.get("resolution") and not r.resolution:
        r.resolution = str(d["resolution"])
    if d.get("group") and not r.group:
        r.group = str(d["group"])
    if r.episodes:
        r.confidence = max(r.confidence, 0.8)


# ---- 显示用 chips:语言/字幕标签 + 集数标签(搜索页) -------------------------
# 语言:按特异性从高到低,每个标题只取最高优先级的一个语言 chip(避免 简日+简体 冗余)
_LANG_RULES = [
    ("简繁日", r"简繁日|簡繁日|简繁中日|簡繁中日"),
    ("简日", r"简日|簡日|CHS\s*&?\s*JP|GB\s*&?\s*JP|简中日|簡中日"),
    ("繁日", r"繁日|繁體日|CHT\s*&?\s*JP|BIG5\s*&?\s*JP|繁中日"),
    ("简繁", r"简繁|簡繁|简繁中文|簡繁中文"),
    ("简体", r"简体|簡体|简中|CHS|GB(?![A-Za-z0-9])|GBK"),
    ("繁体", r"繁体|繁體|繁中|CHT|BIG5"),
]
# 字幕封装形式(可与语言并存)
_CONTAINER_RULES = [
    ("内嵌", r"内嵌|內嵌|硬字幕"),
    ("内封", r"内封|內封|内挂|內掛"),
    ("外挂", r"外挂|外掛|软字幕"),
]


def detect_subtitle_tags(title: str) -> list[str]:
    """标题 → 字幕语言/封装 chips(语言最多 1 个 + 封装若干),供搜索页展示与分面筛选。"""
    t = _preprocess(title)
    tags: list[str] = []
    for label, pat in _LANG_RULES:
        if re.search(pat, t, re.I):
            tags.append(label)
            break
    for label, pat in _CONTAINER_RULES:
        if re.search(pat, t, re.I):
            tags.append(label)
    return tags


def episode_label(p: ParsedTitle) -> str | None:
    """集数 chip 文案:单集补零 08、范围 01-12、无范围合集「合集」、未解析 None。"""
    eps = [e for e in p.episodes if isinstance(e, (int, float))]
    if p.is_batch:
        if eps:
            lo, hi = int(min(eps)), int(max(eps))
            return f"{lo:02d}-{hi:02d}" if lo != hi else f"{lo:02d}"
        return "合集"
    if len(eps) == 1:
        n = eps[0]
        return f"{int(n):02d}" if float(n).is_integer() else f"{n:g}"
    if len(eps) > 1:
        return f"{int(min(eps)):02d}-{int(max(eps)):02d}"
    return None
