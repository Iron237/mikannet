"""bgm.tv v0 API 客户端:番剧元数据(年代/制作公司/简介/官方译名/评分)。

字段依据 P0 探针(fixtures/bgmtv_subject_486347.json)。
"""
from dataclasses import dataclass

from app.clients.http import make_client

API = "https://api.bgm.tv"
UA = {"User-Agent": "mikanarr/0.1 (https://github.com/local/mikanarr)"}


@dataclass
class BgmtvSubject:
    subject_id: int
    name: str                 # 原名
    name_cn: str | None       # 官方中文译名
    date: str | None          # "2025-01-10" → 年代
    platform: str | None      # TV / 剧场版
    eps: int | None
    studio: str | None        # infobox 动画制作
    score: float | None
    summary: str | None
    cover_url: str | None


class BgmtvClient:
    def get_subject(self, subject_id: int) -> BgmtvSubject:
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(f"{API}/v0/subjects/{subject_id}")
            r.raise_for_status()
            d = r.json()
        infobox = {i["key"]: i["value"] for i in d.get("infobox", [])
                   if isinstance(i.get("value"), str)}
        return BgmtvSubject(
            subject_id=subject_id,
            name=d.get("name") or "",
            name_cn=d.get("name_cn") or None,
            date=d.get("date"),
            platform=d.get("platform"),
            eps=d.get("eps") or None,
            studio=infobox.get("动画制作") or infobox.get("製作") or infobox.get("制作"),
            score=(d.get("rating") or {}).get("score"),
            summary=d.get("summary") or None,
            cover_url=(d.get("images") or {}).get("large"))

    def download_image(self, url: str) -> bytes:
        with make_client("bgmtv", headers=UA) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content


bgmtv_client = BgmtvClient()
