"""状态机单元测试:过滤/去重/v2/合集/规则复活(内存 SQLite + mock 外部依赖)。"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.mikan import RssItem
from app.database import Base
from app.models import Bangumi, Subscription, Torrent, TorrentStatus, VideoFile
from app.services import rss_engine
from app.services.postprocess import _apply_version_switch


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def sub(db):
    b = Bangumi(mikan_bangumi_id=999, title="测试番剧")
    db.add(b)
    db.flush()
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="1", save_path="/downloads/测试番剧",
                     include_keywords=[], exclude_keywords=[], exclude_batch=True)
    db.add(s)
    db.flush()
    return s


@pytest.fixture(autouse=True)
def mock_submit(monkeypatch):
    """提交下载器一律成功,info_hash 用计数器凑。"""
    monkeypatch.setattr(rss_engine.mikan_client, "download_torrent", lambda url: b"fake")
    counter = iter(range(10000))
    monkeypatch.setattr(rss_engine.downloader, "add_torrent",
                        lambda data, save_path: f"hash{next(counter):04d}")


def item(guid, title):
    return RssItem(guid=guid, title=title, torrent_url=f"http://x/{guid}.torrent",
                   size=100, published_at=datetime(2026, 1, 1))


def test_accept_and_dedup(db, sub):
    t1 = rss_engine.process_item(db, sub, item("g1", "[字幕组] 测试番剧 - 08 [1080p]"))
    assert t1.status == TorrentStatus.DOWNLOADING
    # 同集另一来源(同订阅)→ 跳过
    t2 = rss_engine.process_item(db, sub, item("g2", "[字幕组] 测试番剧 - 08 [720p]"))
    assert t2.status == TorrentStatus.SKIPPED
    # 同 guid → 不入库
    assert rss_engine.process_item(db, sub, item("g1", "随便")) is None


def test_v2_accepted_and_file_switch(db, sub):
    t1 = rss_engine.process_item(db, sub, item("g1", "[字幕组] 测试番剧 - 08 [1080p]"))
    t2 = rss_engine.process_item(db, sub, item("g2", "[字幕组] 测试番剧 - 08v2 [1080p]"))
    assert t2.status == TorrentStatus.DOWNLOADING and t2.version == 2
    # v3 之后 v2 再来 → 跳过
    t3 = rss_engine.process_item(db, sub, item("g3", "[字幕组] 测试番剧 - 08v2 [1080p]"))
    assert t3.status == TorrentStatus.SKIPPED

    # 文件层 is_active 切换
    ep_id = t1.episodes[0].id
    f1 = VideoFile(torrent_id=t1.id, episode_id=ep_id, relative_path="a/08.mkv")
    f2 = VideoFile(torrent_id=t2.id, episode_id=ep_id, relative_path="a/08v2.mkv")
    db.add_all([f1, f2])
    db.flush()
    _apply_version_switch(db, ep_id)
    assert f1.is_active is False and f2.is_active is True


def test_batch_excluded_then_allowed(db, sub):
    t = rss_engine.process_item(db, sub, item("g1", "[字幕组] 测试番剧 [01-12 合集][1080p]"))
    assert t.status == TorrentStatus.SKIPPED and t.is_batch

    sub.exclude_batch = False
    revived = rss_engine.reevaluate_skipped(db, sub)
    assert revived == 1
    db.refresh(t)
    assert t.status == TorrentStatus.DOWNLOADING
    assert len(t.episodes) == 12   # 合集展开 01-12


def test_filter_keywords(db, sub):
    sub.include_keywords = ["1080"]
    sub.exclude_keywords = ["720"]
    ok = rss_engine.process_item(db, sub, item("g1", "[组] 测试 - 01 [1080p]"))
    no_inc = rss_engine.process_item(db, sub, item("g2", "[组] 测试 - 02 [480p]"))
    assert ok.status == TorrentStatus.DOWNLOADING
    assert no_inc.status == TorrentStatus.SKIPPED


def test_episode_offset_autodetect(db, sub):
    """跨季连续编号:总集数 24、来 25 话 → 自动偏移 24,剧集落在第 1 话。"""
    sub.bangumi.eps_total = 24
    db.flush()
    t = rss_engine.process_item(db, sub, item("g1", "[字幕组] 测试番剧 - 25 [1080p]"))
    assert sub.episode_offset == 24
    assert t.episodes[0].number == 1.0
    # 后续 26 话 → 第 2 话
    t2 = rss_engine.process_item(db, sub, item("g2", "[字幕组] 测试番剧 - 26 [1080p]"))
    assert t2.episodes[0].number == 2.0


def test_manual_delete_not_revived(db, sub):
    t = rss_engine.process_item(db, sub, item("g1", "[组] 测试番剧 - 05 [1080p]"))
    t.status = TorrentStatus.SKIPPED
    t.error_message = rss_engine.MANUAL_SKIP_REASON
    db.flush()
    assert rss_engine.reevaluate_skipped(db, sub) == 0
