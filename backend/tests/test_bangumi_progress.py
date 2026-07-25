"""放送进度:_eps_aired(已播/已发布集数)+ _weekly_aired 回归(内存 SQLite)。

覆盖:RSS 最大集号(含 SKIPPED 留痕)、首播日+周更兜底、**已下载下限**(杜绝已播<已下载)、
封顶 eps_total、非 TV/无总集数 → None。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.bangumi import _eps_aired, _weekly_aired
from app.database import Base
from app.models import (Bangumi, Episode, EpisodeType, Kind, Subscription, Torrent,
                        TorrentStatus, VideoFile)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _bangumi(db, **kw):
    kw.setdefault("kind", Kind.TV)
    kw.setdefault("eps_total", 12)
    b = Bangumi(title="测试番", **kw)
    db.add(b)
    db.flush()
    return b


def _sub(db, b):
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id="g1", save_path="/dl/x")
    db.add(s)
    db.flush()
    return s


def _torrent(db, sub, episodes, guid, status=TorrentStatus.PENDING):
    t = Torrent(subscription_id=sub.id, guid=guid, title_raw="t", torrent_url="u",
                parsed_json={"episodes": episodes}, status=status)
    db.add(t)
    db.flush()
    return t


def _downloaded(db, b, sub, count):
    """造 count 个有 active 文件的正片集(_eps_done 据此计数)。"""
    t = _torrent(db, sub, [], guid=f"dl-{b.id}")
    for n in range(1, count + 1):
        e = Episode(bangumi_id=b.id, number=float(n), type=EpisodeType.REGULAR)
        db.add(e)
        db.flush()
        db.add(VideoFile(torrent_id=t.id, episode_id=e.id,
                         relative_path=f"x/{n}.mkv", is_active=True))
    db.flush()


# ---- _weekly_aired(纯函数)----------------------------------------------------
def test_weekly_aired():
    assert _weekly_aired(None) == 0
    assert _weekly_aired((date.today() + timedelta(days=7)).isoformat()) == 0   # 未开播
    assert _weekly_aired(date.today().isoformat()) == 1                          # 首播当天 = 第1集
    assert _weekly_aired((date.today() - timedelta(days=14)).isoformat()) == 3   # 2 周 → 3 集
    assert _weekly_aired("not-a-date") == 0


# ---- _eps_aired --------------------------------------------------------------
def test_aired_rss_max_includes_skipped(db):
    """已播 = 所有种子(含 SKIPPED 留痕)parsed_json 最大集号。"""
    b = _bangumi(db)
    sub = _sub(db, b)
    _torrent(db, sub, [1, 2, 3], guid="a", status=TorrentStatus.PENDING)
    _torrent(db, sub, [4, 5], guid="b", status=TorrentStatus.SKIPPED)   # 留痕也算
    assert _eps_aired(db, b) == 5


def test_aired_floor_at_downloaded(db):
    """无种子/无首播日,但已下载 4 集 → 已播以已下载为下限(不得 < 已下载)。"""
    b = _bangumi(db, air_date=None)
    sub = _sub(db, b)
    _downloaded(db, b, sub, 4)            # 该 holder 种子 episodes=[],RSS=0
    assert _eps_aired(db, b) == 4


def test_aired_weekly_fallback(db):
    """没见过种子 → 按首播日 + 周更推算。"""
    b = _bangumi(db, air_date=(date.today() - timedelta(days=14)).isoformat())
    _sub(db, b)
    assert _eps_aired(db, b) == 3


def test_aired_capped_to_total(db):
    """解析出的离谱大集号封顶到 eps_total。"""
    b = _bangumi(db, eps_total=12)
    sub = _sub(db, b)
    _torrent(db, sub, [99], guid="x")
    assert _eps_aired(db, b) == 12


def test_aired_none_for_non_tv_or_no_total(db):
    assert _eps_aired(db, _bangumi(db, kind=Kind.MOVIE)) is None
    assert _eps_aired(db, _bangumi(db, eps_total=None)) is None
