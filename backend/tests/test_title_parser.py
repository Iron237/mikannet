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
