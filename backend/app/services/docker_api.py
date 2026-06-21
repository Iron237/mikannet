"""通过 /var/run/docker.sock 直连 Docker Engine API(容器内无需装 docker CLI)。

仅供完整更新(换镜像)用:拉一次性 helper 镜像(官方 docker CLI,含 compose v2 插件)→
启 detached helper 跑 `docker compose up -d` 重建本应用容器。helper 独立于本容器,
本容器被重建杀死时它照常完成(docs/adr/0005 决策 3/9)。

信任权衡:挂 docker.sock = 宿主 root 等价权限(与既有 SYS_ADMIN 一致),见 ADR/README。
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

SOCK = "/var/run/docker.sock"
HELPER_IMAGE = "docker:cli"        # 含 compose v2 插件的官方 CLI 镜像(一次性)
HELPER_NAME = "mikanarr-updater"


def _client(timeout: float = 300) -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=SOCK),
                        base_url="http://localhost", timeout=timeout)


def available() -> bool:
    """docker.sock 是否已挂载且可达。"""
    try:
        with _client(timeout=5) as c:
            return c.get("/_ping").status_code == 200
    except Exception:  # noqa: BLE001
        return False


def pull_image(ref: str) -> None:
    name, _, tag = ref.partition(":")
    tag = tag or "latest"
    with _client() as c:
        with c.stream("POST", "/images/create",
                      params={"fromImage": name, "tag": tag}) as r:
            r.raise_for_status()
            for _ in r.iter_lines():     # 必须读完流,pull 才真正完成
                pass


def run_compose_recreate(host_compose_dir: str, project: str, image_ref: str) -> str:
    """启一次性 helper:`docker compose -p <project> up -d --pull always` 重建本容器。

    host_compose_dir 必须是**宿主**上的 compose 目录路径(daemon 视角),由 compose 注入。
    image_ref 经 helper 的环境变量 MIKANARR_IMAGE_REF 传给 compose 做镜像替换。
    """
    if not host_compose_dir:
        raise RuntimeError("缺少 compose_host_dir(无法定位宿主 compose 目录);"
                           "请确认 compose 注入了 MIKANARR_COMPOSE_HOST_DIR")
    pull_image(HELPER_IMAGE)
    body = {
        "Image": HELPER_IMAGE,
        "Cmd": ["sh", "-c", f"docker compose -p {project} up -d --pull always"],
        "WorkingDir": "/compose",
        "Env": [f"MIKANARR_IMAGE_REF={image_ref}"],
        "HostConfig": {
            "Binds": [f"{SOCK}:{SOCK}", f"{host_compose_dir}:/compose:ro"],
            "AutoRemove": True,        # 一次性:跑完自动删除
            "NetworkMode": "bridge",
        },
    }
    with _client() as c:
        r = c.post("/containers/create", params={"name": HELPER_NAME}, json=body)
        if r.status_code == 409:       # 同名残留 → 强删后重建
            c.delete(f"/containers/{HELPER_NAME}", params={"force": "true"})
            r = c.post("/containers/create", params={"name": HELPER_NAME}, json=body)
        r.raise_for_status()
        cid = r.json()["Id"]
        c.post(f"/containers/{cid}/start").raise_for_status()
        log.info("已启动完整更新 helper 容器 %s(%s)", HELPER_NAME, cid[:12])
        return cid
