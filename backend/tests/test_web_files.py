"""网页播放与文件浏览的路径边界。"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import files
from app.config import settings
from app.services import launch


def test_browse_is_limited_to_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    season = tmp_path / "Season 01"
    season.mkdir()
    (season / "E01.mp4").write_bytes(b"demo")
    (season / "notes.txt").write_text("x", encoding="utf-8")

    out = files.browse("Season 01")
    assert out["path"] == "Season 01"
    assert [item["name"] for item in out["entries"]] == ["E01.mp4", "notes.txt"]
    assert out["entries"][0]["is_video"] is True
    assert out["entries"][0]["content_url"].startswith("/api/files/content?")

    with pytest.raises(HTTPException) as exc:
        files.browse("../")
    assert exc.value.status_code == 400


def test_mkdir_cannot_escape_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    out = files.mkdir({"parent": "", "name": "新番"})
    assert out == {"ok": True, "path": "新番"}
    assert (tmp_path / "新番").is_dir()
    with pytest.raises(HTTPException):
        files.mkdir({"parent": "", "name": "../escape"})


def test_dynamic_windows_path_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "media_host_root", "Z:\\番剧\\mikannet")
    monkeypatch.setattr(settings, "bd_owned_host_root", "")
    monkeypatch.setattr(settings, "data_host_root", "")
    assert launch.path_allowed("Z:\\番剧\\mikannet\\Season 01\\E01.mkv")
    assert not launch.path_allowed("Z:\\番剧\\elsewhere\\secret.txt")
    assert not launch.path_allowed("Z:\\番剧\\mikannet-escape\\x.mkv")
