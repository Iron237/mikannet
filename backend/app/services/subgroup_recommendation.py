"""按本番的实际发布记录比较字幕组。相同集数的修订、不同封装只计一次。"""
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from app.config import settings
from app.parsers.title_parser import detect_subtitle_tags, parse


def matches_language(tags: list[str], preference: str) -> bool:
    if "简" in preference:
        return any("简" in tag for tag in tags)
    if "繁" in preference:
        return any("繁" in tag for tag in tags)
    return True


def publication_order(published: str | None) -> int:
    match = re.search(r"(\d{4})[/\-年.](\d{1,2})[/\-月.](\d{1,2})", published or "")
    if not match:
        return 0
    year, month, day = map(int, match.groups())
    return year * 10000 + month * 100 + day


def summarize_groups(groups, *, today: date | None = None) -> list[tuple]:
    resolution = settings.auto_dl_resolution or "1080p"
    language = (settings.auto_dl_sub_lang or "简中").strip()
    if "简" not in language and "繁" not in language:
        language = "字幕不限"
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    start = int((today - timedelta(days=27)).strftime("%Y%m%d"))
    end = int(today.strftime("%Y%m%d"))
    ranked = []
    for group in groups:
        resolutions, languages, sources = Counter(), Counter(), Counter()
        covered, singles, bd = set(), set(), set()
        recent_singles = {}
        latest = 0
        has_batch = False
        for release in group.torrents:
            parsed = parse(release.title)
            tags = detect_subtitle_tags(release.title)
            languages.update(tags)
            if parsed.resolution:
                resolutions[parsed.resolution] += 1
            if parsed.source:
                sources[parsed.source] += 1
            has_batch |= parsed.is_batch
            if (not release.torrent_url or parsed.is_preview or parsed.ep_type != "regular"
                    or parsed.resolution != resolution or not matches_language(tags, language)):
                continue
            episodes = {int(n) for n in parsed.episodes if n >= 1 and float(n).is_integer()}
            if not episodes:
                continue
            covered.update(episodes)
            published = publication_order(release.published)
            if not parsed.is_batch and len(episodes) == 1:
                singles.update(episodes)
                if start <= published <= end:
                    number = next(iter(episodes))
                    recent_singles[number] = max(published, recent_singles.get(number, 0))
            if parsed.source == "BD":
                bd.update(episodes)
            latest = max(latest, published)
        caps = {"resolutions": [name for name, _ in resolutions.most_common()],
                "subtitle_langs": [name for name, _ in languages.most_common()],
                "sources": [name for name, _ in sources.most_common()], "has_batch": has_batch}
        rank = (len(covered), len(singles), len(bd) if settings.auto_dl_prefer_bd else 0, latest)
        ongoing = (len(recent_singles), max(recent_singles.values(), default=0))
        summary = {"resolution": resolution, "language": language, "episodes": len(covered),
                   "singles": len(singles), "recent_singles": len(recent_singles), "bd": len(bd),
                   "latest": f"{latest // 10000:04}-{latest // 100 % 100:02}-{latest % 100:02}" if latest else None}
        ranked.append((rank, ongoing, group, caps, summary))
    best_coverage = max((r[0] for r in ranked), default=None)
    best_ongoing = max((r[1] for r in ranked), default=None)
    results = []
    for rank, ongoing, group, caps, summary in ranked:
        summary["top"] = bool(rank[0]) and rank == best_coverage
        summary["ongoing"] = bool(ongoing[0]) and ongoing == best_ongoing
        results.append(((summary["ongoing"], summary["top"], rank), group, caps, summary))
    results.sort(key=lambda row: row[0], reverse=True)
    return [(group, caps, summary) for _, group, caps, summary in results]
