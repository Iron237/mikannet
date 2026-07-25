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


def test_batch_deduped_by_union_of_singles(db, sub):
    """逐集下完后来合集:多个单集的并集已覆盖 → 跳过(曾因只查单种子覆盖被判「新」整季重下)。"""
    for i in (1, 2, 3):
        t = rss_engine.process_item(db, sub, item(f"s{i}", f"[字幕组] 测试番剧 - 0{i} [1080p]"))
        assert t.status == TorrentStatus.DOWNLOADING
    sub.exclude_batch = False   # 完结后 lifecycle 自动放开合集
    batch = rss_engine.process_item(db, sub, item("b1", "[字幕组] 测试番剧 [01-03 合集][1080p]"))
    assert batch.status == TorrentStatus.SKIPPED
    # 合集含未覆盖的集(04)→ 仍接受
    batch2 = rss_engine.process_item(db, sub, item("b2", "[字幕组] 测试番剧 [01-04 合集][1080p]"))
    assert batch2.status == TorrentStatus.DOWNLOADING


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


def test_ep_start_continuous_numbering(db, sub):
    """bangumi 连续编号续作(第2期章节 13-25,ep_start=13):
    字幕组随 bangumi 编号 13 → 原样落第 13 话(不误偏移);
    另一订阅按季内 01 计数 → 负偏移抬到 13,与前者同集去重。"""
    b = sub.bangumi
    b.eps_total = 13
    b.ep_start = 13
    db.flush()
    t = rss_engine.process_item(db, sub, item("g1", "[字幕组] 测试番剧 - 13 [1080p]"))
    assert t.status == TorrentStatus.DOWNLOADING
    assert sub.episode_offset == 0
    assert t.episodes[0].number == 13.0

    s2 = Subscription(bangumi_id=b.id, mikan_subgroup_id="2", save_path="/downloads/测试番剧",
                      include_keywords=[], exclude_keywords=[], exclude_batch=True)
    db.add(s2); db.flush()
    t2 = rss_engine.process_item(db, s2, item("g2", "[季内组] 测试番剧 - 01 [1080p]"))
    assert s2.episode_offset == -12
    assert t2.episodes[0].number == 13.0   # 映射到同一话(bangumi 编号)


def test_preview_and_official_separate_streams(db, sub):
    """先行 ep8 与正式 ep8 是两条独立流:互不去重,各自入库;同阶段内仍去重。"""
    prev = rss_engine.process_item(db, sub, item("p1", "[字幕组] 测试番剧 - 08 [先行版][1080p]"))
    assert prev.status == TorrentStatus.DOWNLOADING and prev.is_preview
    off = rss_engine.process_item(db, sub, item("o1", "[字幕组] 测试番剧 - 08 [1080p]"))
    assert off.status == TorrentStatus.DOWNLOADING and not off.is_preview
    # 同为先行的另一 ep8 → 与先行流去重 → 跳过
    prev2 = rss_engine.process_item(db, sub, item("p2", "[字幕组] 测试番剧 - 08 [先行版][720p]"))
    assert prev2.status == TorrentStatus.SKIPPED


def test_preview_by_air_date_fallback(db, sub):
    """无先行标记但发布明显早于官方开播日(bgm.tv air_date)→ 兜底判先行;开播后 → 正式。"""
    sub.bangumi.air_date = "2026-07-09"
    db.flush()
    early = RssItem(guid="e1", title="[字幕组] 测试番剧 - 01 [1080p]",
                    torrent_url="http://x/e1.torrent", size=100,
                    published_at=datetime(2026, 6, 1))
    assert rss_engine.process_item(db, sub, early).is_preview
    late = RssItem(guid="l1", title="[字幕组] 测试番剧 - 01 [1080p]",
                   torrent_url="http://x/l1.torrent", size=100,
                   published_at=datetime(2026, 7, 10))
    t2 = rss_engine.process_item(db, sub, late)
    assert not t2.is_preview and t2.status == TorrentStatus.DOWNLOADING


def test_version_switch_per_phase(db, sub):
    """is_active 每阶段各留一个:同集的先行文件与正式文件互不置灰。"""
    prev = rss_engine.process_item(db, sub, item("p1", "[字幕组] 测试番剧 - 08 [先行版][1080p]"))
    off = rss_engine.process_item(db, sub, item("o1", "[字幕组] 测试番剧 - 08 [1080p]"))
    ep_id = off.episodes[0].id
    fp = VideoFile(torrent_id=prev.id, episode_id=ep_id, relative_path="先行版/08.mkv")
    fo = VideoFile(torrent_id=off.id, episode_id=ep_id, relative_path="Season 01/08.mkv")
    db.add_all([fp, fo])
    db.flush()
    _apply_version_switch(db, ep_id)
    assert fp.is_active is True and fo.is_active is True


def test_manual_delete_not_revived(db, sub):
    t = rss_engine.process_item(db, sub, item("g1", "[组] 测试番剧 - 05 [1080p]"))
    t.status = TorrentStatus.SKIPPED
    t.error_message = rss_engine.MANUAL_SKIP_REASON
    db.flush()
    assert rss_engine.reevaluate_skipped(db, sub) == 0


def test_resume_resubmits_submit_failed(db, sub):
    """提交失败(无 info_hash)的种子点重试 → 异步排队重新提交(而非 qB resume,否则必 409)。"""
    from fastapi import BackgroundTasks

    from app.api import tasks
    t = Torrent(subscription_id=sub.id, guid="g-sf", title_raw="测试番剧 - 05",
                torrent_url="http://x/g-sf.torrent", status=TorrentStatus.SUBMIT_FAILED,
                parsed_json={})
    db.add(t)
    db.flush()
    bg = BackgroundTasks()
    tasks.resume(t.id, bg, db)
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is tasks._resubmit_in_background
    assert bg.tasks[0].args == (t.id,)


def test_add_torrent_idempotent_on_409(monkeypatch):
    """qB 5.x 对重复 add 返回 409 Conflict → add_torrent 按幂等成功处理,不抛异常。"""
    import qbittorrentapi
    from app.clients import qbittorrent as qb

    monkeypatch.setattr(qb, "info_hash_of", lambda b: "deadbeef")

    class _Torrents:
        def add(self, **kw):
            raise qbittorrentapi.exceptions.Conflict409Error("torrent already exists")

    class _Client:
        torrents = _Torrents()

    c = qb.QbClient()
    c._client = _Client()
    assert c.add_torrent(b"whatever", "/downloads/x") == "deadbeef"
