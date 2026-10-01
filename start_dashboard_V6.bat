@echo off
cd /d "%~dp0"
title Hyundai Dashboard V6
echo ============================================================
echo  Hyundai Dashboard V6 - clean start
echo  Folder: %CD%
echo ============================================================
echo.
echo [1/4] Stopping ANY old dashboard server (python run.py / uvicorn)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run\.py|uvicorn' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
for %%P in (8000 8001 8002 8003 8004 8005 8100) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do taskkill /F /PID %%A >nul 2>&1
)
echo.
echo [2/4] Checking dependencies...
python -m pip install -r requirements.txt --quiet
echo.
echo [3/4] Starting on a NEW port (8100) so old browser tabs cannot interfere.
set PORT=8100
echo [4/4] Your browser will open http://127.0.0.1:8100
echo       Check the sidebar bottom says:  Build V6
echo       Close all OLD dashboard tabs (ports 8000 / 8001).
echo.
python run.py
pause
