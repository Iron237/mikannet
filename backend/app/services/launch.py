"""原生启动:容器相对路径 → 宿主机真实路径 → mikanarr:// 协议 URL,并生成协议处理器安装包。

后端跑在 Linux 容器、UI 是浏览器,都无法直接拉起本机 explorer / 默认播放器 / PowerDVD。
方案:Windows 上注册自定义协议 mikanarr://,UI 点链接 → 浏览器唤起协议 → 本机 JScript 处理器
(经 wscript 无窗口运行,无控制台闪)按动作启动程序。处理器只放行「白名单根目录下 + 令牌匹配」
的请求,挡掉其他网页滥用。安装包用 certutil 解码、reg 注册,全程不依赖 PowerShell。
"""
from __future__ import annotations

import base64
import secrets
import threading
from urllib.parse import quote

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


def launch_url(action: str, host_path: str | None) -> str | None:
    """构造 mikanarr://<action>?path=&token= 协议 URL;host_path 为空(未配置根)→ None。"""
    if not host_path:
        return None
    return (f"mikanarr://{action}?path={quote(host_path, safe='')}"
            f"&token={quote(get_token(), safe='')}")


def media_launch(action: str, relative_path: str) -> str | None:
    return launch_url(action, media_host_path(relative_path))


def configured() -> bool:
    return bool(settings.media_host_root or settings.bd_owned_host_root)


# ---- 协议处理器安装包(自包含 .bat,双击即装)--------------------------------

def _handler_js() -> str:
    """生成本机协议处理器(JScript,经 wscript 无窗口运行 → 无控制台闪)。

    令牌/白名单根/PowerDVD 路径以 JSON 字面量嵌入(json.dumps 把反斜杠转义、中文转 \\uXXXX,
    故整份 .js 是纯 ASCII,落盘无编码问题)。decodeURIComponent 正确还原中文路径。
    """
    import json
    roots = [r for r in (settings.media_host_root, settings.bd_owned_host_root) if r]
    js = r'''var TOKEN = __TOKEN__;
var ROOTS = __ROOTS__;
var POWERDVD = __POWERDVD__;
var uri = WScript.Arguments.length ? WScript.Arguments(0) : "";
var m = /^mikanarr:\/\/([a-zA-Z]+)\/?\?(.+)$/.exec(uri);
if (!m) { WScript.Quit(1); }
var action = m[1].toLowerCase();
var parts = m[2].split("&"), path = "", token = "";
for (var i = 0; i < parts.length; i++) {
  var eq = parts[i].indexOf("=");
  if (eq < 0) { continue; }
  var k = parts[i].substring(0, eq), v = decodeURIComponent(parts[i].substring(eq + 1));
  if (k === "path") { path = v; } else if (k === "token") { token = v; }
}
if (token !== TOKEN || !path) { WScript.Quit(2); }
var pl = path.toLowerCase(), ok = false;
for (var j = 0; j < ROOTS.length; j++) {
  var r = ROOTS[j].toLowerCase().replace(/[\\]+$/, "");
  if (pl === r || pl.indexOf(r + "\\") === 0) { ok = true; break; }   // exact or boundary match
}
if (!ok) { WScript.Quit(3); }
var sh = new ActiveXObject("Shell.Application");
var wsh = new ActiveXObject("WScript.Shell");
var fso = new ActiveXObject("Scripting.FileSystemObject");
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
  wsh.Run('explorer.exe /select,"' + path + '"', 1, false);
} else if (action === "bd") {
  var pd = POWERDVD;
  if (!pd || !fso.FileExists(pd)) { pd = findPowerDVD(); }
  if (pd) { wsh.Run('"' + pd + '" "' + path + '"', 1, false); }
  else { wsh.Run('explorer.exe "' + path + '"', 1, false); }  // fallback: open disc folder
}
'''
    return (js.replace("__TOKEN__", json.dumps(get_token()))
              .replace("__ROOTS__", json.dumps(roots))
              .replace("__POWERDVD__", json.dumps(settings.powerdvd_path or "")))


def installer_bat() -> str:
    """生成自安装 .bat:写入 JScript 处理器(%LOCALAPPDATA%\\mikanarr\\handler.js)+ 注册
    mikanarr:// → wscript(无窗口闪)。全程不用 PowerShell:certutil 解 base64,reg 写注册表。"""
    b64 = base64.b64encode(_handler_js().encode("ascii")).decode("ascii")   # 纯 ASCII
    cmd = 'wscript.exe \\"%DIR%\\handler.js\\" \\"%%1\\"'
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "DIR=%LOCALAPPDATA%\\mikanarr"\r\n'
        'if not exist "%DIR%" mkdir "%DIR%"\r\n'
        f'set "B64={b64}"\r\n'
        '> "%DIR%\\handler.b64" echo %B64%\r\n'
        'certutil -decode -f "%DIR%\\handler.b64" "%DIR%\\handler.js" >nul\r\n'
        'del "%DIR%\\handler.b64" >nul 2>&1\r\n'
        'reg add "HKCU\\Software\\Classes\\mikanarr" /ve /t REG_SZ '
        '/d "URL:Mikanarr Protocol" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikanarr" /v "URL Protocol" /t REG_SZ /d "" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikanarr\\shell\\open\\command" /ve /t REG_SZ '
        f'/d "{cmd}" /f >nul\r\n'
        "echo.\r\n"
        "echo Mikanarr protocol handler installed (JScript via wscript - no console flash):\r\n"
        "echo   %DIR%\\handler.js\r\n"
        "echo On first click the browser asks once to open Mikanarr - tick "
        "\"Always allow\", then it is seamless.\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
