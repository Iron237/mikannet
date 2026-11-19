# 🍊 Mikannet

番剧订阅下载管理系统:[蜜柑计划](https://mikanani.me) 订阅 → 自动获取种子 → qBittorrent 下载到 NAS → WebUI 管理 + 手机推送。

- **站内订阅**支持搜索番剧、选择字幕组、配置过滤规则和批量导入蜜柑订阅。在「订阅管理」可按番剧或字幕组搜索，选择「需要处理」查看最近一次读取或提交失败的订阅。
- **虚拟番剧库**:文件保持原始命名持续做种,库视图由数据库渲染(封面墙/年代/制作公司/评分/简介,元数据来自 bgm.tv + TMDB)
- **下载管理**通过 WebSocket 显示实时进度，重新连接后自动核对任务状态。「需处理」集中显示提交失败、下载错误和入库异常的任务，可按任务状态重试。
- **备份恢复**在「设置 → 维护」选择备份文件，预览当前与恢复后的逐表数量，再确认恢复。支持 MikanArr v1 和 Mikannet v2 备份。
- **文件信息**:下载完成自动 ffprobe(分辨率/编码/码率/音轨/字幕轨)
- **智能规则**:v2 修复版自动替换、连载排除合集、补完结老番首选合集、补齐/只追新
- **推送**:Telegram / Server酱 / PushPlus,四类事件独立开关

文档:领域词汇表 [CONTEXT.md](CONTEXT.md) · 架构决策 [docs/adr/](docs/adr/) · 探针结论 [backend/tests/fixtures/PROBE-NOTES.md](backend/tests/fixtures/PROBE-NOTES.md)

## 部署(Docker)

**一键部署**(Windows Docker Desktop 双击 `deploy.bat`;Linux 跑 `./deploy.sh`)与三种部署场景详见 **[DEPLOY.md](DEPLOY.md)**。手动:

```bash
cp .env.example .env   # 填 NAS SMB、代理、TMDB key
docker compose up -d --build
# 在 NAS/本地盘直接跑(本地绑定挂载):
# docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

- WebUI: http://localhost:9000(端口见 `.env` 的 `HOST_PORT`)

### 安全边界

- 当前版本未内置账户登录；只应部署在可信局域网或 VPN 内，**不要直接暴露到公网**。
- `data/`、备份 JSON、`.env`、Cookie 和通知/第三方令牌均含隐私信息，不得提交或作为 Issue 附件上传。
- 发布前运行 `python scripts/check_public_repo.py`、后端测试和前端构建；完整清单见 [RELEASING.md](RELEASING.md)。

### 注意事项

- **qBittorrent Host 头校验**:WebUI 内外端口必须一致(compose 已统一 18080)。
- **代理**:国内环境 Mikan/bgm.tv/TMDB/Telegram 基本都需要代理;容器内访问宿主代理用 `host.docker.internal`。
- **`.torrent` 由 app 经代理取回字节再投给 qB**,qB 容器本身不需要代理。

### 迁移到 Docker

容器内路径(`/downloads`、`/config`),数据库记录的都是相对路径。迁移步骤:

1. 停止 compose,把 `./data/` 目录拷到 NAS
2. 修改 `docker-compose.yml` 的 `downloads` 卷:删掉 CIFS `driver_opts`,改为 NAS 本地路径绑定
3. NAS 上 `docker compose up -d`,启动对账会自动校正任务状态

## 字幕组推荐与单集弹幕

订阅向导及更换订阅源时会按当前目标分辨率和字幕语言比较各组。追更推荐比较近 28 天的正片单集更新数和最近更新日期。补番推荐优先考虑符合偏好的正片覆盖集数，再比较单集发布覆盖、偏好的 BD 版本及最近发布日期。相同集数的修订与多种封装只计一次。偏好在「设置 → 订阅与自动化」调整。

在「设置 → 网络与元数据 → 弹幕」填写自有的弹弹play AppId 和 AppSecret。打开番剧详情，展开剧集，在对应文件旁点「下载弹幕」，核对番名与集名后可保存同名 XML 到视频旁，或下载到浏览器。节目匹配支持文件指纹和手动搜索，已有同名 XML 会保留。服务来自[弹弹play开放弹幕网络](https://www.dandanplay.com/)，凭据可在[开发者中心](https://dev.dandanplay.com/)申请。

配置好弹弹play凭据后，新订阅单集首次入库及从详情页播放视频时会自动尝试匹配；唯一精确匹配且有弹幕时保存同名 XML。需要自行选集时可使用「下载弹幕」。可在弹幕设置中关闭自动下载。

## 网页一键自更新

设置页「更新」区可检查并一键更新,全程自动(下载 → 校验 → 应用 → 重启 → 重连)。架构与权衡详见 **[docs/adr/0005-self-update-architecture.md](docs/adr/0005-self-update-architecture.md)**。两类更新:

- **纯代码**(依赖未变,`base_rev` 相同):下载代码包 → 校验 sha256 → 落可写代码卷 → 原子重指 `current` → 重启;失败由 PID-1 wrapper 自动回滚。
- **完整(换镜像)**(`base_rev` 不同):经 docker socket 启一次性 helper 跑 `docker compose up -d` 换 GHCR 镜像重建容器。

发布流水线 `.github/workflows/release.yml` 在每次 release published 时构建并推送 `ghcr.io/iron237/mikannet:<version>`,并上传 `manifest.json` + 代码包作为 release 资产。

**运维须知 / 信任权衡**:

- **GHCR 包须为 public**:匿名 `docker pull` 与完整更新依赖镜像公开;CI 会尝试自动设为 public,失败则在 GitHub → Packages 手动设置。
- **完整更新需挂 `docker.sock`**:`/var/run/docker.sock` 挂进 app = **宿主 root 等价权限**(与既有 `SYS_ADMIN` 同级)。正式部署模板默认包含该挂载、`/compose` 和动态镜像变量；部署脚本启动后会强制预检。若主动移除这些权限，设置页会在更新前明确阻止完整更新，而不是进入后台后失败。
- **回滚**:纯代码失败自动回滚;完整失败为手动——把 `.env` 的 `MIKANNET_IMAGE_REF` 改回上一个可用 tag 后 `docker compose up -d`(步骤见 ADR-0005)。

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
