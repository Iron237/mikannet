"""探针:Episode 页能否提取 bangumi_id + subgroup_id(导入功能的依赖)。"""
import re
from pathlib import Path

import httpx

URL = "https://mikanani.me/Home/Episode/1c6a8c3e78e91e3f845ce539ce3ec27c8ae4e6ee"
with httpx.Client(proxy="http://127.0.0.1:10808", timeout=30, trust_env=False,
                  headers={"User-Agent": "Mozilla/5.0"}) as c:
    html = c.get(URL).text

print("bangumi links :", re.findall(r'href="(/Home/Bangumi/\d+[^"]*)"', html)[:3])
print("subgroup links:", re.findall(r'href="(/Home/PublishGroup/\d+)"', html)[:3])
print("title tags    :", re.findall(r"<title>([^<]{0,80})", html))
# 保存 fixture(相对仓库定位,不携带开发者本机路径)
fixture = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "mikan_episode.html"
fixture.write_text(html, encoding="utf-8")
print("fixture saved", len(html))
