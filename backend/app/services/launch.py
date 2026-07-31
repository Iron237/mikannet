"""原生启动:容器相对路径 → 宿主机真实路径 → mikannet:// 协议 URL,并生成协议处理器安装包。

后端跑在 Linux 容器、UI 是浏览器,都无法直接拉起本机 explorer / 默认播放器 / PowerDVD。
方案:Windows 上注册自定义协议 mikannet://,UI 点链接 → 浏览器唤起协议 → 本机 JScript 处理器
(经 wscript 无窗口运行,无控制台闪)按动作启动程序。处理器只放行「白名单根目录下 + 令牌匹配」
的请求,挡掉其他网页滥用。安装包用 certutil 解码、reg 注册,全程不依赖 PowerShell。
"""
from __future__ import annotations

import base64
import ntpath
import secrets
import threading
from urllib.parse import quote, urlsplit

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Setting

_TOKEN_KEY = "launch_token"
_token_cache: str | None = None
_token_lock = threading.Lock()


def get_token() -> str:
    """协议头防滥用令牌:首次取用时生成并持久化到 DB(与处理器、URL 始终一致)。

    加锁 + 双检:tracker/worker/请求多线程首次并发取 token 时,避免两线程各生成各 INSERT
    同一主键 → UNIQUE constraint failed 致 500。
    """
    global _token_cache
    if _token_cache:
        return _token_cache
    if settings.launch_token:
        _token_cache = settings.launch_token
        return _token_cache
    with _token_lock:
        if _token_cache:
            return _token_cache
        with db_session() as db:
            row = db.get(Setting, _TOKEN_KEY)
            tok = (row.value or {}).get("v") if row else None
            if not tok:
                tok = secrets.token_urlsafe(18)
                if row is None:
                    db.add(Setting(key=_TOKEN_KEY, value={"v": tok}))
                else:
                    row.value = {"v": tok}
        settings.launch_token = tok
        _token_cache = tok
        return tok


def _join(root: str, rel: str) -> str:
    root = (root or "").replace("/", "\\").rstrip("\\")
    rel = (rel or "").replace("/", "\\").strip("\\")
    return f"{root}\\{rel}" if rel else root


def media_host_path(relative_path: str) -> str | None:
    """番剧库相对路径(相对 download_root)→ 宿主机路径;未配置根则 None。"""
    if not settings.media_host_root:
        return None
    return _join(settings.media_host_root, relative_path)


def owned_host_path(rel_under_mount: str) -> str | None:
    """已购原盘相对路径(相对 bd_owned_mount)→ 宿主机路径;未配置根则 None。"""
    if not settings.bd_owned_host_root:
        return None
    return _join(settings.bd_owned_host_root, rel_under_mount)


def data_host_path(rel: str = "") -> str | None:
    """data 目录相对路径(相对 data_dir,如 'logs')→ 宿主机路径;未配置 data 根则 None。"""
    if not settings.data_host_root:
        return None
    return _join(settings.data_host_root, rel)


def launch_url(action: str, host_path: str | None) -> str | None:
    """构造 mikannet://<action>?path=&token= 协议 URL;host_path 为空(未配置根)→ None。"""
    if not host_path:
        return None
    return (f"mikannet://{action}?path={quote(host_path, safe='')}"
            f"&token={quote(get_token(), safe='')}")


def media_launch(action: str, relative_path: str) -> str | None:
    return launch_url(action, media_host_path(relative_path))


def configured() -> bool:
    return bool(settings.media_host_root or settings.bd_owned_host_root)


def path_allowed(host_path: str) -> bool:
    """按当前设置动态校验 Windows 路径；协议处理器安装后路径变更无需重装。"""
    try:
        candidate = ntpath.normcase(ntpath.abspath(host_path))
    except (TypeError, ValueError):
        return False
    for raw_root in (settings.media_host_root, settings.bd_owned_host_root,
                     settings.data_host_root):
        if not raw_root:
            continue
        try:
            root = ntpath.normcase(ntpath.abspath(raw_root))
            if ntpath.commonpath([root, candidate]) == root:
                return True
        except (TypeError, ValueError):
            continue
    return False


# ---- 协议处理器安装包(自包含 .bat,双击即装)--------------------------------

def _handler_js(origin: str | None = None) -> str:
    """生成本机协议处理器(JScript,经 wscript 无窗口运行 → 无控制台闪)。

    令牌/白名单根/PowerDVD 路径以 JSON 字面量嵌入(json.dumps 把反斜杠转义、中文转 \\uXXXX,
    故整份 .js 是纯 ASCII,落盘无编码问题)。decodeURIComponent 正确还原中文路径。
    """
    import json
    roots = [r for r in (settings.media_host_root, settings.bd_owned_host_root,
                         settings.data_host_root) if r]
    safe_origin = origin if origin and _ORIGIN_RE.match(origin) else ""
    js = r'''var TOKEN = __TOKEN__;
var ROOTS = __ROOTS__;
var POWERDVD = __POWERDVD__;
var ORIGIN = __ORIGIN__;
var uri = WScript.Arguments.length ? WScript.Arguments(0) : "";
var m = /^mikannet:\/\/([a-zA-Z]+)\/?\?(.+)$/.exec(uri);
if (!m) { WScript.Quit(1); }
var action = m[1].toLowerCase();
var parts = m[2].split("&"), path = "", token = "", requestId = "";
for (var i = 0; i < parts.length; i++) {
  var eq = parts[i].indexOf("=");
  if (eq < 0) { continue; }
  var k = parts[i].substring(0, eq), v = decodeURIComponent(parts[i].substring(eq + 1));
  if (k === "path") { path = v; }
  else if (k === "token") { token = v; }
  else if (k === "request_id") { requestId = v; }
}
if (token !== TOKEN) { WScript.Quit(2); }
var fso = new ActiveXObject("Scripting.FileSystemObject");
var sh = new ActiveXObject("Shell.Application");
var wsh = new ActiveXObject("WScript.Shell");
function http(method, url) {
  var req = new ActiveXObject("WinHttp.WinHttpRequest.5.1");
  req.Open(method, url, false);
  req.SetTimeouts(3000, 3000, 5000, 5000);
  req.Send();
  return req;
}
if (action === "pick") {
  if (!ORIGIN || !requestId) { WScript.Quit(5); }
  var chosen = sh.BrowseForFolder(0, "\u9009\u62e9 Mikannet \u6587\u4ef6\u5939", 0x41, 0);
  if (!chosen) { WScript.Quit(0); }
  var chosenPath = chosen.Self.Path;
  try {
    var callback = ORIGIN + "/api/launch/selection?token=" + encodeURIComponent(TOKEN)
      + "&request_id=" + encodeURIComponent(requestId)
      + "&path=" + encodeURIComponent(chosenPath);
    var posted = http("POST", callback);
    if (posted.Status < 200 || posted.Status >= 300) { WScript.Quit(6); }
  } catch (e) { WScript.Quit(6); }
  WScript.Quit(0);
}
if (!path) { WScript.Quit(2); }
// resolve ".." before the whitelist test, else "root\..\elsewhere" passes the prefix
// check yet Windows opens the escaped path (whitelist bypass / path traversal).
try { path = fso.GetAbsolutePathName(path); } catch (e) { WScript.Quit(4); }
var pl = path.toLowerCase(), ok = false;
if (ORIGIN) {
  try {
    var checked = http("GET", ORIGIN + "/api/launch/validate?token="
      + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(path));
    ok = checked.Status >= 200 && checked.Status < 300;
  } catch (e) { ok = false; }
} else {
  for (var j = 0; j < ROOTS.length; j++) {
    var r = ROOTS[j].toLowerCase().replace(/[\\]+$/, "");
    if (pl === r || pl.indexOf(r + "\\") === 0) { ok = true; break; }
  }
}
if (!ok) { WScript.Quit(3); }
function findPowerDVD() {
  var bases = [wsh.ExpandEnvironmentStrings("%ProgramFiles%") + "\\CyberLink",
               wsh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") + "\\CyberLink"];
  for (var b = 0; b < bases.length; b++) {
    if (!fso.FolderExists(bases[b])) { continue; }
    var subs = new Enumerator(fso.GetFolder(bases[b]).SubFolders);
    for (; !subs.atEnd(); subs.moveNext()) {
      if (subs.item().Name.toLowerCase().indexOf("powerdvd") !== 0) { continue; }
      var files = new Enumerator(subs.item().Files);
      for (; !files.atEnd(); files.moveNext()) {
        if (/^powerdvd.*\.exe$/i.test(files.item().Name)) { return files.item().Path; }
      }
    }
  }
  return "";
}
if (action === "play") {
  sh.ShellExecute(path);                                   // default app, no window
} else if (action === "reveal") {
  // directory (e.g. BD "open folder"): open it directly to browse contents. /select only
  // highlights it in the parent and often falls back to Home for paths with special chars.
  // file (locate one episode): keep /select to highlight the file in its folder.
  if (fso.FolderExists(path)) { wsh.Run('explorer.exe "' + path + '"', 1, false); }
  else { wsh.Run('explorer.exe /select,"' + path + '"', 1, false); }
} else if (action === "bd") {
  var pd = POWERDVD;
  if (!pd || !fso.FileExists(pd)) { pd = findPowerDVD(); }
  if (pd) { wsh.Run('"' + pd + '" "' + path + '"', 1, false); }
  else { wsh.Run('explorer.exe "' + path + '"', 1, false); }  // fallback: open disc folder
}
'''
    return (js.replace("__TOKEN__", json.dumps(get_token()))
              .replace("__ROOTS__", json.dumps(roots))
              .replace("__POWERDVD__", json.dumps(settings.powerdvd_path or ""))
              .replace("__ORIGIN__", json.dumps(safe_origin)))


_ORIGIN_RE = __import__("re").compile(r"^https?://[A-Za-z0-9.\-]+(?::\d+)?$")


def _policy_lines(origin: str | None) -> tuple[str, str]:
    """据来源地址生成「免询问」浏览器策略(Chrome+Edge,HKCU,免管理员)+ 收尾提示。

    现代 Chrome/Edge 的外部协议对话框已无「始终允许」勾选框 → 每次唤起都弹窗。
    AutoLaunchProtocolsFromOrigins 把 mikannet 协议 + 本服务器地址列入免询问名单,根治弹窗。
    origin 缺失/不合法 → 不写策略(仅注册协议),退回旧提示。"""
    import json
    if not origin or not _ORIGIN_RE.match(origin):
        return "", ('echo If the browser asks to open Mikannet, allow it.\r\n')
    # 部署脚本只知道一个入口；把两个等价本机地址一起放行，用户切换
    # localhost / 127.0.0.1 后也不会重新看到外部协议确认框。
    origins = [origin]
    parsed = urlsplit(origin)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        alias_host = "127.0.0.1" if parsed.hostname == "localhost" else "localhost"
        alias = f"{parsed.scheme}://{alias_host}"
        if parsed.port:
            alias += f":{parsed.port}"
        origins.append(alias)
    # JSON 不能可靠地直接放进 reg.exe /d 参数，CMD 会吞掉其中的双引号。
    # 生成纯 ASCII .reg 再导入，复用 handler.js 已验证过的 certutil 解码路径。
    js = json.dumps([{"protocol": "mikannet", "allowed_origins": origins}],
                    separators=(",", ":"))
    reg_value = js.replace("\\", "\\\\").replace('"', '\\"')
    reg_file = (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        "[HKEY_CURRENT_USER\\Software\\Policies\\Google\\Chrome]\r\n"
        f'"AutoLaunchProtocolsFromOrigins"="{reg_value}"\r\n\r\n'
        "[HKEY_CURRENT_USER\\Software\\Policies\\Microsoft\\Edge]\r\n"
        f'"AutoLaunchProtocolsFromOrigins"="{reg_value}"\r\n'
    )
    policy_b64 = base64.b64encode(reg_file.encode("ascii")).decode("ascii")
    lines = (
        f'set "PB64={policy_b64}"\r\n'
        '> "%DIR%\\policy.b64" echo %PB64%\r\n'
        'certutil -decode -f "%DIR%\\policy.b64" "%DIR%\\policy.reg" >nul\r\n'
        'reg import "%DIR%\\policy.reg" >nul 2>&1\r\n'
        'if errorlevel 1 (set "MK_POLICY_OK=0") else (set "MK_POLICY_OK=1")\r\n'
        'del "%DIR%\\policy.b64" "%DIR%\\policy.reg" >nul 2>&1\r\n')
    note = (
        'if "%MK_POLICY_OK%"=="1" (\r\n'
        f'  echo Auto-launch whitelisted for {origin} (Chrome / Edge).\r\n'
        '  echo Restart your browser once, then native buttons work with no popup.\r\n'
        ') else (\r\n'
        '  echo Browser policy is protected and was not changed.\r\n'
        '  echo Web playback/file management needs no prompt; native buttons may ask once.\r\n'
        ')\r\n'
    )
    return lines, note


def installer_bat(origin: str | None = None, quiet: bool = False) -> str:
    """生成自安装 .bat:写入 JScript 处理器(%LOCALAPPDATA%\\mikannet\\handler.js)+ 注册
    mikannet:// → wscript(无窗口闪)+(给定 origin 时)写浏览器免询问策略,根治每次播放弹窗。
    全程不用 PowerShell:certutil 解 base64,reg 写注册表。"""
    b64 = base64.b64encode(_handler_js(origin).encode("ascii")).decode("ascii")
    cmd = 'wscript.exe \\"%DIR%\\handler.js\\" \\"%%1\\"'
    policy, note = _policy_lines(origin)
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "DIR=%LOCALAPPDATA%\\mikannet"\r\n'
        'if not exist "%DIR%" mkdir "%DIR%"\r\n'
        f'set "B64={b64}"\r\n'
        '> "%DIR%\\handler.b64" echo %B64%\r\n'
        'certutil -decode -f "%DIR%\\handler.b64" "%DIR%\\handler.js" >nul\r\n'
        'del "%DIR%\\handler.b64" >nul 2>&1\r\n'
        'reg add "HKCU\\Software\\Classes\\mikannet" /ve /t REG_SZ '
        '/d "URL:Mikannet Protocol" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikannet" /v "URL Protocol" /t REG_SZ /d "" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikannet\\shell\\open\\command" /ve /t REG_SZ '
        f'/d "{cmd}" /f >nul\r\n'
        + policy +
        "echo.\r\n"
        "echo Mikannet protocol handler installed (JScript via wscript - no console flash):\r\n"
        "echo   %DIR%\\handler.js\r\n"
        + note +
        "echo.\r\n"
        + ("" if quiet else "pause\r\n")
    )
