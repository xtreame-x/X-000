@echo off
setlocal
cd /d "%~dp0"
python "%~dp0oryn.py" %*
endlocal
