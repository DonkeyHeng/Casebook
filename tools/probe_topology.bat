@echo off
rem aspen-insight: read-only topology / enthalpy probe launcher
rem Double-click this file with Aspen Plus open (model loaded and already run).
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================================
echo  aspen-insight : topology / enthalpy probe   (READ-ONLY)
echo  Needs: Aspen Plus open, model loaded, model already run
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

python -m tools.probe_topology %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FINISHED WITH PROBLEMS] exit code %RC% -- read the messages above.
  echo If Aspen is not running, open the model and run this file again.
) else (
  echo [OK] report saved to:  data\probe_topology.txt
  echo      send that file back as-is.
)
echo.
pause
endlocal
