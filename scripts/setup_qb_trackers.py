"""给 qB 配置自动追加 tracker + 给现有任务补加。

列表源:XIU2/TrackersListCollection best.txt(社区维护,每日更新)。
用法: setup_qb_trackers.py [--list-url URL]
"""
import argparse
import os

import httpx
import qbittorrentapi

DEFAULT_LIST = "https://raw.githubusercontent.com/XIU2/TrackersListCollection/master/best.txt"

ap = argparse.ArgumentParser()
ap.add_argument("--list-url", default=DEFAULT_LIST)
args = ap.parse_args()

print("[1] 拉取 tracker 列表 …")
r = httpx.get(
    args.list_url,
    proxy=os.environ.get("TRACKER_PROXY") or None,
    timeout=30,
)
r.raise_for_status()
trackers = [l.strip() for l in r.text.splitlines() if l.strip()]
print(f"    获取 {len(trackers)} 条:", trackers[:3], "…")

password = os.environ.get("QB_PASS") or os.environ.get("MIKANNET_QB_PASSWORD")
if not password:
    raise SystemExit("请通过环境变量 QB_PASS 提供 qB 密码")
qb = qbittorrentapi.Client(
    host=os.environ.get("QB_HOST", "localhost"),
    port=int(os.environ.get("QB_PORT", "18080")),
    username=os.environ.get("QB_USER", "admin"),
    password=password,
)
qb.auth_log_in()

print("[2] 写入 qB 偏好(自动为新任务追加)…")
qb.app.set_preferences({
    "add_trackers_enabled": True,
    "add_trackers": "\n".join(trackers),
})

print("[3] 给现有任务补加 …")
for t in qb.torrents.info():
    qb.torrents.add_trackers(torrent_hash=t.hash, urls=trackers)
    qb.torrents.reannounce(torrent_hashes=t.hash)
    print(f"    {t.name[:50]} ← +{len(trackers)} trackers,已强制汇报")

print("DONE")
