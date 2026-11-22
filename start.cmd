@echo off
rem Casebook launcher: check env -> start server -> wait for server -> open browser
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo Starting Casebook ...
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
rem Ask the PID file, not the port: on machines with a network filter driver
rem (antivirus / firewall), connecting to a closed local port returns no RST and
rem connect_ex blocks for ~2 s on every launch. The server writes this file
rem right after it binds, so "file + live process" means the port is usable.
%PY% "%~dp0tools\server_state.py" --port 8000 --quiet >nul 2>nul
if not errorlevel 1 (
  echo Service already running, opening the page.
  start "" http://127.0.0.1:8000
  exit /b 0
)

rem ---------- 4) start the server ----------
start "Casebook server" cmd /k %PY% -m app.api --port 8000

rem wait for the server (max ~18 s). The helper polls the PID file and sleeps
rem inside, so no ping-based timer is needed here.
set /a tries=0
:wait
set /a tries+=1
%PY% "%~dp0tools\server_state.py" --port 8000 --wait 1 --quiet >nul 2>nul
if not errorlevel 1 goto up
if %tries% lss 15 goto wait

echo.
echo [ERROR] Service did not start: 127.0.0.1:8000 is not responding.
echo Open the 'Casebook server' window for the traceback. Common causes:
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
