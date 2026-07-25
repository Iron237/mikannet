"""BD 发行输出 + 正片/特典判别 + 番剧生命周期(完结转补全 / 全 BD 收尾)单元测试(内存 SQLite)。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (AiringStatus, Bangumi, BdRelease, Episode, EpisodeType,
                        Subscription, Torrent, TorrentEpisode, TorrentStatus, VideoFile)
from app.services import lifecycle


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ---- BD 发行展示结构(bd_release_out)----------------------------------------
def _bdrip(db):
    b = Bangumi(mikan_bangumi_id=1, title="孤独摇滚")
    db.add(b)
    db.flush()
    r = BdRelease(bangumi_id=b.id, title="[VCB] Bocchi", source_kind="bdrip",
                  root_path="BD/[VCB] Bocchi", owned=False)
    db.add(r)
    db.flush()
    return b, r


def test_release_out_open_url_no_extras(db, monkeypatch):
    """发行输出(去特典分支):有「打开目录」URL(mikannet://reveal),不含任何特典编目字段。"""
    from app.api import bd as bd_api
    from app.config import settings
    monkeypatch.setattr(settings, "media_host_root", "Z:\\番剧\\mikannet")
    monkeypatch.setattr(settings, "launch_token", "tok")
    _, r = _bdrip(db)
    out = bd_api.bd_release_out(r)
    assert out["source_kind"] == "bdrip" and out["has_discs"] is False
    assert out["open_url"] and "reveal" in out["open_url"]   # mikannet://reveal?path=...
    for gone in ("extra_count", "image_books", "audio_albums", "videos",
                 "image_count", "video_count"):
        assert gone not in out


def test_handler_js_ascii_and_reveal_opens_dir(monkeypatch):
    """协议处理器 JS 必须纯 ASCII(否则 installer_bat 的 .encode('ascii') 抛错、重装失效),
    且 reveal 对目录直接打开(不用 /select,后者对特殊字符路径会退回资源管理器主页)。"""
    from app.config import settings
    from app.services import launch
    monkeypatch.setattr(settings, "media_host_root", "Z:\\番剧\\mikannet")
    monkeypatch.setattr(settings, "launch_token", "tok")
    js = launch._handler_js()
    js.encode("ascii")                      # 非 ASCII 会在此抛 UnicodeEncodeError
    assert "FolderExists(path)" in js       # 目录:直接打开
    assert 'explorer.exe "' in js           # 直接打开分支
    launch.installer_bat("http://localhost:9000").encode("ascii")  # 整包也必须可 ASCII 编码


# ---- 生命周期 ----------------------------------------------------------------
def _ep(db, b, n, typ=EpisodeType.REGULAR):
    e = Episode(bangumi_id=b.id, number=float(n), type=typ)
    db.add(e)
    db.flush()
    return e


def _torrent(db, sub, info_hash, ep, source, is_active, rel):
    t = Torrent(subscription_id=sub.id, guid=f"g-{info_hash}", info_hash=info_hash,
                title_raw="x", torrent_url="", status=TorrentStatus.ARCHIVED)
    db.add(t)
    db.flush()
    db.add(VideoFile(torrent_id=t.id, episode_id=ep.id, relative_path=rel,
                     source=source, is_active=is_active))
    db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
    db.flush()
    return t


def test_full_bd_finalize_deletes_web_keeps_bd(db, monkeypatch):
    deleted = []
    monkeypatch.setattr(lifecycle.downloader, "delete",
                        lambda h, delete_files: deleted.append((h, delete_files)))
    monkeypatch.setattr(lifecycle, "_notify", lambda *a, **k: None)
    b = Bangumi(mikan_bangumi_id=1, title="番", eps_total=2, auto_best=True,
                airing_status=AiringStatus.FINISHED)
    db.add(b)
    db.flush()
    rss = Subscription(bangumi_id=b.id, mikan_subgroup_id="123", subgroup_name="组",
                       enabled=True, save_path="/d")
    auto = Subscription(bangumi_id=b.id, mikan_subgroup_id="auto", enabled=False, save_path="/d")
    db.add_all([rss, auto])
    db.flush()
    e1, e2 = _ep(db, b, 1), _ep(db, b, 2)
    wt1 = _torrent(db, rss, "webhash1", e1, "Web", False, "web/01.mkv")
    wt2 = _torrent(db, rss, "webhash2", e2, "Web", False, "web/02.mkv")
    bd1 = _torrent(db, auto, "bdhash1", e1, "BD", True, "bd/01.mkv")
    _torrent(db, auto, "bdhash2", e2, "BD", True, "bd/02.mkv")

    assert lifecycle.bd_complete(db, b) is True
    assert lifecycle.finalize_complete(db, b) is True
    assert b.auto_best is False                  # 停定期扫
    db.refresh(rss)
    assert rss.enabled is False                  # 停真订阅
    assert {h for h, _ in deleted} == {"webhash1", "webhash2"}   # 删 web,且带删文件
    assert all(df for _, df in deleted)
    assert db.get(Torrent, wt1.id) is None and db.get(Torrent, wt2.id) is None
    assert db.get(Torrent, bd1.id) is not None   # BD 种子保留
    # 幂等:再调一次无动作
    assert lifecycle.finalize_complete(db, b) is False


def test_finalize_noop_when_not_all_bd(db, monkeypatch):
    monkeypatch.setattr(lifecycle, "_notify", lambda *a, **k: None)
    b = Bangumi(mikan_bangumi_id=1, title="番", eps_total=2, auto_best=True)
    db.add(b)
    db.flush()
    rss = Subscription(bangumi_id=b.id, mikan_subgroup_id="1", enabled=True, save_path="/d")
    db.add(rss)
    db.flush()
    e1, e2 = _ep(db, b, 1), _ep(db, b, 2)
    _torrent(db, rss, "bd1", e1, "BD", True, "bd/01.mkv")   # 只有第 1 集是 BD
    _torrent(db, rss, "web2", e2, "Web", True, "web/02.mkv")
    assert lifecycle.bd_complete(db, b) is False
    assert lifecycle.finalize_complete(db, b) is False
    assert b.auto_best is True                   # 未完成 → 不动


def test_evaluate_airing_flips_on_eps_downloaded(db, monkeypatch):
    monkeypatch.setattr(lifecycle, "_notify", lambda *a, **k: None)
    b = Bangumi(mikan_bangumi_id=2, title="新番", eps_total=2,
                airing_status=AiringStatus.AIRING, auto_best=False)
    db.add(b)
    db.flush()
    rss = Subscription(bangumi_id=b.id, mikan_subgroup_id="9", enabled=True,
                       exclude_batch=True, save_path="/d")
    db.add(rss)
    db.flush()
    e1, e2 = _ep(db, b, 1), _ep(db, b, 2)
    _torrent(db, rss, "w1", e1, "Web", True, "w/01.mkv")
    _torrent(db, rss, "w2", e2, "Web", True, "w/02.mkv")
    assert lifecycle.evaluate_airing(db, b) is True
    assert b.airing_status == AiringStatus.FINISHED and b.auto_best is True
    db.refresh(rss)
    assert rss.exclude_batch is False            # 完结后放开合集


def test_evaluate_airing_skips_without_mikan_id(db, monkeypatch):
    monkeypatch.setattr(lifecycle, "_notify", lambda *a, **k: None)
    b = Bangumi(title="本地导入无蜜柑", eps_total=2, airing_status=AiringStatus.AIRING)
    db.add(b)
    db.flush()
    assert lifecycle.evaluate_airing(db, b) is False   # 无蜜柑 ID 不转补全


# ---- BD 正片/特典判别(纯集号 vs 描述标签;不靠 web 集号匹配)--------------------
@pytest.mark.parametrize("name,is_extra", [
    # 正片:中间方括号是纯集号
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [01][Ma10p_1080p][x265_aac].mkv", False),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [12][Ma10p_1080p][x265_aac].mkv", False),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [12.5][Ma10p_1080p][x265_aac].mkv", False),
    # 特典:中间方括号带描述标签(截图实例)——绝不能当正片
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [BOCCHI THE TALK! 01][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [Lyric Video 02][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [Menu01][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [NCED01][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [Character PV 02][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [IV][Ma10p_1080p][x265_flac].mkv", True),
    ("[DMG&VCB-Studio] BOCCHI THE ROCK! [Road to Guitar Hero 01][Ma10p_1080p][x265_flac].mkv", True),
    # DBD-Raws 式:作品名在方括号内,标题方括号不得误当特典(否则正片被剔)
    ("[DBD-Raws][深夜重拳][01][1080P][BDRip][HEVC-10bit][FLAC].mkv", False),
    ("[DBD-Raws][深夜重拳][12][1080P][BDRip][HEVC-10bit][FLAC].mkv", False),
    ("[DBD-Raws][Mayonaka Punch][After Broadcasting Short Drama][01][1080P][BDRip][HEVC-10bit][FLAC].mkv", True),
    ("[DBD-Raws][深夜重拳][NCOP][1080P][BDRip][HEVC-10bit][FLAC].mkv", True),
])
def test_bd_video_discriminator(name, is_extra):
    from app.services.bd_scan import bd_is_extra_video
    assert bd_is_extra_video(name) is is_extra


def test_heal_bd_extras_removes_mismapped_keeps_main(db):
    """全局自愈:BD 番剧里被误登记成正片的特典(任意来源/嵌套)移出,正片保留。锁 深夜重拳 回归。"""
    from app.services.library_scan import _heal_bd_extras
    b = Bangumi(mikan_bangumi_id=9, title="深夜重拳", eps_total=12)
    db.add(b)
    db.flush()
    db.add(BdRelease(bangumi_id=b.id, title="rel", source_kind="bdrip",
                     root_path="深夜重拳/rel", owned=False))      # 该番有 BD 发行 → BD 上下文
    sub = Subscription(bangumi_id=b.id, mikan_subgroup_id="local", enabled=False, save_path="/d")
    db.add(sub)
    db.flush()
    t = Torrent(subscription_id=sub.id, guid="library:9", title_raw="x", torrent_url="",
                status=TorrentStatus.ARCHIVED)
    db.add(t)
    db.flush()
    e1 = Episode(bangumi_id=b.id, number=1.0, type=EpisodeType.REGULAR)
    db.add(e1)
    db.flush()
    main = VideoFile(torrent_id=t.id, episode_id=e1.id, source="BD", is_active=True,
                     relative_path="深夜重拳/rel/[DBD-Raws][深夜重拳][01][1080P][BDRip].mkv")
    extra = VideoFile(torrent_id=t.id, episode_id=e1.id, source="BD", is_active=True,
                      relative_path="深夜重拳/rel/短剧/[DBD-Raws][Mayonaka Punch]"
                                    "[After Broadcasting Short Drama][01][1080P][BDRip].mkv")
    db.add_all([main, extra])
    db.flush()
    assert _heal_bd_extras(db) == 1
    assert db.get(VideoFile, extra.id) is None     # 短剧特典移出剧集
    assert db.get(VideoFile, main.id) is not None  # 纯集号正片保留


# ---- 正片导入向导(手动指定 / 自动匹配,对发行权威)----------------------------
def test_import_candidates_requires_binding(db):
    """未绑定番剧的发行不能导入正片(向导先要求绑定)。"""
    from fastapi import HTTPException

    from app.api.bd import import_candidates
    r = BdRelease(title="rel", source_kind="bdrip", root_path="x/rel", owned=False)
    db.add(r)
    db.flush()
    with pytest.raises(HTTPException):
        import_candidates(r.id, db)


def test_import_main_authoritative(db, monkeypatch):
    """导入向导对该发行权威:选中→映射为正片;未选的已登记 BD 文件移出网格;并标 manual_import。"""
    monkeypatch.setattr("app.services.lifecycle.on_torrent_processed", lambda *a, **k: None)
    from app.api.bd import import_main
    from app.services.local_import import LOCAL_SUBGROUP_ID
    b = Bangumi(mikan_bangumi_id=5, title="番", eps_total=2)
    db.add(b)
    db.flush()
    r = BdRelease(bangumi_id=b.id, title="rel", source_kind="bdrip",
                  root_path="番/rel", owned=False)
    sub = Subscription(bangumi_id=b.id, mikan_subgroup_id=LOCAL_SUBGROUP_ID,
                       enabled=False, save_path="/d")
    db.add_all([r, sub])
    db.flush()
    t = Torrent(subscription_id=sub.id, guid="library:5", title_raw="x", torrent_url="",
                status=TorrentStatus.ARCHIVED)
    db.add(t)
    db.flush()
    e1 = Episode(bangumi_id=b.id, number=1.0, type=EpisodeType.REGULAR)
    db.add(e1)
    db.flush()
    f_main = VideoFile(torrent_id=t.id, episode_id=e1.id, source="BD", is_active=True,
                       relative_path="番/rel/[VCB] 番 [01].mkv")
    f_bogus = VideoFile(torrent_id=t.id, episode_id=e1.id, source="BD", is_active=False,
                        relative_path="番/rel/[VCB] 番 [Menu01].mkv")
    db.add_all([f_main, f_bogus])
    db.flush()
    res = import_main(r.id, {"assignments": [
        {"path": "番/rel/[VCB] 番 [01].mkv", "episode_number": 1}]}, db)
    assert res["removed"] == 1 and res["remapped"] == 1
    assert db.get(VideoFile, f_bogus.id) is None      # 未选为正片 → 移出剧集网格
    assert db.get(VideoFile, f_main.id) is not None   # 选中 → 保留为正片
    db.refresh(r)
    assert r.manual_import is True                     # 标记后库扫描不再自动改这套
