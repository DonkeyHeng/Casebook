@echo off
rem Casebook: read-only stream vapor-pressure probe launcher
rem Double-click with Aspen Plus open (model loaded and already run).
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================================
echo  Casebook : vapor pressure (p_v) probe   (READ-ONLY)
echo  Needs: Aspen Plus open, model loaded and run
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] "python" was not found in PATH.
  echo         Install Python 3 first, or run this from a terminal where python works.
  echo.
  pause
  exit /b 1
)

python -m tools.probe_pv --save %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FINISHED WITH PROBLEMS] exit code %RC% -- read the messages above.
) else (
  echo [OK] report saved to:  probe_out\probe_pv.txt
  echo      tell the assistant when it is done.
)
echo.
pause
endlocal
