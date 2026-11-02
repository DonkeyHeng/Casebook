@echo off
rem aspen-insight launcher: check env -> start server -> wait for port -> open browser
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
    echo [ERROR] Python not found. Install Python 3 and enable 'Add python.exe to PATH'.
    echo.
    pause
    exit /b 1
  )
  set "PY=py -3"
)

rem ---------- 2) dependencies ----------
%PY% -c "import win32com.client, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Missing dependencies. Run in this folder:
  echo     python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

rem ---------- 3) already running -> just open the page ----------
%PY% -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>nul
if not errorlevel 1 (
  echo Service already running, opening the page.
  start "" http://127.0.0.1:8000
  exit /b 0
)

rem ---------- 4) start the server ----------
start "aspen-insight server" cmd /k %PY% -m app.api --port 8000

rem wait for the port (max 15 s). ping is used instead of timeout:
rem Git Bash / MSYS ships a GNU timeout that shadows the Windows one.
set /a tries=0
:wait
ping -n 2 127.0.0.1 >nul 2>nul
set /a tries+=1
%PY% -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>nul
if not errorlevel 1 goto up
if %tries% lss 15 goto wait

echo.
echo [ERROR] Service did not start: 127.0.0.1:8000 is not responding.
echo Open the 'aspen-insight server' window for the traceback. Common causes:
echo   1) dependencies not installed: python -m pip install -r requirements.txt
echo   2) port 8000 already in use: python -m app.api --port 8001
echo.
pause
exit /b 1

:up
echo Service started: http://127.0.0.1:8000
rem the server opens the page itself (a browser tab by default); opening one here would duplicate it
echo The page should open in your browser. If it does not, open the URL above manually.
endlocal
