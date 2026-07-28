"""下载根改名/换址回归：订阅、qB 与磁盘记录必须指向同一份文件。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models import (Bangumi, Episode, EpisodeType, Subscription, Torrent,
                        TorrentEpisode, TorrentStatus, VideoFile)
from app.services import download_paths, library_scan, postprocess


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _torrent(db, *, status=TorrentStatus.COMPLETED):
    b = Bangumi(title="尼古喵喵")
    db.add(b)
    db.flush()
    sub = Subscription(
        bangumi_id=b.id, mikan_subgroup_id="auto", enabled=False,
        save_path=r"\\nas\番剧\mikanarr\尼古喵喵")
    db.add(sub)
    db.flush()
    ep = Episode(bangumi_id=b.id, number=2, type=EpisodeType.REGULAR)
    db.add(ep)
    db.flush()
    torrent = Torrent(
        subscription_id=sub.id, guid="g2", info_hash="hash2", title_raw="第 2 集",
        torrent_url="", status=status, parsed_json={"episodes": [2]})
    db.add(torrent)
    db.flush()
    db.add(TorrentEpisode(torrent_id=torrent.id, episode_id=ep.id))
    db.flush()
    return sub, ep, torrent


def test_unc_rebase_preserves_suffix():
    old = r"\\nas\共享\番剧\mikanarr"
    new = r"\\nas\共享\番剧\mikannet"
    path = r"\\NAS\共享\番剧\mikanarr/尼古喵喵"
    assert download_paths.rebase_path(path, old, new) == (
        r"\\nas\共享\番剧\mikannet\尼古喵喵")
    assert download_paths.relative_under(path, new) is None


def test_rebase_all_subscription_sources(db):
    sub, _, _ = _torrent(db)
    changed = download_paths.rebase_subscription_paths(
        db, r"\\nas\番剧\mikanarr", r"\\nas\番剧\mikannet")
    assert changed == 1
    assert sub.save_path == r"\\nas\番剧\mikannet\尼古喵喵"


def test_qb_files_reject_save_path_outside_current_root(monkeypatch):
    from app.clients import qbittorrent as qb

    class _Torrents:
        def info(self, **_kw):
            return [SimpleNamespace(save_path=r"\\nas\番剧\mikanarr\尼古喵喵")]

        def files(self, **_kw):
            return [{"name": "02.mkv", "size": 123}]

    client = qb.QbClient()
    client._client = SimpleNamespace(torrents=_Torrents())
    monkeypatch.setattr(settings, "download_root", r"\\nas\番剧\mikannet")
    with pytest.raises(RuntimeError, match="不在当前下载根"):
        client.files("hash2")


def test_qb_rebase_moves_each_torrent_to_matching_subdir(monkeypatch):
    from app.clients import qbittorrent as qb

    calls = []

    class _Torrents:
        def info(self, **_kw):
            return [SimpleNamespace(
                hash="hash2", save_path=r"\\nas\番剧\mikanarr\尼古喵喵")]

        def set_location(self, **kw):
            calls.append(kw)

    client = qb.QbClient()
    client._client = SimpleNamespace(torrents=_Torrents())
    monkeypatch.setattr(settings, "qb_category", "mikannet")
    assert client.rebase_save_paths(
        r"\\nas\番剧\mikanarr", r"\\nas\番剧\mikannet") == 1
    assert calls == [{
        "location": r"\\nas\番剧\mikannet\尼古喵喵",
        "torrent_hashes": "hash2",
    }]


def test_postprocess_removes_ghost_file_record(db, tmp_path, monkeypatch):
    """qB 仍报 100% 但实体不在当前根：不得继续让详情页显示为有文件。"""
    _, ep, torrent = _torrent(db)
    rel = "尼古喵喵/不存在的第2集.mkv"
    ghost = VideoFile(
        torrent_id=torrent.id, episode_id=ep.id, relative_path=rel, is_active=True)
    db.add(ghost)
    db.flush()
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    monkeypatch.setattr(
        postprocess.downloader, "files",
        lambda _hash: [{"name": rel, "size": 740176222}])

    postprocess.process_torrent(db, torrent.id)

    assert db.query(VideoFile).count() == 0
    assert torrent.status == TorrentStatus.COMPLETED
    assert torrent.error_message == "1 个文件探测失败,可重试"


def test_postprocess_prunes_old_path_after_qb_rebase(db, tmp_path, monkeypatch):
    """qB 换根后清单出现正确前缀：旧幽灵行必须让位给真实新路径。"""
    _, ep, torrent = _torrent(db)
    old_rel = "原始目录/第2集.mkv"
    new_rel = "尼古喵喵/第2集.mkv"
    db.add(VideoFile(
        torrent_id=torrent.id, episode_id=ep.id,
        relative_path=old_rel, size=123, is_active=True))
    (tmp_path / "尼古喵喵").mkdir()
    (tmp_path / new_rel).write_bytes(b"video")
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    monkeypatch.setattr(settings, "organize_enabled", False)
    monkeypatch.setattr(
        postprocess.downloader, "files",
        lambda _hash: [{"name": new_rel, "size": 5}])
    monkeypatch.setattr(
        postprocess.media_probe, "probe",
        lambda _path: SimpleNamespace(
            resolution="1080p", video_codec="h264", color_depth="8bit",
            hdr=None, bitrate=1, audio_tracks=[], subtitle_tracks=[]))

    postprocess.process_torrent(db, torrent.id)

    rows = db.query(VideoFile).all()
    assert [row.relative_path for row in rows] == [new_rel]
    assert rows[0].is_active is True
    assert torrent.status == TorrentStatus.ARCHIVED


def test_library_reconcile_includes_qb_rows_with_missing_parent(
        db, tmp_path, monkeypatch):
    """普通 qB 任务的整个旧父目录消失时，也必须清掉幽灵记录。"""
    _, ep, torrent = _torrent(db, status=TorrentStatus.ARCHIVED)
    ghost = VideoFile(
        torrent_id=torrent.id, episode_id=ep.id,
        relative_path="旧目录/第2集.mkv", is_active=True)
    db.add(ghost)
    db.flush()
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    monkeypatch.setattr(settings, "storage_mode", "local")

    assert library_scan._reconcile_removed(db) == 1
    assert db.query(VideoFile).count() == 0
