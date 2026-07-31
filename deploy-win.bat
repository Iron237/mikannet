@echo off
setlocal enableextensions
cd /d "%~dp0"
title Mikannet deploy

REM Mikannet full-environment release for Windows.
REM The image is bundled (mikannet-image.tar.gz) - no build, no Docker Hub pull.
REM ASCII-only on purpose: avoids the chcp/UTF-8 .bat parsing issue that made it not run.

where docker >nul 2>&1
if errorlevel 1 (
  echo [X] Docker not found. Please install and START Docker Desktop, then run this again.
  echo.
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [X] Docker is installed but not running. Start Docker Desktop and wait until it is ready.
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="down" (
  docker compose -f docker-compose.release.yml down
  pause
  exit /b 0
)
if /i "%~1"=="logs" (
  docker compose -f docker-compose.release.yml logs -f mikannet
  exit /b 0
)

REM 1) Always load the bundled image (so THIS version runs, even if an older
REM    mikannet:latest from a previous deploy is still present - load re-points the tag).
echo Loading bundled image from mikannet-image.tar.gz ^(~1 GB, may take a minute^)...
docker load -i "mikannet-image.tar.gz"
if errorlevel 1 (
  echo [X] Failed to load image. Make sure mikannet-image.tar.gz sits next to this script.
  echo.
  pause
  exit /b 1
)

REM 2) Start using the pre-loaded image (no build, no Docker Hub, no .env needed).
REM    NAS / proxy / qB are configured in the WebUI setup wizard on first open.
echo Starting Mikannet (using the bundled image)...
docker compose -f docker-compose.release.yml up -d
if errorlevel 1 (
  echo.
  echo [X] Start failed. See the error above. (Docker running? port 8008 free?)
  echo.
  pause
  exit /b 1
)
set "MK_HANDLER=%TEMP%\mikannet-handler-install.bat"
echo Installing Windows browser / Explorer integration...
for /l %%I in (1,1,30) do (
  curl.exe -fsS --max-time 4 "http://127.0.0.1:8008/api/launch/handler.bat?origin=http://localhost:8008&quiet=true" -o "%MK_HANDLER%" 2>nul
  if not errorlevel 1 goto handler_ready
  timeout /t 2 /nobreak >nul
)
echo [!] Windows integration was not installed automatically. Retry from Settings later.
goto handler_done
:handler_ready
call "%MK_HANDLER%" >nul
del "%MK_HANDLER%" >nul 2>&1
echo [OK] Windows integration installed for this user.
:handler_done
echo.
echo [OK] Mikannet started.
echo.
echo   Open  http://localhost:8008  in your browser.
echo   First time: a SETUP WIZARD walks you through storage (local/SMB/NFS),
echo   downloader, proxy and metadata - all in the web UI, no text files to edit.
echo.
echo      Logs:  deploy-win.bat logs
echo      Stop:  deploy-win.bat down
echo.
pause
