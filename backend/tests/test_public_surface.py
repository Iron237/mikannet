"""公开静态资源边界：运行数据与凭据不得经 Web 直接下载。"""

from app.config import Settings
from app.main import app
from app.services import backup, settings_service


def test_only_image_cache_is_mounted_as_static_data():
    mounts = {getattr(route, "path", None) for route in app.routes}
    assert "/data/images" in mounts
    assert "/data" not in mounts


def test_default_downloader_passwords_are_empty():
    assert Settings.model_fields["qb_password"].default == ""
    assert Settings.model_fields["bitcomet_password"].default == ""


def test_bitcomet_connection_is_configurable_but_not_exported():
    """UI 可完整配置 BitComet,但机器连接参数与密码不能进入可迁移备份。"""
    expected = {
        "bitcomet_host", "bitcomet_port", "bitcomet_username",
        "bitcomet_password", "bitcomet_download_root",
    }
    assert expected <= settings_service.EDITABLE.keys()
    assert expected <= backup._SETTING_DENYLIST
