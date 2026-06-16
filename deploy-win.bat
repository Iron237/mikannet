@echo off
setlocal enableextensions
cd /d "%~dp0"
title Mikanarr deploy

REM Mikanarr full-environment release for Windows.
REM The image is bundled (mikanarr-image.tar.gz) - no build, no Docker Hub pull.
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
  docker compose -f docker-compose.release.yml logs -f mikanarr
  exit /b 0
)

REM 1) Load the bundled image if it is not present yet
docker image inspect mikanarr:latest >nul 2>&1
if errorlevel 1 (
  echo Loading image mikanarr:latest from mikanarr-image.tar.gz ^(~1 GB, first run is slow^)...
  docker load -i "mikanarr-image.tar.gz"
  if errorlevel 1 (
    echo [X] Failed to load image. Make sure mikanarr-image.tar.gz sits next to this script.
    echo.
    pause
    exit /b 1
  )
)

REM 2) First run: create .env from the template and open it for editing
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [!] Created .env. Notepad will open - fill in NAS path/credentials, SAVE,
  echo     then double-click this script again.
  echo.
  notepad ".env"
  pause
  exit /b 1
)

REM 3) Start using the pre-loaded image (no build, no Docker Hub)
echo Starting Mikanarr (using the bundled image)...
docker compose -f docker-compose.release.yml up -d
if errorlevel 1 (
  echo.
  echo [X] Start failed. Check the error above and your .env
  echo     - most often a wrong NAS path or credentials = CIFS mount failure.
  echo.
  pause
  exit /b 1
)
echo.
echo [OK] Mikanarr started.  WebUI:  http://localhost:8008
echo      Logs:  deploy-win.bat logs
echo      Stop:  deploy-win.bat down
echo.
pause
