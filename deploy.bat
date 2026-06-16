@echo off
setlocal enableextensions
cd /d "%~dp0"
title Mikanarr deploy

REM ASCII-only on purpose (a chcp/UTF-8 .bat can fail to run on double-click).

where docker >nul 2>&1
if errorlevel 1 (
  echo [X] Docker not found. Please install and START Docker Desktop, then run this again.
  echo.
  pause
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo [X] Docker is installed but not running. Start Docker Desktop and wait until ready.
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="down" (
  docker compose down
  pause
  exit /b 0
)
if /i "%~1"=="logs" (
  docker compose logs -f mikanarr
  exit /b 0
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [!] Created .env. Notepad will open - fill in NAS path/credentials, SAVE,
  echo     then double-click this script again.
  echo.
  notepad ".env"
  pause
  exit /b 1
)

echo Building and starting Mikanarr (first build pulls base images, ~2-4 min)...
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo [X] Build/start failed. If it timed out pulling base images
  echo     ^(auth.docker.io / registry-1.docker.io^) you cannot reach Docker Hub:
  echo     add registry-mirrors in Docker Desktop ^> Settings ^> Docker Engine, then retry
  echo     ^(see DEPLOY.md^). Otherwise check your .env.
  echo.
  pause
  exit /b 1
)
echo.
echo [OK] Mikanarr started.  WebUI:  http://localhost:8008
echo      Logs:  deploy.bat logs
echo      Stop:  deploy.bat down
echo.
pause
