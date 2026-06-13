# 🍊 Mikanarr

番剧订阅下载管理系统:[蜜柑计划](https://mikanani.me) 订阅 → 自动获取种子 → qBittorrent 下载到 NAS → WebUI 管理 + 手机推送。

- **站内订阅**:WebUI 内搜索番剧、选字幕组、配过滤规则,无需去蜜柑网站
- **虚拟番剧库**:文件保持原始命名持续做种,库视图由数据库渲染(封面墙/年代/制作公司/评分/简介,元数据来自 bgm.tv + TMDB)
- **实时下载管理**:WebSocket 实时进度,暂停/恢复/删除/重试
- **文件信息**:下载完成自动 ffprobe(分辨率/编码/码率/音轨/字幕轨)
- **智能规则**:v2 修复版自动替换、连载排除合集、补完结老番首选合集、补齐/只追新
- **推送**:Telegram / Server酱 / PushPlus,四类事件独立开关

文档:领域词汇表 [CONTEXT.md](CONTEXT.md) · 架构决策 [docs/adr/](docs/adr/) · 探针结论 [backend/tests/fixtures/PROBE-NOTES.md](backend/tests/fixtures/PROBE-NOTES.md)

## 部署(Docker)

```bash
cp .env.example .env   # 填 NAS SMB、代理、TMDB key
docker compose up -d --build
```

- WebUI: http://localhost:8008
- 下载器:**桌面 qBittorrent**(.env 里 `QB_HOST=host.docker.internal` `QB_PORT=19897`)——绕开 Docker Desktop 双重 NAT 的入站连通性问题,下载走桌面那条单层 NAT 的快网。
  - 桌面 qB 需开 WebUI、关「Host 头校验」、能写 NAS;mikanarr 把下载存到 `DOWNLOAD_ROOT` 指的 NAS UNC,自己从 `/downloads` 读同一份。
  - 容器版 qB(`qbittorrent:18080`)仍在 compose 里(已 stop),改回 `QB_HOST=qbittorrent`/`QB_PORT=18080`/`DOWNLOAD_ROOT=/downloads` 即可回退。

### 重要约定

- **qB 5.x Host 头校验**:WebUI 内外端口必须一致(compose 已统一 18080),否则一律 401。
- **代理**:国内环境 Mikan/bgm.tv/TMDB/Telegram 基本都需要代理;容器内访问宿主代理用 `host.docker.internal`。
- **`.torrent` 由 app 经代理取回字节再投给 qB**,qB 容器本身不需要代理。

### 迁移到 NAS Docker

容器内路径(`/downloads`、`/config`)是稳定契约,数据库记录的都是相对路径。迁移步骤:

1. 停止 compose,把 `./data/` 目录拷到 NAS
2. 修改 `docker-compose.yml` 的 `downloads` 卷:删掉 CIFS `driver_opts`,改为 NAS 本地路径绑定
3. NAS 上 `docker compose up -d`,启动对账会自动校正任务状态

## 开发

```bash
# 后端(Python 3.11)
cd backend
python -m venv .venv && .venv/Scripts/pip install -e .[dev]
.venv/Scripts/python -m uvicorn app.main:app --port 8008 --reload

# 前端
cd frontend
npm install && npm run dev    # http://localhost:5173,代理到 8008

# 测试(标题解析语料回归 + Mikan HTML 快照 + 状态机)
cd backend && .venv/Scripts/python -m pytest tests/ -q
```

开发期 qBittorrent:`docker run -d --name qb -p 18080:18080 -e WEBUI_PORT=18080 linuxserver/qbittorrent`,
`backend/.env` 里配置 `MIKANARR_DOWNLOAD_ROOT_LOCAL` 指向 qB 下载目录的本机视角路径。

## 架构速览

```
RSS 轮询(APScheduler 15min)
  → 标题解析(anitopy+规则链,语料回归 98%)
  → 过滤(关键词/合集策略)→ 去重(guid/同集版本)
  → 提交 qBittorrent(专属分类,只管自己的任务)
  → tracker 轮询(活动 2s)→ WebSocket 推送进度
  → 完成 → ffprobe 串行队列 → 文件↔剧集映射 → v2 切换 → 入库(ARCHIVED)
  → 事件总线 → 通知(Telegram/Server酱/PushPlus)
```

核心不变式(ADR-0001):**下载文件永不移动/重命名**,番剧库是数据库上的虚拟视图。
