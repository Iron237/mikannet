# 安全说明

Mikannet 当前面向可信局域网或 VPN 部署，尚未内置账户认证。请勿把 WebUI/API 端口直接暴露到公网。

运行目录、备份文件和日志可能包含媒体库信息、本机/NAS 路径、Cookie、通知凭据或第三方令牌。提交 Issue 时请先脱敏，不要上传 `.env`、数据库、备份或完整日志。

安全问题优先通过 GitHub 的私密漏洞报告入口提交：
https://github.com/Iron237/mikannet/security/advisories/new
