"""蜜柑全量导入 cookie 解析:三种粘贴形式都能拼进 httpx Cookie 头而不报 Illegal header value。

回归自「用户贴了 Cookie 扩展导出的整段 JSON → 整段被当成 .AspNetCore.Identity.Application
的值 → httpx 拼 Cookie 头炸 Illegal header value」这个 bug。
"""
import httpx
import pytest

from app.services.mikan_full_import import _parse_cookie, _serialize_cookie

# Cookie-Editor / EditThisCookie 导出的真实结构(值已改写,base64url、无 = 号)
JSON_EXPORT = """[
  {"domain": "mikanani.me", "name": "mikan-announcement", "value": "10"},
  {"domain": "mikanani.me", "httpOnly": true, "name": ".AspNetCore.Antiforgery.dIJyUX9c7XY",
   "value": "CfDJ8Gm_wMGJKtQ58HXHTm3Snmallgv-yIQ1qNmWlMxTeiVRvbbfxm1ufopKRRnNl5K"},
  {"domain": "mikanani.me", "httpOnly": true, "name": ".AspNetCore.Identity.Application",
   "value": "CfDJ8GmEyw_wMGJKtQ58HXHTm3TaxHK-gedD2q0siRhKh-Uc3dUfl0_bLWagldFC3jXg5i"}
]"""


def _builds_header(cookies: dict) -> str:
    """httpx 会在这里对非法 header 值抛错;返回拼好的 Cookie 头。"""
    req = httpx.Request("GET", "https://mikanani.me/Home/MyBangumi", cookies=cookies)
    return req.headers.get("cookie", "")


def test_json_export_is_parsed():
    got = _parse_cookie(JSON_EXPORT)
    assert set(got) == {
        "mikan-announcement",
        ".AspNetCore.Antiforgery.dIJyUX9c7XY",
        ".AspNetCore.Identity.Application",
    }
    # 关键:能拼进 httpx Cookie 头,不再 Illegal header value
    header = _builds_header(got)
    assert ".AspNetCore.Identity.Application=CfDJ8GmEyw" in header
    assert "\n" not in header


def test_json_export_roundtrips_through_settings():
    """存进设置的是规范化单行,重新读取得到同一组 cookie。"""
    parsed = _parse_cookie(JSON_EXPORT)
    stored = _serialize_cookie(parsed)
    assert stored.startswith("mikan-announcement=10; ")
    assert _parse_cookie(stored) == parsed


def test_cookie_header_line():
    got = _parse_cookie("a=1; .AspNetCore.Identity.Application=XYZ; b=2")
    assert got[".AspNetCore.Identity.Application"] == "XYZ"
    assert _builds_header(got)


def test_bare_value():
    got = _parse_cookie("  CfDJ8bareValueNoEquals  ")
    assert got == {".AspNetCore.Identity.Application": "CfDJ8bareValueNoEquals"}
    assert _builds_header(got)


def test_bare_value_strips_wrapped_whitespace():
    # 复制时被换行截断的裸值:去掉空白后仍是合法 header 值
    got = _parse_cookie("CfDJ8part1\n  part2")
    assert got == {".AspNetCore.Identity.Application": "CfDJ8part1part2"}
    assert _builds_header(got)


def test_empty():
    assert _parse_cookie("") == {}
    assert _parse_cookie("   ") == {}


def test_invalid_json_falls_back_gracefully():
    # 以 [ 开头但不是合法 JSON:不崩,退回裸值分支(去空白)
    got = _parse_cookie("[not json at all")
    assert got == {".AspNetCore.Identity.Application": "[notjsonatall"}
