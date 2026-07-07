"""先行/正式阶段自动判定:开播前内容归先行、先行不触发完结误判(内存 SQLite)。"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (AiringStatus, Bangumi, Episode, EpisodeType, Subscription,
                        Torrent, TorrentStatus, VideoFile)
from app.services.phase import before_official_air


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_before_official_air():
    assert before_official_air(_d(30)) is True        # 官方开播还早 → 先行期
    assert before_official_air(_d(3)) is True
    assert before_official_air(_d(2)) is False        # margin 内不判(时区/当天误差)
    assert before_official_air(_d(-10)) is False      # 已开播
    assert before_official_air(None) is False         # 判不了当正式
    assert before_official_air("垃圾数据") is False


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _bangumi_with_files(db, *, air_date: str, is_preview: bool):
    """2 集番剧,两集都有 active 文件(挂在一个容器种子上)。"""
    b = Bangumi(mikan_bangumi_id=888, title="先行测试番", eps_total=2, air_date=air_date,
                airing_status=AiringStatus.AIRING)
    db.add(b); db.flush()
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="local", subgroup_name="本地导入",
                     enabled=False, save_path="/downloads/先行测试番")
    db.add(s); db.flush()
    t = Torrent(subscription_id=s.id, guid="library:888", title_raw="容器", parsed_json={},
                torrent_url="", is_batch=True, is_preview=is_preview,
                status=TorrentStatus.ARCHIVED)
    db.add(t); db.flush()
    for n in (1, 2):
        ep = Episode(bangumi_id=b.id, number=float(n), type=EpisodeType.REGULAR)
        db.add(ep); db.flush()
        db.add(VideoFile(torrent_id=t.id, episode_id=ep.id,
                         relative_path=f"先行测试番/{n:02d}.mkv", is_active=True))
    db.flush()
    return b


def test_preview_full_set_does_not_finish(db):
    """官方未开播 + 先行下满全集 → 绝不判完结(曾被「下满 eps_total」误判)。"""
    from app.services.lifecycle import evaluate_airing
    b = _bangumi_with_files(db, air_date=_d(10), is_preview=True)
    assert evaluate_airing(db, b) is False
    assert b.airing_status == AiringStatus.AIRING


def test_official_full_set_finishes(db):
    """对照:已开播 + 正式流下满全集 → 正常判完结。"""
    from app.services.lifecycle import evaluate_airing
    b = _bangumi_with_files(db, air_date=_d(-30), is_preview=False)
    assert evaluate_airing(db, b) is True
    assert b.airing_status == AiringStatus.FINISHED


def test_library_container_preview_before_air(db, monkeypatch):
    """库扫描容器:官方开播前 → 独立先行容器(library-preview + is_preview)。"""
    from app.services import library_scan
    b = Bangumi(mikan_bangumi_id=999, title="容器测试番", air_date=_d(10))
    db.add(b); db.flush()
    t = library_scan._container_torrent(db, b)
    assert t.guid == "library-preview:999"
    assert t.is_preview is True
    # 开播后再扫 → 另建正式容器,两阶段并存
    b.air_date = _d(-1)
    t2 = library_scan._container_torrent(db, b)
    assert t2.guid == "library:999"
    assert t2.is_preview is False
    assert t2.id != t.id
