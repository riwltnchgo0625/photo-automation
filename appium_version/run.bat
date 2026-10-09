@echo off
cd /d "%~dp0"
if not exist "config.json" (
  echo Copy config.example.json to config.json and configure device and locators.
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" collector.py run
) else (
  python collector.py run
)
set "collector_exit=%errorlevel%"
pause
exit /b %collector_exit%
