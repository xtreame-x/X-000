@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ============================================================
echo    LAUNCHING ORYN DASHBOARD UI
echo  ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  [error] Python was not found on PATH.
    echo  Please install Python 3.10+ from https://python.org.
    echo.
    pause
    exit /b 1
)

python "%~dp0oryn_ui.py"

pause
endlocal
