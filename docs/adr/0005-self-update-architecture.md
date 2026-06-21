# 网页一键自更新架构(检查更新 → 拉包 → 自动完成)

> 状态:已接受(2026-06-22,grill 一轮敲定)。实现分 5 个阶段,见末尾「实施切片」。

设置页提供「检查更新」,发现新版后一键更新、全程自动完成(下载 → 校验 → 应用 → 重启 → 重连),覆盖两类更新:**纯代码**(常见,快)与**完整**(依赖/系统库变更,换镜像)。

## 背景与约束

- 应用以 **FastAPI + Vue3 + SQLite + Docker** 部署;`docker/app.Dockerfile` 把 `backend/app` 与构建好的 `frontend/dist` **COPY 进镜像**(代码烤死),运行时容器内**无 git、无 node、无 docker socket**。
- 因此「运行中的代码」默认不可变;且 **pip 依赖 / apt 系统库(ffmpeg、cifs-utils)装在镜像里**,纯换源码碰不到它们。
- DB schema 由 `database._migrate_columns` 在启动时做**前向 ALTER**,加列可自愈。
- 仓库 **公开**(`github.com/Iron237/mikanarr`),Release 资产 / GHCR 匿名可拉,无需 token。
- 部署已为 SMB 挂载加了 `cap_add: SYS_ADMIN`(见 [ADR 无,compose 注释]),信任模型本就接近宿主特权。

## 决策

### 1. 更新机制:更新包 + 可写代码卷
镜像仍烤一份**基线代码**;运行时代码改到**可写卷** `/<data>/code/current`(symlink),首启若卷为空则从烤死基线**播种**。更新 = 把新代码落到卷 + 重启进程。前端 `dist` 静态服务,落盘即生效;后端需进程重启。

### 2. 依赖边界:纯代码就地更新,依赖变更走完整包(自动)
锁依赖,使绝大多数发布是**纯代码/UI/修复**。每个发布的 `manifest.json` 声明 `base_rev`(= `pyproject` 依赖 + Dockerfile apt 列表的哈希,构建期烤进镜像)。检查更新时:

- 目标 `base_rev == 运行中 base_rev` → **纯代码**就地更新;
- 不同(依赖/系统库变了) → 自动转**完整更新**(拉镜像),绝不半更新成 import 错误。

### 3. 镜像交换执行:挂 docker socket + 一次性 helper 容器
容器无法给自己换镜像并重建自身(执行重建的进程会随之死)。故 app 容器挂 `/var/run/docker.sock`;完整更新时由 app 通过 socket 启一个 **detached 一次性 helper 容器**执行重建,helper 不随 app 死而中断。

### 4. 镜像来源:GHCR + GitHub Actions
`on: release published` 时 CI 构建并推 `ghcr.io/iron237/mikanarr:<version>`;完整更新 = `docker pull` 新 tag(分层复用、增量小),由 helper `compose up` 重建。**仅 amd64**(本机与发布包均 amd64,需要再加 arm64)。

### 5. 控制面:Releases API + 每版 manifest.json 资产
app 调 GitHub Releases API(公开免认证)取目标 release;每个 release 带 `manifest.json` 资产:

```json
{
  "version": "0.1.1",
  "base_rev": "<deps+apt 哈希>",
  "image_ref": "ghcr.io/iron237/mikanarr:0.1.1",
  "image_digest": "sha256:...",
  "code_package_url": "https://.../mikanarr-0.1.1-code.tar.gz",
  "code_sha256": "<sha256>",
  "min_version": "0.1.0",
  "prerelease": true,
  "changelog": "..."
}
```

版本用 semver 比较(含预发布后缀排序规则);`min_version` 用于「太旧只能走完整」的兜底。

### 6. 应用 + 重启:版本目录 + symlink + 退出交给 restart 策略
卷里 `code/releases/<version>/` 多版本并存,`current` symlink 指活动版。纯代码更新:下载 → 解压到新目录 → 校验 `sha256` → **原子重指 symlink** → app `sys.exit` → `restart: unless-stopped` 拉起并 import 新代码。原子、留旧版可回滚、重启即干净重初始化。entrypoint 从 symlink 路径运行。

### 7. 失败处理:健康门控自动回滚
新 **PID-1 wrapper** 脚本:重启前写 `pending{prev,new,attempts}` 标记;wrapper 跑新版;app 启动自检(可 import + DB 可开 + HTTP 健康)通过后 **confirm** 清标记。若崩溃循环(多次快速退出仍未 confirm)超过 K 次,wrapper 把 symlink 指回 `prev` 重启。坏更新自愈,无需人工。

### 8. 触发 / UX:手动检查 + 一键更新 + 后台提醒
设置页「检查更新」→ 展示当前 vs 最新 + changelog + 类型(代码/完整)+ 大小。点「立即更新」后全程自动;前端检测后端重启 → 轮询 `/api/system/version` 直到新版起来 → 「更新完成」。另有每日后台检查,只出「有新版」徽标,**不自动装**(自动装为 opt-in)。

### 9. helper 重建:docker compose up -d(挂 compose 目录)
为完整保留部署配置(caps / socket / 卷 / 端口),helper 以 `docker compose -p <project> up -d` 声明式重建,而非克隆容器配置。要求:部署把 **compose 项目目录只读挂进 app**,并记 `COMPOSE_PROJECT`。helper 镜像 = 更新时拉含 compose v2 插件的官方 docker CLI 镜像(一次性用完即弃)。

### 10. 完整性:manifest sha256 + GitHub TLS + 镜像 digest
代码包按 `manifest.code_sha256` 校验后才应用(开销毫秒级);镜像靠 `docker pull` 的内容寻址 digest 自验。信任根 = GitHub TLS + 仓库所有权。加密签名(minisign/cosign)作为后续加固,非 v1。

### 11. 并发:静默 + 门控
应用前:若**移文件操作**(本地导入 / BD 导入)进行中则拒绝/延后(不可安全打断)。应用时:停 APScheduler、置 `updating` 标记(拒新的写请求)、等在飞 DB 事务完成 → 退出重启。**外部下载器(qB/BitComet)中的下载跨重启继续**,启动对账收拾其余。

### 12. 通道:开关,默认含预发布
设置加「包含预发布」开关。当前只发 pre-release → 默认开;将来切正式稳定版后再把默认改为仅稳定。

## Consequences

- **部署改造**:Dockerfile 改从卷运行 + 新 PID-1 wrapper entrypoint;compose 增 `docker.sock` 挂载、compose 目录只读挂载、代码卷;dev(仓库)与发布包两套 compose 都要同步,否则测不了。
- **新依赖面**:socket = 宿主 root 等价权限(与既有 `SYS_ADMIN` 一致,需文档写明);GHCR 包须设为 public。
- **完整更新回滚比代码回滚重**(镜像级):v1 先做「完整更新失败 → 手动回滚/重拉上一版」,纯代码更新才自动回滚;完整级自动回滚列为后续。
- **版本来源**:`version` 与 `base_rev` 构建期烤进镜像(label/env + 生成 `_version.py`),新增 `GET /api/system/version`。

## 实施切片(→ issues)

1. **CI + 发布产物**:GitHub Actions on release 构 amd64 镜像推 GHCR;打代码包 `app/+dist/` tar.gz;算 `base_rev`/`sha256`/digest;生成并上传 `manifest.json` + 代码包;`base_rev`/`version` 烤进镜像。
2. **部署改造 + PID-1 wrapper**:代码卷 + `current` symlink + 首启/重播种;wrapper(启动计数 + 自动回滚 + confirm);compose 加 socket + compose 目录挂载 + 代码卷;`GET /api/system/version`。
3. **后端 updater(纯代码路径)**:`services/updater.py`(check via Releases API+manifest、下载校验、解压重指 symlink、退出);`/api/system/update/{check,apply,status}`;并发门控(移文件检测 + 调度暂停 + updating 标记)。
4. **完整更新路径**:依赖变更判定 → `docker pull` → socket 起 helper 跑 `compose up -d` 重建;启动时「烤死版本 > 卷版本」重播种;通道开关(含预发布)。
5. **前端设置页 UI**:更新区(当前版本、检查更新、最新版+changelog+类型+大小、立即更新、进度+断连重连轮询)、后台每日检查徽标。
