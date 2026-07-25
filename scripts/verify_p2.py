"""P2 验收脚本:air_date 修复、订阅 2 补齐留痕、库视图元数据。"""
import json

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8008", timeout=30, trust_env=False)

d = c.get("/api/search/bangumi/3203").json()
print("[1] air_date:", d["air_date"])

r = c.post("/api/system/poll").json()
print("[2] poll:", json.dumps(r, ensure_ascii=False))

tasks = c.get("/api/tasks").json()
sub2 = [t for t in tasks if t["subscription_id"] == 2]
batches = [t for t in sub2 if t["is_batch"]]
print(f"[3] 订阅2 条目 {len(sub2)},其中合集 {len(batches)},全部 skipped:",
      all(t["status"] == "skipped" for t in sub2))

lib = c.get("/api/bangumi").json()
for b in lib:
    print(f"[4] 库: {b['title']} | {b['year']} {b['season']} | {b['studio']} | "
          f"{b['airing_status']} | poster={'Y' if b['poster'] else 'N'}")
