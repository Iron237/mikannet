"""LLM 兜底标题解析(OpenAI 兼容 /chat/completions)。仅在规则解析低置信度时调用,结果按标题缓存。

走代理(settings.proxy_url):clash 类代理会按规则把国内 relay 直连、国外 API 走代理,兼容性最好。
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import settings

log = logging.getLogger(__name__)
_cache: dict[str, dict | None] = {}

_SYS = (
    "你是动漫种子文件名解析器。从标题中提取信息,**只输出一个 JSON 对象**,字段:"
    '{"episodes": [数字数组], "is_batch": 布尔, "group": "字幕组名或null", "resolution": "如1080p或null"}。'
    "episodes 是该标题包含的集号(单集一个元素;合集列出全部集号);无法判断用 null 或空数组。"
    "不要输出 JSON 以外的任何文字、不要用 markdown 代码块。"
)


def parse_title(title: str) -> dict | None:
    if not (settings.llm_enabled and settings.llm_base_url and settings.llm_api_key):
        return None
    if title in _cache:
        return _cache[title]
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": settings.llm_model or "gpt-4o-mini",
        "temperature": 0,
        "messages": [{"role": "system", "content": _SYS},
                     {"role": "user", "content": title}],
    }
    result: dict | None = None
    try:
        with httpx.Client(proxy=settings.proxy_url or None, trust_env=False, timeout=30) as c:
            r = c.post(url, json=body,
                       headers={"Authorization": "Bearer " + settings.llm_api_key})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)   # 容错:从可能含多余文字的回复里抠出 JSON
        if m:
            result = json.loads(m.group(0))
    except Exception as e:  # noqa: BLE001 — 兜底失败不影响主流程
        log.warning("LLM 解析失败 %r: %s", title[:50], e)
    _cache[title] = result
    return result
