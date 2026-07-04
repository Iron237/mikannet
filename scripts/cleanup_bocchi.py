"""清理误提交的单集任务,只留 LoliHouse 合集;并用修复后的解析器重写合集行。

直接操作生产 DB(data/mikannet)+ 经 API 删任务(标记手动删除防复活)。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["MIKANNET_DATA_DIR"] = str(ROOT / "data" / "mikannet")
os.environ["MIKANNET_DOWNLOAD_ROOT_LOCAL"] = "/downloads"
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from sqlalchemy import select

from app.database import db_session
from app.models import Episode, EpisodeType, Torrent, TorrentEpisode, TorrentStatus
from app.parsers.title_parser import parse

api = httpx.Client(base_url="http://127.0.0.1:8008", timeout=60, trust_env=False)

# 1) 重解析所有非淘汰的合集行(修复前集数为空)
with db_session() as db:
    rows = db.execute(select(Torrent).where(
        Torrent.is_batch, Torrent.status.notin_([TorrentStatus.SKIPPED]))).scalars().all()
    for t in rows:
        p = parse(t.title_raw)
        t.parsed_json = p.to_dict()
        sub = t.subscription
        for n in p.episodes:
            ep = db.execute(select(Episode).where(
                Episode.bangumi_id == sub.bangumi_id, Episode.type == EpisodeType.EP,
                Episode.number == n)).scalar_one_or_none()
            if ep is None:
                ep = Episode(bangumi_id=sub.bangumi_id, number=n, type=EpisodeType.EP)
                db.add(ep)
                db.flush()
            if not db.get(TorrentEpisode, (t.id, ep.id)):
                db.add(TorrentEpisode(torrent_id=t.id, episode_id=ep.id))
        print(f"重解析 #{t.id} episodes={p.episodes[:1]}..{p.episodes[-1:]} | {t.title_raw[:50]}")

# 2) 删多余任务:订阅2 全部 + 订阅3 非合集(经 API,标手动删除)
tasks = api.get("/api/tasks").json()
to_delete = [t for t in tasks
             if t["status"] in ("downloading", "pending", "completed")
             and (t["subscription_id"] == 2 or (t["subscription_id"] == 3 and not t["is_batch"]))]
for t in to_delete:
    r = api.delete(f"/api/tasks/{t['id']}?delete_files=true")
    print(f"删除 #{t['id']} [{r.status_code}] {t['title_raw'][:55]}")
print(f"\n共删除 {len(to_delete)} 个;保留 LoliHouse 合集")
