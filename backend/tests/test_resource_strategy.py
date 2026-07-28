"""资源策略 P0/P1:覆盖语义、内部来源边界、原子换源与自动获取门控。"""
import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import bangumi as bangumi_api
from app.api import files as files_api
from app.api import subscriptions as subscriptions_api
from app.database import Base
from app.config import settings
from app.models import (AutoScanLog, Bangumi, BdRelease, Episode, EpisodeType,
                        Subscription, Torrent, TorrentStatus, VideoFile)
from app.models import TorrentEpisode
from app.services import auto_best


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def _sub(db, b, group="100", name="字幕组"):
    s = Subscription(bangumi_id=b.id, mikan_subgroup_id=group, subgroup_name=name,
                     include_keywords=["1080"], exclude_keywords=["720"],
                     pinned_guids=["old-pin"], blocked_guids=["old-block"],
                     enabled=True, save_path="/downloads/番")
    db.add(s)
    db.flush()
    return s


def _torrent(db, sub, guid):
    t = Torrent(subscription_id=sub.id, guid=guid, title_raw=guid, torrent_url="u",
                status=TorrentStatus.ARCHIVED, is_preview=False)
    db.add(t)
    db.flush()
    return t


def _ep(db, b, number):
    ep = Episode(bangumi_id=b.id, number=float(number), type=EpisodeType.REGULAR)
    db.add(ep)
    db.flush()
    return ep


def test_coverage_distinguishes_release_partial_complete_and_fallback(db):
    b = Bangumi(mikan_bangumi_id=1, title="番", eps_total=3, ep_start=1)
    db.add(b)
    db.flush()
    db.add(BdRelease(bangumi_id=b.id, title="BD", source_kind="bdrip",
                     root_path="番/BD", owned=False))
    sub = _sub(db, b)
    e1, e2 = _ep(db, b, 1), _ep(db, b, 2)
    web = _torrent(db, sub, "web")
    bd = _torrent(db, sub, "bd")
    # 同一 Web 合集:第1集被 BD 顶替,第2集仍生效 → 不能整包清理。
    db.add_all([
        VideoFile(torrent_id=web.id, episode_id=e1.id, relative_path="web/01.mkv",
                  source="Web", is_active=False),
        VideoFile(torrent_id=web.id, episode_id=e2.id, relative_path="web/02.mkv",
                  source="Web", is_active=True),
        VideoFile(torrent_id=bd.id, episode_id=e1.id, relative_path="bd/01.mkv",
                  source="BD", is_active=True),
    ])
    db.flush()

    c = bangumi_api._resource_coverage(db, b, include_cleanup=True)
    assert c["bd_status"] == "partial"
    assert c["bd"] == [1] and c["web"] == [2] and c["missing"] == [3]
    assert c["fallback_count"] == 1
    assert c["cleanup_blocked_torrents"] == 1
    assert [x["active_source"] for x in c["episodes"]] == ["BD", "Web", None]

    # 只有发行、没有 BD 文件时不能声称 BDrip 已替换。
    db.delete(db.get(VideoFile, 3))
    db.flush()
    c = bangumi_api._resource_coverage(db, b)
    assert c["bd_status"] == "release_only"


def test_library_returns_precise_bd_badge_fields(db, monkeypatch):
    monkeypatch.setattr(bangumi_api, "_file_exists", lambda _path: True)
    b = Bangumi(mikan_bangumi_id=2, title="番", eps_total=2)
    db.add(b)
    db.flush()
    sub = _sub(db, b)
    e1 = _ep(db, b, 1)
    t = _torrent(db, sub, "bd-one")
    db.add(VideoFile(torrent_id=t.id, episode_id=e1.id, relative_path="bd/01.mkv",
                     source="BD", is_active=True))
    db.flush()
    row = bangumi_api.library(db)[0]
    assert row["bd_status"] == "partial"
    assert row["bd_active_eps"] == 1
    assert row["bd_rip"] is True


def test_user_can_select_fallback_and_restore_automatic_choice(db):
    b = Bangumi(mikan_bangumi_id=20, title="版本", eps_total=1)
    db.add(b)
    db.flush()
    sub = _sub(db, b)
    ep = _ep(db, b, 1)
    web_t, bd_t = _torrent(db, sub, "web-v"), _torrent(db, sub, "bd-v")
    web = VideoFile(torrent_id=web_t.id, episode_id=ep.id, relative_path="web.mkv",
                    source="Web", is_active=False)
    bd = VideoFile(torrent_id=bd_t.id, episode_id=ep.id, relative_path="bd.mkv",
                   source="BD", is_active=True)
    db.add_all([web, bd])
    db.flush()
    files_api.activate_version(web.id, {"preferred": True}, db)
    db.refresh(web)
    db.refresh(bd)
    assert web.is_active is True and web.is_preferred is True and bd.is_active is False

    files_api.activate_version(web.id, {"preferred": False}, db)
    db.refresh(web)
    db.refresh(bd)
    assert bd.is_active is True and web.is_active is False
    assert not web.is_preferred and not bd.is_preferred


def test_subscription_list_hides_internal_by_default(db):
    b = Bangumi(mikan_bangumi_id=3, title="番")
    db.add(b)
    db.flush()
    _sub(db, b)
    db.add_all([
        Subscription(bangumi_id=b.id, mikan_subgroup_id="local", subgroup_name="本地",
                     enabled=False, save_path="/d"),
        Subscription(bangumi_id=b.id, mikan_subgroup_id="auto", subgroup_name="自动",
                     enabled=False, save_path="/d"),
    ])
    db.flush()
    assert len(subscriptions_api.list_subscriptions(False, db)) == 1
    assert len(subscriptions_api.list_subscriptions(True, db)) == 3


def test_replace_source_preserves_rules_and_history_container(db):
    b = Bangumi(mikan_bangumi_id=4, title="番")
    db.add(b)
    db.flush()
    sub = _sub(db, b, group="10", name="旧组")
    old_torrent = _torrent(db, sub, "history")
    out = subscriptions_api.replace_subscription_source(
        sub.id, {"mikan_subgroup_id": "20", "subgroup_name": "新组"},
        BackgroundTasks(), db)
    assert out.mikan_subgroup_id == "20" and out.subgroup_name == "新组"
    assert out.include_keywords == ["1080"] and out.exclude_keywords == ["720"]
    assert out.save_path == "/downloads/番"
    assert out.pinned_guids == [] and out.blocked_guids == []
    assert db.get(Torrent, old_torrent.id).subscription_id == sub.id


def test_internal_subscription_cannot_be_deleted_directly(db):
    b = Bangumi(title="本地")
    db.add(b)
    db.flush()
    sub = Subscription(bangumi_id=b.id, mikan_subgroup_id="local",
                       enabled=False, save_path="/d")
    db.add(sub)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        subscriptions_api.delete_subscription(sub.id, False, db)
    assert exc.value.status_code == 400


def test_stop_automatic_is_independent_from_owned_and_blocks_auto_scan(db):
    b = Bangumi(mikan_bangumi_id=9, title="原盘番", bd_owned=True,
                auto_download_disabled=False)
    db.add(b)
    db.flush()
    # 拥有原盘不再自动跳过;显式策略才是门控。先测试门控分支避免访问网络。
    b.auto_download_disabled = True
    result = auto_best.scan_bangumi(db, b)
    assert result["note"] == "已设置停止自动获取,跳过"


def test_auto_mode_validation_and_review_approval_is_auditable(db, monkeypatch):
    b = Bangumi(mikan_bangumi_id=30, title="审核番", auto_best=True)
    db.add(b)
    db.flush()
    out = bangumi_api.update_bangumi(b.id, {"auto_mode": "review"}, db)
    assert out["auto_mode"] == "review"

    row = AutoScanLog(
        bangumi_id=b.id, mode="review", trigger="manual",
        result_json={"pending": 1, "submitted": 0,
                     "proposals": [{"guid": "proposal-1"}]})
    db.add(row)
    db.commit()
    monkeypatch.setattr(auto_best, "_submit_candidate", lambda *_args: True)

    before = bangumi_api.auto_history(b.id, 20, db)
    assert before[0]["pending"] == 1
    result = bangumi_api.approve_auto_review(b.id, row.id, db)
    assert result["submitted"] == 1
    after = bangumi_api.auto_history(b.id, 20, db)
    assert after[0]["pending"] == 0
    assert after[0]["approved_at"]


def test_resource_issue_center_finds_stale_records_failed_tasks_and_bad_sources(db):
    b = Bangumi(mikan_bangumi_id=31, title="异常番")
    db.add(b)
    db.flush()
    sub = _sub(db, b)
    sub.last_poll_ok = False
    stale = _torrent(db, sub, "stale-archive")  # ARCHIVED 且无生效文件
    ep = _ep(db, b, 1)
    db.add(TorrentEpisode(torrent_id=stale.id, episode_id=ep.id))
    failed = Torrent(subscription_id=sub.id, guid="failed", title_raw="failed",
                     torrent_url="u", status=TorrentStatus.SUBMIT_FAILED)
    db.add(failed)
    db.flush()

    result = bangumi_api.resource_issues(verify_files=False, db=db)
    keys = {group["key"] for group in result["groups"]}
    assert {"missing_files", "failed_tasks", "subscription_errors"} <= keys
    assert result["summary"]["error"] >= 2


def test_static_batch_routes_are_declared_before_dynamic_bangumi_route():
    paths = [route.path for route in bangumi_api.router.routes]
    dynamic = paths.index("/api/bangumi/{bangumi_id}")
    assert paths.index("/api/bangumi/auto-scan") < dynamic
    assert paths.index("/api/bangumi/resource-issues") < dynamic


def test_downloaded_status_requires_the_physical_file(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_root_local", tmp_path)
    b = Bangumi(mikan_bangumi_id=32, title="尼古喵喵", eps_total=3)
    db.add(b)
    db.flush()
    sub = _sub(db, b)
    for number in (1, 2, 3):
        ep = _ep(db, b, number)
        torrent = _torrent(db, sub, f"episode-{number}")
        path = f"尼古喵喵/Season 01/S01E{number:02}.mkv"
        db.add(VideoFile(torrent_id=torrent.id, episode_id=ep.id,
                         relative_path=path, source="Web", is_active=True))
        if number == 1:
            real = tmp_path / path
            real.parent.mkdir(parents=True)
            real.write_bytes(b"episode one")
    db.flush()

    row = bangumi_api.library(db)[0]
    assert row["eps_downloaded"] == 1
    detail = bangumi_api.detail(b.id, None, db)
    by_number = {ep["number"]: ep for ep in detail["episodes"]}
    assert by_number[1.0]["status"] == "archived"
    assert by_number[2.0]["status"] == "missing"
    assert by_number[2.0]["files"] == []
    coverage = bangumi_api._resource_coverage(db, b, verify_files=True)
    assert coverage["web"] == [1]
    assert coverage["missing"] == [2, 3]
