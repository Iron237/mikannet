"""Mikan HTML 解析:fixtures 快照回归(Mikan 改版时此测试先红)。"""
from pathlib import Path

import pytest

from app.parsers.mikan_html import parse_bangumi, parse_search

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "mikan_bangumi_3530.html").exists(), reason="fixtures 未抓取")


def test_parse_search():
    results = parse_search((FIXTURES / "mikan_search.html").read_text(encoding="utf-8"))
    assert len(results) >= 2
    ids = {r.mikan_bangumi_id for r in results}
    assert 3530 in ids and 3203 in ids
    hit = next(r for r in results if r.mikan_bangumi_id == 3530)
    assert "药" in hit.title
    assert hit.cover_url and hit.cover_url.startswith("/images/")


def test_parse_bangumi():
    d = parse_bangumi((FIXTURES / "mikan_bangumi_3530.html").read_text(encoding="utf-8"), 3530)
    assert "药" in d.title
    assert d.bgmtv_subject_id == 486347
    assert d.cover_url and d.cover_url.startswith("/images/")
    assert d.air_date_str and "2025" in d.air_date_str
    assert len(d.subgroups) >= 10
    g = next(g for g in d.subgroups if g.subgroup_id == "635")
    assert g.torrents, "字幕组种子表为空"
    t = g.torrents[0]
    assert t.episode_url.startswith("/Home/Episode/")
    assert t.torrent_url and t.torrent_url.endswith(".torrent")
    # 列错位回归:size 必须是体积串、published 必须含年份(曾把标题当 size、体积当时间)
    import re
    assert t.size and re.search(r"\d+(\.\d+)?\s*[GMK]B", t.size), f"size 列错位: {t.size!r}"
    assert t.published and re.search(r"20\d\d", t.published), f"published 列错位: {t.published!r}"
