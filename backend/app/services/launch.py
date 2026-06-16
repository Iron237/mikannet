"""原生启动:容器相对路径 → 宿主机真实路径 → mikanarr:// 协议 URL,并生成协议处理器安装包。

后端跑在 Linux 容器、UI 是浏览器,都无法直接拉起本机 explorer / 默认播放器 / PowerDVD。
方案:Windows 上注册自定义协议 mikanarr://,UI 点链接 → 浏览器唤起协议 → 本机 PowerShell
处理器按动作启动程序。处理器只放行「白名单根目录下 + 令牌匹配」的请求,挡掉其他网页滥用。
"""
from __future__ import annotations

import base64
import secrets
from urllib.parse import quote

from sqlalchemy import select

from app.config import settings
from app.database import db_session
from app.models import Setting

_TOKEN_KEY = "launch_token"
_token_cache: str | None = None


def get_token() -> str:
    """协议头防滥用令牌:首次取用时生成并持久化到 DB(与处理器、URL 始终一致)。"""
    global _token_cache
    if _token_cache:
        return _token_cache
    if settings.launch_token:
        _token_cache = settings.launch_token
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

def _handler_ps1() -> str:
    """生成本机协议处理器(PowerShell)。令牌/白名单根/PowerDVD 路径在生成时嵌入。"""
    roots = [r for r in (settings.media_host_root, settings.bd_owned_host_root) if r]
    roots_lit = ",".join("'" + r.replace("'", "''") + "'" for r in roots) or "''"
    token = get_token().replace("'", "''")
    pdvd = (settings.powerdvd_path or "").replace("'", "''")
    return f"""param([string]$Uri)
$ErrorActionPreference = 'SilentlyContinue'
$TOKEN = '{token}'
$ROOTS = @({roots_lit})
$POWERDVD = '{pdvd}'
if ($Uri -notmatch '^mikanarr://([a-zA-Z]+)/?\\?(.+)$') {{ exit 1 }}
$action = $Matches[1].ToLower()
$path = $null; $tok = $null
foreach ($p in $Matches[2].Split('&')) {{
  $i = $p.IndexOf('=')
  if ($i -lt 0) {{ continue }}
  $k = $p.Substring(0, $i); $v = [Uri]::UnescapeDataString($p.Substring($i + 1))
  if ($k -eq 'path') {{ $path = $v }} elseif ($k -eq 'token') {{ $tok = $v }}
}}
if ($tok -ne $TOKEN -or -not $path) {{ exit 2 }}
$pl = $path.ToLower()
$ok = $false
foreach ($r in $ROOTS) {{ if ($r -and $pl.StartsWith($r.ToLower())) {{ $ok = $true; break }} }}
if (-not $ok) {{ exit 3 }}
switch ($action) {{
  'play'   {{ Start-Process -FilePath $path }}
  'reveal' {{ Start-Process explorer.exe -ArgumentList ('/select,"{{0}}"' -f $path) }}
  'bd'     {{
    $pd = $POWERDVD
    if (-not $pd -or -not (Test-Path $pd)) {{
      $pd = $null
      foreach ($g in @("$env:ProgramFiles\\CyberLink\\PowerDVD*\\PowerDVD*.exe",
                       "${{env:ProgramFiles(x86)}}\\CyberLink\\PowerDVD*\\PowerDVD*.exe")) {{
        $f = Get-ChildItem -Path $g -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($f) {{ $pd = $f.FullName; break }}
      }}
    }}
    if ($pd) {{ Start-Process -FilePath $pd -ArgumentList ('"{{0}}"' -f $path) }}
    else {{ Start-Process explorer.exe -ArgumentList ('"{{0}}"' -f $path) }}
  }}
}}
"""


def installer_bat() -> str:
    """生成自安装 .bat:把处理器写到 %LOCALAPPDATA%\\mikanarr\\handler.ps1 + 注册 mikanarr:// 协议。"""
    # ps1 以 UTF-8(含 BOM)落盘,保证 PowerShell 正确读取其中的中文路径
    ps1_bytes = b"\xef\xbb\xbf" + _handler_ps1().encode("utf-8")
    b64 = base64.b64encode(ps1_bytes).decode("ascii")
    cmd = (
        'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
        '-File \\"%DIR%\\handler.ps1\\" \\"%%1\\"'
    )
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "DIR=%LOCALAPPDATA%\\mikanarr"\r\n'
        'if not exist "%DIR%" mkdir "%DIR%"\r\n'
        f'set "B64={b64}"\r\n'
        "powershell -NoProfile -Command "
        "\"[IO.File]::WriteAllBytes('%DIR%\\handler.ps1',"
        "[Convert]::FromBase64String($env:B64))\"\r\n"
        'reg add "HKCU\\Software\\Classes\\mikanarr" /ve /t REG_SZ '
        '/d "URL:Mikanarr Protocol" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikanarr" /v "URL Protocol" /t REG_SZ /d "" /f >nul\r\n'
        'reg add "HKCU\\Software\\Classes\\mikanarr\\shell\\open\\command" /ve /t REG_SZ '
        f'/d "{cmd}" /f >nul\r\n'
        "echo.\r\n"
        "echo Mikanarr protocol handler installed:\r\n"
        "echo   %DIR%\\handler.ps1\r\n"
        "echo You can now click play / open / PowerDVD buttons in the web UI.\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
