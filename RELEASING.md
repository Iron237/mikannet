# 发布检查清单

## 1. 隐私与安全

- 确认 `git status --short` 中没有 `.env`、Cookie、备份、数据库、日志、截图或本机配置。
- 运行 `python scripts/check_public_repo.py`。
- 人工检查 `git diff --cached`，尤其关注账号、密码、令牌、内网地址、NAS/本机路径。
- 若真实密钥曾进入提交历史，仅删除当前文件不够：先轮换密钥，再用 `git filter-repo` 或 BFG 清理历史，并协调强制推送。
- 应用尚无账户登录，只发布为可信局域网/VPN 服务，不宣称可安全暴露公网。

## 2. 验证

```bash
cd backend
python -m pytest -q
cd ../frontend
npm ci
npm run build
cd ..
python scripts/check_public_repo.py
git diff --check
```

确认源码许可证选择已明确；仓库若保持公开，没有许可证文件意味着他人默认没有复制、修改或分发授权。

## 3. 创建 Release

1. 确认 `main` 的测试工作流成功，版本标签使用 `vX.Y.Z`。
2. 在 GitHub 创建 Release，填写变更说明；未进入稳定阶段时勾选 pre-release。
3. 发布后流水线会构建 `linux/amd64` GHCR 镜像，并上传：
   - `manifest.json`
   - `mikannet-X.Y.Z-code.tar.gz`
4. 不要手工上传源码目录、`.env`、运行数据或本地打包目录。

## 4. 发布后核验

- 测试与 release 工作流均为绿色。
- Release 两项资产存在，`manifest.json` 的版本、镜像 digest、代码包 SHA-256 正确。
- `ghcr.io/iron237/mikannet:X.Y.Z` 可匿名读取，架构为 `linux/amd64`。
- 在干净环境完成一次首次部署、纯代码更新和必要时的镜像更新测试。
