@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
  echo [X] 未找到 docker,请先安装并启动 Docker Desktop。
  pause & exit /b 1
)

if /i "%~1"=="down" ( docker compose down & pause & exit /b 0 )
if /i "%~1"=="logs" ( docker compose logs -f mikanarr & exit /b 0 )

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [!] 已生成 .env —— 即将打开记事本,请填写 NAS 路径/凭据后保存,再重新运行 deploy.bat
  notepad ".env"
  pause & exit /b 1
)

echo ^> 构建并启动 Mikanarr ...
docker compose up -d --build
if errorlevel 1 (
  echo [X] 启动失败,请检查上方错误与 .env 配置。
  echo     若是拉取基础镜像超时^(auth.docker.io / registry-1.docker.io^) = 连不上 Docker Hub:
  echo     Docker Desktop ^> Settings ^> Docker Engine 加 registry-mirrors 后 Apply ^& Restart^(见 DEPLOY.md^),再重试。
  pause & exit /b 1
)
echo.
echo [OK] 已启动。WebUI:  http://localhost:8008
echo      查看日志:        deploy.bat logs
echo      停止:            deploy.bat down
pause
