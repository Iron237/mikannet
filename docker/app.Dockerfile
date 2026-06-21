# 阶段 1:构建前端
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# 阶段 2:Python 运行时(含 ffprobe)
FROM python:3.11-slim
# 国内网络:apt/pip 用清华镜像直连(deb.debian.org 经代理或直连都不稳)。
# CI(GitHub runner)用 build-args 覆盖回上游 deb.debian.org / pypi.org。
ARG DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# apt 包清单集中在 docker/apt-packages.txt(base_rev 据此 + pyproject 依赖计算)
COPY docker/apt-packages.txt /opt/mikanarr/apt-packages.txt
RUN sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       $(grep -vE '^\s*#|^\s*$' /opt/mikanarr/apt-packages.txt) \
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
COPY docker/wrapper.py /opt/mikanarr/wrapper.py
COPY --from=frontend /build/dist /opt/mikanarr/frontend/dist
# 版本与依赖基线:CI 用 release tag 注入;本地构建留空 → 运行期回退计算(见 app/_version.py)
ARG VERSION=
ARG BASE_REV=
ENV MIKANARR_DATA_DIR=/config \
    MIKANARR_DOWNLOAD_ROOT_LOCAL=/downloads \
    MIKANARR_DOWNLOAD_ROOT=/downloads \
    MIKANARR_CODE_DIR=/code \
    MIKANARR_VERSION=${VERSION} \
    MIKANARR_BASE_REV=${BASE_REV}
LABEL org.opencontainers.image.title="mikanarr" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/Iron237/mikanarr"
EXPOSE 8008
# PID-1 wrapper:首启从烤死基线播种代码卷 → 从 /code/current 跑应用 → 健康门控自愈回滚。
# 本地无代码卷(开发期直跑 uvicorn)时 wrapper 也能从镜像自身播种,行为一致。
CMD ["python", "/opt/mikanarr/wrapper.py"]
