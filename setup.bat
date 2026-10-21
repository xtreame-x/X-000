@echo off
setlocal EnableDelayedExpansion
title "Oryn Setup and Installation"

echo ===================================================
echo             ORYN SYSTEM AGENT SETUP
echo ===================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo [OK] Found %%i

:: 2. Install dependencies
echo.
echo [*] Installing dependencies from requirements.txt...
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [WARNING] Some dependencies may have failed to install. Continuing...
) else (
    echo [OK] All dependencies installed successfully.
)

:: 3. Setup .env and Gemini API Key
echo.
echo [*] Checking API Key configuration...
set "ENV_FILE=%~dp0.env"

if exist "%ENV_FILE%" (
    findstr /i "GEMINI_API_KEY" "%ENV_FILE%" >nul 2>&1
    if not errorlevel 1 goto has_key
    findstr /i "GOOGLE_API_KEY" "%ENV_FILE%" >nul 2>&1
    if not errorlevel 1 goto has_key
)

echo.
echo -------------------------------------------------------------
echo  Oryn is powered by Google Gemini (Free Tier: 15 RPM, 1M TPM)
echo  Get your free API key at: https://aistudio.google.com/
echo -------------------------------------------------------------
echo.
set /p "USER_GEMINI_KEY=Enter your Gemini / Google API Key: "
if defined USER_GEMINI_KEY (
    if not exist "%ENV_FILE%" (
        echo # Oryn Environment Variables > "%ENV_FILE%"
    )
    echo GEMINI_API_KEY=!USER_GEMINI_KEY!>> "%ENV_FILE%"
    echo [OK] Saved GEMINI_API_KEY to .env
) else (
    echo [WARNING] No API key entered. You can set GEMINI_API_KEY later in .env.
)
goto configure_path

:has_key
echo [OK] Gemini API Key already configured in .env.

:configure_path
:: 4. Add Oryn directory to User PATH
echo.
echo [*] Registering 'oryn' command in Windows PATH (Environment Properties)...
set "ORYN_DIR=%~dp0"
if "%ORYN_DIR:~-1%"=="\" set "ORYN_DIR=%ORYN_DIR:~0,-1%"

powershell -NoProfile -Command ^
    "$target = '%ORYN_DIR%';" ^
    "$userPath = [Environment]::GetEnvironmentVariable('Path', 'User');" ^
    "if ($userPath -split ';' -notcontains $target) {" ^
    "    $newPath = ($userPath.TrimEnd(';') + ';' + $target).Trim(';');" ^
    "    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User');" ^
    "    Write-Host '[OK] Added to User PATH: ' $target -ForegroundColor Green;" ^
    "} else {" ^
    "    Write-Host '[OK] Oryn is already in your PATH.' -ForegroundColor Green;" ^
    "}"

echo.
echo ===================================================
echo                 SETUP COMPLETE!
echo ===================================================
echo.
echo You can now open a new terminal anywhere and type:
echo.
echo      oryn
echo.
echo ===================================================
echo.
