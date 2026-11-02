@echo off
rem aspen-insight 启动器:检查环境 -> 起本地服务 -> 确认端口可用 -> 打开浏览器
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo Starting aspen-insight ...
echo.

rem ---------- 1) Python ----------
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo [错误] 没有找到 Python。
    echo 请先安装 Python 3,安装时勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
  )
  set "PY=py -3"
)

rem ---------- 2) 依赖 ----------
%PY% -c "import win32com.client, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo [错误] 缺少依赖。请在本目录执行:
  echo     python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

rem ---------- 3) 已经在跑就直接开页面 ----------
%PY% -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>nul
if not errorlevel 1 (
  echo 服务已经在运行,直接打开页面。
  start "" http://127.0.0.1:8000
  exit /b 0
)

rem ---------- 4) 起服务 ----------
start "aspen-insight server" cmd /k %PY% -m app.api --port 8000

rem 等端口真的起来(最多 15 秒);起不来就把原因留在屏幕上
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
set /a tries+=1
%PY% -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>nul
if not errorlevel 1 goto up
if %tries% lss 15 goto wait

echo.
echo [错误] 服务没有启动(127.0.0.1:8000 无响应)。
echo 请看 "aspen-insight server" 窗口里的报错,常见原因:
echo   1) 依赖没装:python -m pip install -r requirements.txt
echo   2) 8000 端口被别的程序占用:改用 python -m app.api --port 8001
echo.
pause
exit /b 1

:up
echo 服务已启动:http://127.0.0.1:8000
start "" http://127.0.0.1:8000
endlocal
