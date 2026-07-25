"""公开静态资源边界：运行数据与凭据不得经 Web 直接下载。"""

from app.config import Settings
from app.main import app


def test_only_image_cache_is_mounted_as_static_data():
    mounts = {getattr(route, "path", None) for route in app.routes}
    assert "/data/images" in mounts
    assert "/data" not in mounts


def test_default_downloader_passwords_are_empty():
    assert Settings.model_fields["qb_password"].default == ""
    assert Settings.model_fields["bitcomet_password"].default == ""
