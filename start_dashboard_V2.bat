@echo off
cd /d "%~dp0"
echo Closing any old dashboard server on ports 8000-8005 ...
for %%P in (8000 8001 8002 8003 8004 8005) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do taskkill /F /PID %%A >nul 2>&1
)
echo.
echo Installing/checking dependencies...
python -m pip install -r requirements.txt --quiet
echo.
echo Starting Hyundai Dealership Dashboard (V2)...
python run.py
pause
