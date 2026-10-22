@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ============================================================
echo    ORYN - NOTION 1-CLICK LOGIN
echo  ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  [error] Python was not found on PATH.
    echo  Install Python 3.10+ from https://python.org and run this again.
    echo.
    pause
    exit /b 1
)

python "%~dp0auth_notion.py" login
set "ORYN_AUTH_CODE=%ERRORLEVEL%"

echo.
if not "%ORYN_AUTH_CODE%"=="0" (
    echo  [notice] The 1-click login did not finish.
    echo  Opening the full menu so you can add or test credentials...
    echo.
    python "%~dp0auth_notion.py" menu
)

echo.
pause
endlocal
