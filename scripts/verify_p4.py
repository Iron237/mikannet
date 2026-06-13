"""P4 验收:等待 47 话完成 → 后处理 → 详情页文件信息。"""
import sys
import time

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8008", timeout=30, trust_env=False)

deadline = time.time() + 600
while time.time() < deadline:
    tasks = c.get("/api/tasks").json()
    t1 = next((t for t in tasks if t["id"] == 1), None)
    if t1 is None:
        print("任务 1 不存在")
        sys.exit(1)
    print(f"任务1: {t1['status']} {t1['progress']:.1%}", flush=True)
    if t1["status"] == "archived":
        break
    if t1["status"] in ("download_error", "submit_failed"):
        print("下载出错:", t1["error_message"])
        sys.exit(1)
    if t1["status"] == "completed" and t1.get("error_message"):
        print("后处理有失败:", t1["error_message"])
        break
    time.sleep(10)
else:
    print("超时未完成")
    sys.exit(1)

d = c.get("/api/bangumi/1").json()
for ep in d["episodes"]:
    if not ep["files"]:
        continue
    print(f"\n第 {ep['number']} 话 [{ep['status']}]")
    for f in ep["files"]:
        print("  path      :", f["path"])
        print("  resolution:", f["resolution"], "| codec:", f["codec"],
              "| bitrate:", f["bitrate"])
        print("  audio     :", f["audio_tracks"])
        print("  subs      :", f["subtitle_tracks"])

ua = c.get("/api/files/unassigned").json()
print("\n待确认文件:", len(ua))
print("\nP4 VERIFY DONE")
