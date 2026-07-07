"""bgm.tv 每集同步:编号/标题/放送日落库、ep_start 回填、延期检测、不重复建行。"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.bgmtv import BgmtvEpisode
from app.database import Base
from app.models import Bangumi, Episode, EpisodeType
from app.services import bgm_sync


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ep(ep_id, sort, name_cn=None, airdate=None):
    return BgmtvEpisode(ep_id=ep_id, type=0, sort=float(sort),
                        name=f"第{sort}話", name_cn=name_cn, airdate=airdate)


def test_sync_creates_and_backfills(db, monkeypatch):
    """续作章节 13-15:建行(bangumi 编号)、回填 ep_start、写中文标题与放送日。"""
    b = Bangumi(title="测试续作", bgmtv_subject_id=100)
    db.add(b); db.flush()
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes", lambda sid, ep_type=0: [
        _ep(9001, 13, "十三话", "2026-07-05"),
        _ep(9002, 14, "十四话", "2026-07-12"),
        _ep(9003, 15, None, "2026-07-19"),
    ])
    changed = bgm_sync.sync_bgmtv_episodes(db, b)
    assert changed == []                      # 首次填充不算变动
    assert b.ep_start == 13
    eps = db.query(Episode).order_by(Episode.number).all()
    assert [e.number for e in eps] == [13.0, 14.0, 15.0]
    assert eps[0].title == "十三话" and eps[0].air_date == "2026-07-05"
    assert eps[0].bgmtv_ep_id == 9001
    assert eps[2].title == "第15話"           # 无中文名退原名


def test_sync_upserts_existing_and_detects_delay(db, monkeypatch):
    """已有下载建的裸集(同编号)→ 回填不重复建行;未来集放送日变动 → 报延期。"""
    future = (date.today() + timedelta(days=10)).isoformat()
    future2 = (date.today() + timedelta(days=17)).isoformat()
    b = Bangumi(title="测试", bgmtv_subject_id=101)
    db.add(b); db.flush()
    db.add(Episode(bangumi_id=b.id, type=EpisodeType.REGULAR, number=1.0))  # 下载先建的裸集
    db.flush()
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes",
                        lambda sid, ep_type=0: [_ep(8001, 1, "一话", future)])
    assert bgm_sync.sync_bgmtv_episodes(db, b) == []
    assert db.query(Episode).count() == 1     # 回填,不重复
    # 延期:同一集换新日期
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes",
                        lambda sid, ep_type=0: [_ep(8001, 1, "一话", future2)])
    changed = bgm_sync.sync_bgmtv_episodes(db, b)
    assert changed == [{"number": 1.0, "old": future, "new": future2}]
    assert db.query(Episode).one().air_date == future2


def test_shift_legacy_seasonal_numbering(db, monkeypatch):
    """ep_start 上线前的存量:剧集按季内 1-based、订阅带正偏移 →
    同步时整体平移到 bangumi 编号(行复用不分裂),偏移矫正为 0;再跑幂等。"""
    from app.models import Subscription
    b = Bangumi(title="芙莉莲S2式", bgmtv_subject_id=200, eps_total=3)
    db.add(b); db.flush()
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="1", save_path="/d/x",
                     episode_offset=28)   # 字幕组连续编号 29..,旧逻辑拉回 1-based
    db.add(s)
    old_rows = []
    for n in (1, 2, 3):
        ep = Episode(bangumi_id=b.id, type=EpisodeType.REGULAR, number=float(n))
        db.add(ep); db.flush()
        old_rows.append(ep.id)
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes", lambda sid, ep_type=0: [
        _ep(6001, 29, "二九", "2026-01-05"), _ep(6002, 30, "三零", "2026-01-12"),
        _ep(6003, 31, "三一", "2026-01-19")])
    bgm_sync.sync_bgmtv_episodes(db, b)
    assert b.ep_start == 29
    assert s.episode_offset == 0                       # 29 - 0 = 29 ✓
    rows = db.query(Episode).order_by(Episode.number).all()
    assert [e.number for e in rows] == [29.0, 30.0, 31.0]   # 平移,不分裂
    assert [e.id for e in rows] == old_rows            # 复用原行(文件关联不丢)
    assert rows[0].title == "二九" and rows[0].bgmtv_ep_id == 6001
    bgm_sync.sync_bgmtv_episodes(db, b)                # 幂等
    assert db.query(Episode).count() == 3


def test_shift_merges_mixed_numbering(db, monkeypatch):
    """历史混编:同一集 RSS 存第 2 话、auto 存第 30 话(两套编号并存)→
    平移撞号时合并:文件关联并入 bangumi 编号行,旧行删除,按画质重选 active。"""
    from app.models import Subscription, Torrent, TorrentStatus, VideoFile
    b = Bangumi(title="混编", bgmtv_subject_id=201, eps_total=2)
    db.add(b); db.flush()
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="1", save_path="/d/x",
                     episode_offset=28)
    db.add(s); db.flush()
    t = Torrent(subscription_id=s.id, guid="g", title_raw="x", torrent_url="",
                status=TorrentStatus.ARCHIVED, parsed_json={})
    db.add(t); db.flush()
    legacy = Episode(bangumi_id=b.id, type=EpisodeType.REGULAR, number=2.0)   # RSS 建的
    canon = Episode(bangumi_id=b.id, type=EpisodeType.REGULAR, number=30.0)   # auto 建的
    db.add_all([legacy, canon]); db.flush()
    f_web = VideoFile(torrent_id=t.id, episode_id=legacy.id,
                      relative_path="x/02.mkv", is_active=True, source="Web")
    f_bd = VideoFile(torrent_id=t.id, episode_id=canon.id,
                     relative_path="x/30-bd.mkv", is_active=True, source="BD")
    db.add_all([f_web, f_bd]); db.flush()

    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes", lambda sid, ep_type=0: [
        _ep(5001, 29), _ep(5002, 30)])
    bgm_sync.sync_bgmtv_episodes(db, b)

    nums = sorted(e.number for e in db.query(Episode).all())
    assert nums == [29.0, 30.0]                       # 旧第 2 话并入第 30 话,不再并存
    assert f_web.episode_id == canon.id               # 文件关联迁到目标行
    assert f_bd.is_active is True and f_web.is_active is False   # 画质重选:BD 留,Web 置灰


def test_build_series_whitelist_and_order(monkeypatch):
    """系列链:沿白名单关系 BFS(角色出演/总集篇不进),按放送日期排序。"""
    from app.clients.bgmtv import BgmtvSubject, RelatedSubject
    REL = {
        100: [RelatedSubject(101, 2, "续集", "S2", "系列 第二季", None),
              RelatedSubject(300, 2, "角色出演", "客串", "客串作", None)],   # 噪声,不进
        101: [RelatedSubject(100, 2, "前传", "S1", "系列", None),
              RelatedSubject(102, 2, "衍生", "Movie", "系列 剧场版", None),
              RelatedSubject(301, 2, "总集篇", "recap", "总集篇", None)],   # 噪声,不进
        102: [RelatedSubject(101, 2, "主线故事", "S2", "系列 第二季", None)],
    }
    SUBJ = {100: ("系列", "2024-01-01"), 101: ("系列 第二季", "2025-01-01"),
            102: ("系列 剧场版", "2025-07-01")}
    monkeypatch.setattr(bgm_sync.bgmtv_client, "related_subjects", lambda sid: REL.get(sid, []))
    monkeypatch.setattr(bgm_sync.bgmtv_client, "get_subject", lambda sid: BgmtvSubject(
        sid, SUBJ[sid][0], SUBJ[sid][0], SUBJ[sid][1], "TV", 12, None, None, None, None))
    monkeypatch.setattr(bgm_sync, "sleep", lambda *_: None, raising=False)
    chain = bgm_sync.build_series(101)   # 从中间一部出发也能拉全
    assert [x["subject_id"] for x in chain] == [100, 101, 102]   # 按日期序
    assert all(x["subject_id"] not in (300, 301) for x in chain)


def test_series_labels_strip_common_prefix():
    labels = bgm_sync.series_labels(["相反的你和我", "相反的你和我 第二季"])
    assert labels == ["相反的你和我", "第二季"]   # 去前缀;去空回退全名
    assert bgm_sync.series_labels(["单独一部"]) == ["单独一部"]
    assert bgm_sync.series_labels(["AB", "CD"]) == ["AB", "CD"]   # 前缀太短不去


def test_past_episode_date_correction_not_reported(db, monkeypatch):
    """过去集的日期修正(资料订正)不算延期,不提醒。"""
    b = Bangumi(title="测试", bgmtv_subject_id=102)
    db.add(b); db.flush()
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes",
                        lambda sid, ep_type=0: [_ep(7001, 1, None, "2020-01-01")])
    bgm_sync.sync_bgmtv_episodes(db, b)
    monkeypatch.setattr(bgm_sync.bgmtv_client, "episodes",
                        lambda sid, ep_type=0: [_ep(7001, 1, None, "2020-01-08")])
    assert bgm_sync.sync_bgmtv_episodes(db, b) == []
