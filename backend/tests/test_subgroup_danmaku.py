import base64
import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi import HTTPException

from app.api import files
from app.clients import dandanplay
from app.config import settings
from app.parsers.mikan_html import Subgroup, SubgroupTorrent
from app.services.subgroup_recommendation import summarize_groups


def group(name, titles):
    return Subgroup(name, name, [SubgroupTorrent(t, "episode", "release.torrent", None, "2025/1/20")
                                  for t in titles])


def test_recommendation_counts_distinct_matching_episodes(monkeypatch):
    monkeypatch.setattr(settings, "auto_dl_sub_lang", "简中")
    monkeypatch.setattr(settings, "auto_dl_resolution", "1080p")
    series = ["[A] Test - 01 [1080p][CHS]", "[A] Test - 02 [1080p][CHS]"]
    versions = ["[B] Test - 01 [1080p][CHS]", "[B] Test - 01v2 [1080p][CHS]",
                "[B] Test - 02 [720p][CHS]", "[B] Test - 03 [1080p][CHT]"]
    rows = summarize_groups([group("B", versions), group("A", series), group("C", series)])
    assert [(g.name, r["episodes"], r["top"]) for g, _, r in rows] == [
        ("A", 2, True), ("C", 2, True), ("B", 1, False)]
    assert rows[0][2]["latest"] == "2025-01-20"
    monkeypatch.setattr(settings, "auto_dl_resolution", "2160p")
    assert not any(r["top"] for _, _, r in summarize_groups([group("A", series), group("B", [])]))


def test_follow_and_backfill_can_recommend_different_groups(monkeypatch):
    monkeypatch.setattr(settings, "auto_dl_resolution", "1080p")
    monkeypatch.setattr(settings, "auto_dl_sub_lang", "简中")
    archive = group("archive", [f"[A] Test - {i:02} [1080p][CHS]" for i in range(1, 8)])
    ongoing = group("weekly", [f"[B] Test - {i:02} [1080p][CHS]" for i in (11, 12)])
    for item in archive.torrents:
        item.published = "2024/12/20"
    rows = summarize_groups([archive, ongoing], today=date(2025, 1, 22))
    assert [(g.name, r["top"], r["ongoing"]) for g, _, r in rows] == [
        ("weekly", False, True), ("archive", True, False)]


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setattr(settings, "dandanplay_app_id", "test-app")
    monkeypatch.setattr(settings, "dandanplay_app_secret", "test-secret")
    dandanplay._download_xml.cache_clear()
    yield
    dandanplay._download_xml.cache_clear()


def test_official_request_contract_match_offset_xml_and_cache(provider, monkeypatch, tmp_path):
    video = tmp_path / "renamed.mkv"
    prefix = b"x" * (16 * 1024 * 1024)
    video.write_bytes(prefix + b"tail")
    calls = []

    def respond(req):
        import json
        calls.append(req.url.path)
        if req.url.host == "cdn.dandanplay.net":
            assert not any(h.startswith("x-app") or h == "x-signature" for h in req.headers)
            return httpx.Response(200, json={"comments":[{"cid":2, "p":"2.3,5,16777215,user", "m":"甲<&\x00"},
                                                         {"p":"bad", "m":"invalid"}]})
        stamp = req.headers["X-Timestamp"]
        signed = f"test-app{stamp}{req.url.path}test-secret".encode()
        assert req.headers["X-Signature"] == base64.b64encode(hashlib.sha256(signed).digest()).decode()
        if req.url.path.endswith("/match"):
            payload = json.loads(req.content)
            assert payload == {"fileName":"original", "fileHash":hashlib.md5(prefix).hexdigest(),
                               "fileSize":len(prefix)+4, "matchMode":"hashAndFileName"}
            return httpx.Response(200, json={"isMatched":True, "matches":[
                {"episodeId":123, "animeTitle":"Season 2", "episodeTitle":"01", "shift":1.5}]})
        if req.url.path.endswith("/episodes"):
            assert req.url.params["episode"] == "2"
            return httpx.Response(200, json={"animes":[{"animeTitle":"Season 1", "episodes":[
                {"episodeId":124, "episodeTitle":"02"}]}]})
        assert req.url.params["withRelated"] == "true"
        return httpx.Response(302, headers={"location":"https://cdn.dandanplay.net/test"})

    monkeypatch.setattr(dandanplay, "make_client", lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(respond)))
    matched = dandanplay.match(video, "original.mkv")
    choice = matched["choices"][0]
    assert matched["exact"] and choice["episode_id"] == 123
    assert dandanplay.search("Season", "2")[0]["episode_id"] == 124
    xml, count = dandanplay.download(choice["episode_id"], choice["shift"])
    assert count == 1
    comment = ET.fromstring(xml).find("d")
    assert comment.text == "甲<&" and comment.get("p") == "3.800,5,25,16777215,0,0,user,2"
    assert dandanplay.download(123, 1.5) == (xml, count)
    assert calls.count("/api/v2/comment/123") == 1


def test_danmaku_save_preserves_existing_xml_and_allows_browser_copy(provider, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    video = tmp_path / "番剧 S02E01.mkv"
    video.touch()
    monkeypatch.setattr(files, "_video_file", lambda *args: (SimpleNamespace(original_name=None), video))
    monkeypatch.setattr(dandanplay, "download", lambda *args: (b"<i/>", 1))
    selection = files.DanmakuSelection(episode_id=123)
    result = files.save_danmaku(1, selection, False, None)
    assert result["count"] == 1 and "download=true" in result["existing"]
    xml = video.with_suffix(".xml")
    xml.write_bytes(b"keep existing file")
    assert files.danmaku_matches(1, None)["existing"] == result["existing"]
    with pytest.raises(HTTPException) as err:
        files.save_danmaku(1, selection, False, None)
    assert err.value.status_code == 409 and xml.read_bytes() == b"keep existing file"
    assert files.save_danmaku(1, selection, True, None).body == b"<i/>"
    assert list(tmp_path.glob("*.part")) == []


def test_empty_comments_and_missing_credentials(provider, monkeypatch):
    monkeypatch.setattr(dandanplay, "_request", lambda *args, **kw: {"comments":[]})
    with pytest.raises(HTTPException) as err:
        dandanplay.download(90, 0)
    assert err.value.status_code == 404
    monkeypatch.setattr(settings, "dandanplay_app_secret", "")
    assert not dandanplay.match(Path("never-opened.mkv"), None)["configured"]


def test_auto_fetch_requires_exact_match_and_keeps_existing_xml(provider, monkeypatch, tmp_path):
    from app.services.danmaku import fetch_available
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    video = tmp_path / "S02E01.mkv"
    video.touch()
    choice = {"episode_id": 123, "shift": 0.0}
    result = {"choices": [choice], "exact": False}
    monkeypatch.setattr(dandanplay, "match", lambda *args: result)
    monkeypatch.setattr(dandanplay, "download", lambda *args: (b"<i/>", 1))
    assert fetch_available(video, None)["status"] == "needs_selection"
    assert not video.with_suffix(".xml").exists()
    result["exact"] = True
    assert fetch_available(video, None) == {"status": "saved", "count": 1}
    assert fetch_available(video, None)["status"] == "existing"
    monkeypatch.setattr(settings, "danmaku_auto_download", False)
    assert fetch_available(video, None)["status"] == "disabled"
