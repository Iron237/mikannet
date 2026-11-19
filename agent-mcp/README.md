# Mikannet MCP

在运行 Agent 的电脑上启动 stdio 适配器，连接可访问的 Mikannet HTTP 服务。已验证服务端 v0.1.27。工具清单取本地已实现操作与目标实例 OpenAPI 的交集。升级服务或适配器后，重新启动 MCP 客户端连接。

## 接入

需要 Python 3.11 或更高版本。在项目根目录执行：

```sh
python -m venv agent-mcp/.venv
# Linux / macOS
agent-mcp/.venv/bin/python -m pip install -e ./agent-mcp
# Windows PowerShell
agent-mcp/.venv/Scripts/python.exe -m pip install -e ./agent-mcp
```

把对应虚拟环境的 Python 绝对路径填入支持 stdio 的 Agent。下例假设 Mikannet 映射到本机 9000 端口，远程服务器请填写其可信局域网或 VPN 地址。

```json
{
  "mcpServers": {
    "mikannet": {
      "command": "/absolute/path/to/agent-mcp/.venv/bin/python",
      "args": ["-m", "mikannet_mcp.server", "--base-url", "http://127.0.0.1:9000"]
    }
  }
}
```

Codex CLI 也可使用 `codex mcp add mikannet -- <虚拟环境的Python绝对路径> -m mikannet_mcp.server --base-url http://127.0.0.1:9000`。Windows 使用 `.venv/Scripts/python.exe`。可用环境变量 `MIKANNET_BASE_URL` 替代命令行地址。默认连接本机 8008。向启动参数追加 `--read-only` 即可只注册查询工具。适配器通过 stdio 通信，沿用现有 Mikannet 实例的网络访问边界。

## 常用工作流

| 目标 | 工具与调用顺序 |
| --- | --- |
| 新增追番 | `search_anime` → `recommend_subgroups` → `preview_subscription` → `create_subscription`。预览接口的 `bangumi_id` 使用蜜柑 ID。创建只追新集的订阅时，在 `body` 传 `backfill: false`。 |
| 管理订阅 | `list_subscriptions` 拿本地 ID，`poll_subscription` 立即轮询，`update_subscription` 传 `body: {"enabled": false}` 停用，或使用 `replace_subscription_source` 换组。 |
| 处理下载异常 | `list_downloads` 分别传 `status: "download_error"` 或 `"submit_failed"`，核对任务后使用 `resume_download` 或 `retry_postprocess`。删除操作的 `delete_files=true` 会影响物理文件。 |
| 查缺和补番 | 从 `list_library` 取本地番剧 ID，调用 `resource_strategy`；`scan_anime` 传 `body: {"mode":"review"}` 生成候选，再用 `scan_status`、`scan_history` 查看结果，`approve_scan` 批准后提交。 |
| 单集弹幕 | `get_anime` 指定 `section: "episodes"`，从 `files[].id` 取视频 ID；调用 `auto_danmaku`。需人工选集时改用 `match_danmaku` 或 `search_danmaku`，确认后用 `save_danmaku`。凭据在 Web 设置里配置。 |
| 管理偏好 | `get_preferences` 查看生效值，之后用 `set_preferences` 提交部分字段，如 `{"body":{"danmaku_auto_download":true}}`。 |

调用 `tools/list` 可读到本实例支持的完整工具及输入 schema。路径、查询参数放在工具输入顶层，JSON 请求体放在 `body`。详情页使用本地 `bangumi_id`，蜜柑搜索结果使用 `mikan_bangumi_id`。

工具返回 `structuredContent`。成功结果包含 HTTP `status`，有正文时包含 `data`。查询列表默认每页 20 项，最多 100 项。顶层数组在 `data.items`，对象中的列表保留其字段名。按 `pages.<字段>.next_offset` 翻页，值为 null 表示已读完。分页发生在适配器内。后台任务的响应只表示已受理，后续需查询进度。HTTP 失败通过 MCP 的 `isError` 返回。写请求中断时请先查询状态。

## 开发与验证

在装有后端与 MCP 依赖的测试环境执行 `python -m pytest -q agent-mcp/tests`。测试对照 FastAPI 路由和工具 schema，并通过真实 stdio 客户端在临时数据库上验证写入、只读模式与错误反馈。新增功能的同步要求见仓库根目录的 [AGENTS.md](../AGENTS.md)。

工具参数有意变化时，从项目根目录运行 `python agent-mcp/tests/test_contract.py --update`，审阅 `tool-inputs.jsonl` 中的字段、类型、默认值及必填项差异后一起提交。
