@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0bootstrap.py" setup
) else (
  python "%~dp0bootstrap.py" setup
)
if errorlevel 1 pause
