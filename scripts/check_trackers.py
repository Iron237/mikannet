"""统计任务的 tracker 工作状态分布。status: 0未联系 1禁用 2工作中 3更新中 4不工作"""
from collections import Counter

import qbittorrentapi

qb = qbittorrentapi.Client(host="localhost", port=18080, username="admin", password="qbadmin")
qb.auth_log_in()
for t in qb.torrents.info():
    trackers = [tr for tr in qb.torrents.trackers(torrent_hash=t.hash)
                if not tr.url.startswith("**")]
    c = Counter(tr.status for tr in trackers)
    print(f"{t.name[:50]} | state={t.state} peers_known={t.num_incomplete}+{t.num_complete}")
    print(f"  tracker 状态分布: 工作中={c.get(2,0)} 不工作={c.get(4,0)} 未联系={c.get(0,0)} 更新中={c.get(3,0)}")
    working = [tr for tr in trackers if tr.status == 2]
    for tr in working[:6]:
        print(f"  [OK] {tr.url[:60]} peers={tr.num_peers} seeds={tr.num_seeds}")
