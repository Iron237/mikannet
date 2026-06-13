"""探针:验证 qBittorrent Web API 满足 Mikanarr 全部需求。

验证点:登录、版本、分类创建、按 URL 添加种子(指定 category+savepath)、
列出/过滤 category 任务、实时进度字段、暂停/恢复、删除(含删文件)、全局限速。
用法: python probe_qbittorrent.py --host localhost --port 18080 --user admin --password XXX [--torrent-url URL]
"""
import argparse
import sys
import time

import qbittorrentapi


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--torrent-url", default=None, help="可选:真实种子 URL,测试完整添加流程")
    args = ap.parse_args()

    qb = qbittorrentapi.Client(host=args.host, port=args.port,
                               username=args.user, password=args.password)
    qb.auth_log_in()
    print(f"[1] 登录 OK — qB {qb.app.version} / API {qb.app.web_api_version}")

    cat = "mikanarr-probe"
    existing = qb.torrent_categories.categories
    if cat not in existing:
        qb.torrent_categories.create_category(name=cat, save_path="/downloads/probe")
    print(f"[2] 分类创建 OK — {cat} (现有: {list(qb.torrent_categories.categories.keys())})")

    qb.transfer.set_download_limit(5 * 1024 * 1024)
    print(f"[3] 全局限速 OK — 当前 DL limit: {qb.transfer.download_limit} B/s")

    if args.torrent_url:
        # 经代理由 Python 拉取 .torrent 字节再投给 qB(qB 容器自身无代理,不能让它去拉 URL)
        import httpx
        torrent_bytes = httpx.get(args.torrent_url, proxy="http://127.0.0.1:10808",
                                  timeout=30, trust_env=False).content
        print(f"    .torrent 已经代理取回 {len(torrent_bytes)} bytes")
        r = qb.torrents.add(torrent_files=torrent_bytes, category=cat, save_path="/downloads/probe/测试番剧")
        print(f"[4] 添加种子: {r}")
        time.sleep(3)
        tasks = qb.torrents.info(category=cat)
        if not tasks:
            print("    !! 添加后未在分类中找到任务")
            return 1
        t = tasks[0]
        print(f"    hash={t.hash[:16]}… name={t.name[:40]} state={t.state}")
        print(f"    进度字段: progress={t.progress:.2%} dlspeed={t.dlspeed} size={t.size} eta={t.eta} save_path={t.save_path}")
        qb.torrents.pause(torrent_hashes=t.hash)
        time.sleep(1)
        print(f"[5] 暂停 OK — state={qb.torrents.info(torrent_hashes=t.hash)[0].state}")
        qb.torrents.resume(torrent_hashes=t.hash)
        time.sleep(1)
        print(f"[6] 恢复 OK — state={qb.torrents.info(torrent_hashes=t.hash)[0].state}")
        qb.torrents.delete(delete_files=True, torrent_hashes=t.hash)
        print(f"[7] 删除(含文件)OK — 剩余任务 {len(qb.torrents.info(category=cat))}")
    else:
        print("[4-7] 未提供 --torrent-url,跳过真实种子流程")

    print("PROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
