"""整理(organize)测试:本地导入走文件系统 move、托管走下载器改名、先行版目录、保留原始名。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models import Bangumi, Episode, EpisodeType, Subscription, Torrent, TorrentStatus, VideoFile
from app.services import organize


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "organize_enabled", True)
    monkeypatch.setattr(settings, "nfo_enabled", False)
    monkeypatch.setattr(settings, "download_root", "/downloads")
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    return tmp_path


def _scaffold(db, *, info_hash=None, is_preview=False, orig="[组] Test - 05 [1080p].mkv"):
    b = Bangumi(title="测试番", season_number=1)
    db.add(b); db.flush()
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="local", save_path="/downloads/测试番")
    db.add(s); db.flush()
    ep = Episode(bangumi_id=b.id, number=5.0, type=EpisodeType.REGULAR)
    db.add(ep); db.flush()
    t = Torrent(subscription_id=s.id, guid="g1", title_raw="x", torrent_url="",
                status=TorrentStatus.ARCHIVED, info_hash=info_hash, is_batch=True,
                is_preview=is_preview, parsed_json={})
    db.add(t); db.flush()
    rel = f"测试番/{orig}"
    vf = VideoFile(torrent_id=t.id, episode_id=ep.id, relative_path=rel, is_active=True)
    db.add(vf); db.flush()
    return t, vf, rel


def test_local_import_fs_move(db, tmp_path):
    """本地导入(无 info_hash)→ 文件系统 move 进 Season NN + 记录原始名。"""
    t, vf, rel = _scaffold(db)
    (tmp_path / "测试番").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"data")

    organize.organize_torrent(db, t)

    assert vf.relative_path == "测试番/Season 01/测试番 S01E05.mkv"
    assert vf.original_name == "[组] Test - 05 [1080p].mkv"
    assert (tmp_path / vf.relative_path).read_bytes() == b"data"
    assert not (tmp_path / rel).exists()


def test_local_import_preview_to_preview_dir(db, tmp_path):
    """先行的本地导入 → 先行版/ 目录。"""
    t, vf, rel = _scaffold(db, is_preview=True)
    (tmp_path / "测试番").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"data")

    organize.organize_torrent(db, t)

    assert vf.relative_path == "测试番/先行版/测试番 S01E05.mkv"
    assert (tmp_path / vf.relative_path).exists()


def test_managed_torrent_uses_downloader(db, monkeypatch):
    """下载器托管(有 info_hash + qB)→ 走 rename_file,不碰文件系统;同样记录原始名。"""
    calls = []
    monkeypatch.setattr(organize.downloader, "rename_file",
                        lambda h, o, n: calls.append((h, o, n)))
    t, vf, rel = _scaffold(db, info_hash="abcdef00")

    organize.organize_torrent(db, t)

    assert calls == [("abcdef00", "[组] Test - 05 [1080p].mkv", "Season 01/测试番 S01E05.mkv")]
    assert vf.relative_path == "测试番/Season 01/测试番 S01E05.mkv"
    assert vf.original_name == "[组] Test - 05 [1080p].mkv"


def test_idempotent_already_organized(db, tmp_path):
    """已在 Season 里的文件再整理一次 → 不动(幂等)。"""
    t, vf, _ = _scaffold(db, orig="测试番 S01E05.mkv")
    # 直接把它放到 Season 目标位置
    vf.relative_path = "测试番/Season 01/测试番 S01E05.mkv"
    db.flush()
    (tmp_path / "测试番/Season 01").mkdir(parents=True)
    (tmp_path / vf.relative_path).write_bytes(b"data")

    organize.organize_torrent(db, t)

    assert vf.relative_path == "测试番/Season 01/测试番 S01E05.mkv"
    assert (tmp_path / vf.relative_path).exists()
