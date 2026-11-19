"""BD 替换后的自动收尾必须以正式正片实盘为准。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.models import (Bangumi, Episode, EpisodeType, Kind, Subscription, Torrent,
                        TorrentStatus, VideoFile)
from app.services import lifecycle


@pytest.fixture
def season(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    monkeypatch.setattr(settings, "storage_mode", "local")
    monkeypatch.setattr(lifecycle, "_notify", lambda *_: None)
    monkeypatch.setattr(lifecycle.downloader, "files", lambda _: [{"name": "Web.mkv"}])
    monkeypatch.setattr(lifecycle.downloader, "delete",
                        lambda *_args, **_kwargs: pytest.fail("不应清理 Web 版本"))
    with Session(engine) as db:
        b = Bangumi(title="第二季", eps_total=1, ep_start=13, auto_best=True)
        sub = Subscription(bangumi=b, mikan_subgroup_id="9", enabled=True, save_path="/d")
        ep = Episode(bangumi=b, number=13, type=EpisodeType.REGULAR)
        web = Torrent(subscription=sub, guid="web", info_hash="web-hash", title_raw="Web",
                      torrent_url="", status=TorrentStatus.ARCHIVED, episodes=[ep])
        bd = Torrent(subscription=sub, guid="bd", title_raw="BD", torrent_url="",
                     status=TorrentStatus.ARCHIVED, episodes=[ep])
        db.add_all([b, sub, ep, web, bd])
        db.flush()
        for torrent, source, active in ((web, "Web", False), (bd, "BD", True)):
            path = f"{source}.mkv"
            (tmp_path / path).touch()
            db.add(VideoFile(torrent_id=torrent.id, episode_id=ep.id, relative_path=path,
                             source=source, is_active=active))
        db.commit()
        yield db, b, web, bd
    engine.dispose()


@pytest.mark.parametrize("case", ["missing", "preview", "fraction", "unknown_total",
                                  "movie", "smb", "nfs"])
def test_unconfirmed_coverage_keeps_files_and_subscription(season, case, tmp_path, monkeypatch):
    db, b, web, bd = season
    if case == "missing":
        assert lifecycle.bd_complete(db, b)
        (tmp_path / "BD.mkv").unlink()
    elif case == "preview":
        bd.is_preview = True
    elif case == "fraction":
        bd.episodes[0].number = 13.5
    elif case == "unknown_total":
        b.eps_total = None
    elif case == "movie":
        b.kind = Kind.MOVIE
    else:
        monkeypatch.setattr(settings, "storage_mode", case)
        monkeypatch.setattr(lifecycle.storage, "is_mounted", lambda: False)
    db.flush()
    assert not lifecycle.finalize_complete(db, b)
    assert b.auto_best and web.subscription.enabled
    assert len(web.files) == 1 and db.get(Torrent, web.id) is web


def test_cleanup_failure_preserves_links_for_retry(season, monkeypatch):
    db, b, web, bd = season
    web_id, bd_id = web.id, bd.id
    attempts = []

    def delete(info_hash, *, delete_files):
        attempts.append((info_hash, delete_files))
        if len(attempts) == 1:
            raise ConnectionError("下载器暂不可达")

    monkeypatch.setattr(lifecycle.downloader, "delete", delete)
    lifecycle.finalize_complete(db, b)
    db.commit()
    assert db.get(Torrent, web_id).files and web.episodes
    assert lifecycle.finalize_complete(db, b)
    db.commit()
    assert db.get(Torrent, web_id) is None and db.get(Torrent, bd_id).files
    assert not lifecycle.finalize_complete(db, b)
    assert attempts == [("web-hash", True)] * 2


@pytest.mark.parametrize("case", ["unmapped", "shared_path", "unknown_source", "preview",
                                  "extra_video", "extra_subtitles", "shared_alias", "replacement_disappears"])
def test_only_proven_redundant_web_torrents_are_removed(season, case, monkeypatch, tmp_path):
    db, b, web, _ = season
    if case == "unmapped":
        db.add(VideoFile(torrent_id=web.id, episode_id=None, relative_path="bonus.mkv",
                         source="Web", is_active=False))
    elif case == "shared_path":
        web.files[0].relative_path = "BD.mkv"
        monkeypatch.setattr(lifecycle.downloader, "files", lambda _: [{"name": "BD.mkv"}])
    elif case == "unknown_source":
        web.files[0].source = None
    elif case == "preview":
        web.is_preview = True
    elif case in ("extra_video", "extra_subtitles"):
        extra = "bonus.mkv" if case == "extra_video" else "bonus.ass"
        monkeypatch.setattr(lifecycle.downloader, "files",
                            lambda _: [{"name": "Web.mkv"}, {"name": extra}])
    elif case == "shared_alias":
        bd = db.query(Torrent).filter_by(guid="bd").one()
        db.add(VideoFile(torrent_id=bd.id, relative_path="old/../WEB.mkv",
                         source="Web", is_active=False))
    elif case == "replacement_disappears":
        def inventory(_):
            (tmp_path / "BD.mkv").unlink()
            return [{"name": "Web.mkv"}]
        monkeypatch.setattr(lifecycle.downloader, "files", inventory)
    db.flush()
    assert lifecycle.bd_complete(db, b)
    lifecycle.finalize_complete(db, b)
    db.commit()
    assert db.get(Torrent, web.id).files
