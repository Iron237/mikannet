#!/usr/bin/env bash
# Mikanarr 一键部署(Linux / macOS)
#
#   ./deploy.sh          连接远程 NAS(CIFS 网络卷,与 Windows 同一套)
#   ./deploy.sh local    在本机/NAS 上直接跑(本地目录绑定挂载,见 docker-compose.local.yml)
#   ./deploy.sh down     停止并移除容器
#   ./deploy.sh logs     跟踪日志
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || { echo "✗ 未找到 docker,请先安装 Docker"; exit 1; }
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else echo "✗ 未找到 docker compose 插件"; exit 1; fi

FILES="-f docker-compose.yml"
[ "${1:-}" = "local" ] && FILES="$FILES -f docker-compose.local.yml"

case "${1:-up}" in
  down) $DC $FILES down; exit 0 ;;
  logs) $DC $FILES logs -f mikanarr; exit 0 ;;
esac

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ 已生成 .env —— 请先编辑填写 NAS 路径/凭据(或本地路径),然后重新运行 ./deploy.sh"
  exit 1
fi

echo "▶ 构建并启动 Mikanarr ..."
if ! $DC $FILES up -d --build; then
  echo
  echo "✗ 构建/启动失败。"
  echo "  若错误是拉取基础镜像超时(auth.docker.io / registry-1.docker.io)→ 本机连不上 Docker Hub:"
  echo "  编辑 /etc/docker/daemon.json 加 registry-mirrors 后重启 docker(见 DEPLOY.md「构建时拉基础镜像超时」),再重试。"
  exit 1
fi
echo
echo "✓ 已启动。WebUI:  http://localhost:8008"
echo "  查看日志:        ./deploy.sh logs"
echo "  停止:            ./deploy.sh down"
