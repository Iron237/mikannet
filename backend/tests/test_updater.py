"""自更新 updater 回归:semver、check 的 code/full/none 判定、通道过滤、并发门控。

不触网:monkeypatch _list_releases / _fetch_manifest。依赖 app 包(httpx 等),
随 CI(pip install -e .[dev])/ 容器跑;裸机缺依赖会在 collection 跳过同其它 app 测试。
"""
import pytest

from app.services import update_gate, updater


def _releases():
    return [
        {"tag_name": "v0.1.0", "prerelease": False, "draft": False, "body": "base", "assets": []},
        {"tag_name": "v0.1.1", "prerelease": True, "draft": False, "body": "new",
         "assets": [
             {"name": "manifest.json", "browser_download_url": "http://x/manifest.json"},
             {"name": "mikannet-0.1.1-code.tar.gz", "size": 123456,
              "browser_download_url": "http://x/code.tgz"},
         ]},
    ]


def _patch(monkeypatch, base_rev_remote, current_base="baseAAA", current_ver="0.1.0"):
    monkeypatch.setattr(updater, "VERSION", current_ver)
    monkeypatch.setattr(updater, "BASE_REV", current_base)
    monkeypatch.setattr(updater, "_list_releases", _releases)

    def fake_manifest(release):
        if release["tag_name"] == "v0.1.1":
            return {"version": "0.1.1", "base_rev": base_rev_remote,
                    "image_ref": "ghcr.io/iron237/mikannet:0.1.1",
                    "code_package_url": "http://x/code.tgz", "code_sha256": "deadbeef",
                    "min_version": "0.1.0", "prerelease": True, "changelog": "new"}
        return {"version": "0.1.0", "base_rev": current_base, "changelog": "base"}

    monkeypatch.setattr(updater, "_fetch_manifest", fake_manifest)


def test_ver_key():
    assert updater.ver_key("0.1.1") > updater.ver_key("0.1.0")
    assert updater.ver_key("0.1.0") > updater.ver_key("0.1.0-rc.1")
    assert updater.ver_key("1.0.0") > updater.ver_key("0.9.9")


def test_check_code_when_base_rev_matches(monkeypatch):
    _patch(monkeypatch, base_rev_remote="baseAAA")          # 同 base_rev
    r = updater.check(include_prerelease=True)
    assert r["latest"] == "0.1.1"
    assert r["type"] == "code"
    assert r["size"] == 123456
    assert r["prerelease"] is True


def test_check_full_when_base_rev_differs(monkeypatch):
    _patch(monkeypatch, base_rev_remote="baseBBB")          # 不同 base_rev
    r = updater.check(include_prerelease=True)
    assert r["type"] == "full"


def test_check_none_when_prerelease_excluded(monkeypatch):
    _patch(monkeypatch, base_rev_remote="baseAAA")
    r = updater.check(include_prerelease=False)              # 0.1.1 是预发布 → 排除
    assert r["latest"] == "0.1.0"
    assert r["type"] == "none"


def test_check_full_when_too_old(monkeypatch):
    # 同 base_rev 但当前低于 min_version → 强制完整
    _patch(monkeypatch, base_rev_remote="baseAAA", current_ver="0.0.5")

    def manifest_minver(release):
        if release["tag_name"] == "v0.1.1":
            return {"version": "0.1.1", "base_rev": "baseAAA", "min_version": "0.1.0",
                    "image_ref": "ghcr.io/iron237/mikannet:0.1.1", "prerelease": True}
        return {"version": "0.1.0", "base_rev": "baseAAA"}
    monkeypatch.setattr(updater, "_fetch_manifest", manifest_minver)
    r = updater.check(include_prerelease=True)
    assert r["type"] == "full"


def test_apply_blocked_when_file_op_running(monkeypatch):
    _patch(monkeypatch, base_rev_remote="baseAAA")
    with update_gate.file_operation("test-move"):
        assert update_gate.busy_reason() is not None
        with pytest.raises(RuntimeError):
            updater.apply_code({"version": "0.1.1", "code_package_url": "http://x",
                                "code_sha256": "x"})
    assert update_gate.busy_reason() is None


def _symlink_replace_ok(tmp):
    import os
    try:
        a, b = tmp / "_a", tmp / "_b"
        a.mkdir()
        b.mkdir()
        ln, t = tmp / "_l", tmp / "_t"
        os.symlink(a, ln)
        os.symlink(b, t)
        os.replace(t, ln)
        ln.unlink()
        a.rmdir()
        b.rmdir()
        return True
    except (OSError, NotImplementedError):
        return False


def _make_targz(files: dict) -> bytes:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, content in files.items():
            data = content.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _seed_current(tmp_path, version="0.1.0"):
    import os
    g = tmp_path / "releases" / version / "backend" / "app"
    g.mkdir(parents=True)
    (g / "main.py").write_text("# good\n")
    os.symlink(tmp_path / "releases" / version, tmp_path / "current")


def test_apply_code_extract_repoint(tmp_path, monkeypatch):
    if not _symlink_replace_ok(tmp_path):
        pytest.skip("symlink 无权限")
    import hashlib
    import json
    monkeypatch.setattr(updater.settings, "code_dir", tmp_path)
    _seed_current(tmp_path, "0.1.0")
    blob = _make_targz({"backend/app/main.py": "# v011\n",
                        "frontend/dist/index.html": "<html></html>"})
    manifest = {"version": "0.1.1", "code_package_url": "http://x",
                "code_sha256": hashlib.sha256(blob).hexdigest()}
    monkeypatch.setattr(updater, "_download", lambda url: blob)
    monkeypatch.setattr(updater, "_quiesce", lambda: None)
    monkeypatch.setattr(updater, "_exit_soon", lambda *a, **k: None)
    updater.apply_code(manifest)
    assert (tmp_path / "releases" / "0.1.1" / "backend" / "app" / "main.py").exists()
    assert (tmp_path / "current").resolve().name == "0.1.1"
    pend = json.loads((tmp_path / "pending.json").read_text())
    assert pend["prev"] == "0.1.0" and pend["new"] == "0.1.1"


def test_apply_code_bad_sha_aborts(tmp_path, monkeypatch):
    if not _symlink_replace_ok(tmp_path):
        pytest.skip("symlink 无权限")
    monkeypatch.setattr(updater.settings, "code_dir", tmp_path)
    _seed_current(tmp_path, "0.1.0")
    blob = _make_targz({"backend/app/main.py": "# v011\n"})
    manifest = {"version": "0.1.1", "code_package_url": "http://x", "code_sha256": "WRONG"}
    monkeypatch.setattr(updater, "_download", lambda url: blob)
    monkeypatch.setattr(updater, "_quiesce", lambda: None)
    monkeypatch.setattr(updater, "_exit_soon", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        updater.apply_code(manifest)
    assert (tmp_path / "current").resolve().name == "0.1.0"   # 不切换
    assert not (tmp_path / "releases" / "0.1.1").exists()
