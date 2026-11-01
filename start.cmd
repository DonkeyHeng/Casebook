@echo off
rem aspen-insight 启动器:起本地服务并自动打开浏览器
chcp 65001 >nul
cd /d "%~dp0"

echo Starting aspen-insight ...
start "aspen-insight server" cmd /k python -m app.api --port 8000
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8000
