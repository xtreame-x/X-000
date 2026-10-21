@echo off
setlocal
cd /d "%~dp0"
python "%~dp0auth_drive.py"
pause
endlocal
