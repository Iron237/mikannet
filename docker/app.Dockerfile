# 阶段 1:构建前端
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# 阶段 2:Python 运行时(含 ffprobe)
FROM python:3.11-slim
# 国内网络:apt/pip 用清华镜像直连(deb.debian.org 经代理或直连都不稳)
ARG DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/mikanarr/backend
COPY backend/pyproject.toml .
# 仅安装依赖(利用层缓存):从 pyproject 提取 dependencies
RUN python - <<'EOF' && pip install --no-cache-dir -i ${PIP_INDEX} -r /tmp/req.txt
import tomllib
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
open("/tmp/req.txt", "w").write("\n".join(deps))
EOF
COPY backend/app ./app
COPY --from=frontend /build/dist /opt/mikanarr/frontend/dist
ENV MIKANARR_DATA_DIR=/config \
    MIKANARR_DOWNLOAD_ROOT_LOCAL=/downloads \
    MIKANARR_DOWNLOAD_ROOT=/downloads
EXPOSE 8008
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
