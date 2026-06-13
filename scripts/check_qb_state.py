"""检查 qB 任务状态/做种数/tracker 列表。"""
import qbittorrentapi

qb = qbittorrentapi.Client(host="localhost", port=18080, username="admin", password="qbadmin")
qb.auth_log_in()
print("qB 版本:", qb.app.version)
for t in qb.torrents.info():
    print(f"\n任务: {t.name[:60]}")
    print(f"  category={t.category} state={t.state} progress={t.progress:.1%}")
    print(f"  seeds={t.num_seeds}({t.num_complete}) peers={t.num_leechs}({t.num_incomplete}) dlspeed={t.dlspeed}")
    trackers = qb.torrents.trackers(torrent_hash=t.hash)
    real = [tr for tr in trackers if not tr.url.startswith("**")]
    print(f"  trackers={len(real)}:")
    for tr in real[:5]:
        print(f"    [{tr.status}] {tr.url[:70]} msg={tr.msg[:40]}")
prefs = qb.app.preferences
print("\nadd_trackers_enabled:", prefs.get("add_trackers_enabled"))
print("add_trackers:", repr(prefs.get("add_trackers"))[:100])
print("dht:", prefs.get("dht"), "| pex:", prefs.get("pex"), "| lsd:", prefs.get("lsd"))
