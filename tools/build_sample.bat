@echo off
rem Casebook: rebuild the public sample database from the open Aspen model
rem Double-click with Aspen Plus open, sample model loaded and already run.
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================================
echo  Casebook : rebuild samples\case-23-error.db   (Aspen stays read-only)
echo  Needs: Aspen open, the sample model loaded and already run
echo  Takes about 40 s (reads every stream composition)
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

python -m tools.build_sample %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FINISHED WITH PROBLEMS] exit code %RC% -- read the messages above.
  echo If Aspen is not running, open the model and run this file again.
) else (
  echo [OK] sample rebuilt:  samples\case-23-error.db
  echo      tell the assistant when it is done.
)
echo.
pause
endlocal
