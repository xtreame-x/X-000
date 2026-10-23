@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ============================================================
echo    ORYN  //  DESKTOP DASHBOARD
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

python "%~dp0oryn_ui.py" %*

echo.
echo  Dashboard stopped.
pause
endlocal
