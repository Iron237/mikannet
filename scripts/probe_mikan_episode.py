"""探针:Episode 页能否提取 bangumi_id + subgroup_id(导入功能的依赖)。"""
import re

import httpx

URL = "https://mikanani.me/Home/Episode/1c6a8c3e78e91e3f845ce539ce3ec27c8ae4e6ee"
with httpx.Client(proxy="http://127.0.0.1:10808", timeout=30, trust_env=False,
                  headers={"User-Agent": "Mozilla/5.0"}) as c:
    html = c.get(URL).text

print("bangumi links :", re.findall(r'href="(/Home/Bangumi/\d+[^"]*)"', html)[:3])
print("subgroup links:", re.findall(r'href="(/Home/PublishGroup/\d+)"', html)[:3])
print("title tags    :", re.findall(r"<title>([^<]{0,80})", html))
# 保存 fixture
open(r"G:\Works\Bots\RSS video\backend\tests\fixtures\mikan_episode.html", "w",
     encoding="utf-8").write(html)
print("fixture saved", len(html))
