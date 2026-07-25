# Mikannet — Windows 完整环境包(免构建)

这个包**已内置构建好的 Docker 镜像**(`mikannet-image.tar.gz`),部署时**不需要联网构建、也不从 Docker Hub 拉镜像**——专为国内拉不动 `python/node` 基础镜像的情况准备。

> 架构:linux/amd64(Windows Docker Desktop = WSL2,适用)。ARM 机器请用源码包自行构建。

## 步骤(全程网页配置,不用编辑任何文本)

1. 安装并启动 **Docker Desktop**。
2. 解压本包,**双击 `deploy-win.bat`**。首次会 `docker load` 载入镜像(约 1GB,稍慢)后自动启动。
3. 浏览器打开 **http://localhost:8008** → 自动进入**首次配置向导**:
   - **存储**:选「NAS / SMB」填共享地址(如 `//192.168.1.100/anime/mikannet`)+ 账号/密码 →「测试连接」通过即挂载;或选「本地 / Docker 路径」用容器内 `/downloads`。
   - **下载器**:qBittorrent 地址/端口/账号密码 + 写盘根(可「测试连接」)。
   - **代理**:国内填 `http://host.docker.internal:10808`,不用可留空。
   - **元数据**(可跳过):TMDB key、蜜柑 cookie。
   - 完成 → 进入主页。
4. 之后所有项都能在**设置页**改;NAS 也能在设置页重配。

- 看日志:`deploy-win.bat logs` ;停止:`deploy-win.bat down`
- 不再需要手编辑 `.env`(NAS/代理/下载器全在网页向导里)。

## 迁移历史数据

旧实例:设置页「数据备份 / 迁移」→ 导出 JSON。新实例:同处导入。只要 NAS 文件仍在下载根下的**相同相对路径**,番剧库即原样复现。

## 原生播放 / 打开目录 / PowerDVD

设置页填好「宿主机路径前缀」后,点「下载协议处理器」并双击运行(JScript,无窗口闪、无需 PowerShell)。首次点播放时浏览器勾「始终允许」,之后免提示。仅在装了处理器的本机有效。

## 排障

- **NAS 挂载失败 / 容器起不来**:多为 `.env` 的 NAS 凭据或共享路径不对。`deploy-win.bat logs` 看错误。
- **`mikannet-image.tar.gz` 载入失败**:确认它与 `deploy-win.bat` 在同一目录;或手动 `docker load -i mikannet-image.tar.gz`。
- 其它见 `DEPLOY.md`。
