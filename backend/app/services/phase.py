"""先行/正式阶段公共判定。

bgm.tv 不建模「先行放送」(章节表只记正式放送),但「放送开始」(air_date)本身就是
严谨判据:**官方开播日之前出现的任何内容必然是先行**。RSS 路径按条目发布时间判
(rss_engine._is_preview);本地导入/库扫描没有发布时间,以「导入那一刻」对照开播日,
共用本模块。margin 与 RSS 路径一致,避开时区/开播当天的误差。
"""
from __future__ import annotations

from datetime import date

MARGIN_DAYS = 2


def before_official_air(air_date: str | None, margin_days: int = MARGIN_DAYS) -> bool:
    """现在是否仍在官方开播日之前(差距 > margin 天)→ 此刻出现的内容判先行。

    air_date 缺失/不可解析 → False(判不了就当正式,不误伤普通番)。
    """
    if not air_date:
        return False
    try:
        start = date.fromisoformat(air_date[:10].replace("/", "-"))
    except ValueError:
        return False
    return (start - date.today()).days > margin_days
