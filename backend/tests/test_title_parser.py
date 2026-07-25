"""标题解析回归测试:真实语料(fixtures/titles.txt)+ 关键场景用例。"""
from pathlib import Path

import pytest

from app.parsers.title_parser import parse

FIXTURES = Path(__file__).parent / "fixtures"


# ---- 关键场景 ----------------------------------------------------------------

def test_single_episode():
    p = parse("[桜都字幕组] 药屋少女的呢喃 [47][1080p][简繁内封]")
    assert p.episodes == [47.0]
    assert not p.is_batch
    assert p.resolution == "1080p"
    assert p.confidence >= 0.9


def test_v2():
    p = parse("[ANi] 某番剧 - 08v2 [1080P][Baha][WEB-DL]")
    assert p.episodes == [8.0]
    assert p.version == 2


def test_v2_tag_variants():
    """真实语料的 V2 变体:[V2修复版](后跟中文)与 [MP4]V2(前是 ]、串尾)。"""
    assert parse("[哆啦字幕组][哆啦A梦新番][215][1080P][MP4+MKV][V2修复版]").version == 2
    assert parse("[梦蓝字幕组]New Doraemon 哆啦A梦新番[830][1080P][GB_JP][MP4]V2").version == 2
    # 不误伤:v2ray 之类后跟 ASCII 字母不算版本号
    assert parse("[组] 某番剧 - 03 [v2ray教程][1080p]").version == 1


def test_batch_range():
    p = parse("【豌豆字幕组】[药屋少女的呢喃][25-48][合集][1080P][MP4]")
    assert p.is_batch
    assert p.episodes[0] == 25.0 and p.episodes[-1] == 48.0


def test_batch_range_with_cjk_suffix():
    """范围后跟汉字:[01-12修正合集] 也要解析出 1-12(此前漏判导致合集挡不住单集)。"""
    p = parse("[动漫国字幕组&LoliHouse] 孤独摇滚 / BOCCHI THE ROCK! - [01-12修正合集][WebRip 1080p HEVC-10bit AAC]")
    assert p.is_batch
    assert p.episodes[0] == 1.0 and p.episodes[-1] == 12.0


def test_batch_box():
    p = parse("[DBD-Raws][药屋少女的呢喃 第二季][13-18TV][BOX3][1080P][BDRip]")
    assert p.is_batch
    assert p.episodes[0] == 13.0 and p.episodes[-1] == 18.0


def test_star_separated():
    """P0 失败样本:六四位元字幕组星号分隔格式。"""
    p = parse("六四位元字幕组★躲在超市后门抽烟的两人 Super no Ura de Yani Suu Futari★04(abema先行版)★1920x1080★AVC AAC MP4★繁体中文")
    assert p.episodes == [4.0]
    assert p.resolution == "1080p"
    assert p.is_preview        # 标题带「先行版」→ 先行


def test_preview_detection():
    """先行(抢先/先行配信版)识别:带「先行」标记 → is_preview;正式发布无标记 → False。"""
    assert parse("[某字幕组] 某番 - 01 [先行版][1080p]").is_preview
    assert parse("[ANi] 某番 - 01 [先行配信][WEB-DL]").is_preview
    assert not parse("[某字幕组] 某番 - 01 [1080p][简繁内封]").is_preview
    # 阶段信息随 to_dict / from_dict 往返(parsed_json 缓存用)
    from app.parsers.title_parser import ParsedTitle
    assert ParsedTitle.from_dict(parse("某番 01 先行版").to_dict()).is_preview


def test_zhengli_banyun_is_batch():
    """P0 失败样本:[整理搬运] 多部合集 → 合集(集数未知)。"""
    p = parse("[整理搬运] 幸运星 (らき☆すた) (Lucky Star):TV动画+OVA篇+漫画+音乐+其他;日英音轨; 外挂简中字幕 (整理时间:2023.12.03)")
    assert p.is_batch


def test_doraemon_part():
    """P0 失败样本:[164PART1] → 第 164 集。"""
    p = parse("[哆啦字幕组][哆啦A梦新番 New Doraemon][164PART1][2009.03.20][HDTV][1080P][简繁日][MP4][修复版]")
    assert p.episodes == [164.0]


def test_chinese_episode():
    p = parse("[字幕组] 某某番剧 第08话 简体 1080P")
    assert p.episodes == [8.0]


def test_half_episode():
    p = parse("[字幕组] 某某番剧 - 12.5 [1080p]")
    assert p.episodes == [12.5]


def test_source_bd_vcb_profile():
    """VCB-Studio / Ma10p 这类无显式 BD 字样的蓝光压制 → 仍判 BD(才能顶替 Web)。"""
    p = parse("[DMG&VCB-Studio] BOCCHI THE ROCK! [01][Ma10p_1080p][x265_flac].mkv")
    assert p.source == "BD"
    assert p.episodes == [1.0]


def test_source_webrip_10bit_not_bd():
    """WebRip 的 10bit 不能误判为 BD(否则会错误顶替/压过真 BD 排序)。"""
    p = parse("[LoliHouse] 某番 - 05 [WebRip 1080p HEVC-10bit AAC].mkv")
    assert p.source == "Web"


def test_unparseable_low_confidence():
    p = parse("完全没有集数信息的一个标题")
    assert p.confidence <= 0.3
    assert p.episodes == []


# ---- 语料回归 ----------------------------------------------------------------

@pytest.mark.skipif(not (FIXTURES / "titles.txt").exists(), reason="语料未抓取")
def test_corpus_accuracy():
    titles = (FIXTURES / "titles.txt").read_text(encoding="utf-8").splitlines()
    titles = [t for t in titles if t.strip()]
    ok = sum(1 for t in titles if (r := parse(t)).episodes or r.is_batch)
    rate = ok / len(titles)
    failed = [t for t in titles if not ((r := parse(t)).episodes or r.is_batch)]
    print(f"\n语料 n={len(titles)} 通过率 {rate:.1%},失败 {len(failed)} 条:")
    for t in failed[:10]:
        print(f"  {t[:90]}")
    assert rate >= 0.95, f"通过率 {rate:.1%} 低于 95%"
