@echo off
cd /d "%~dp0"
echo Installing/checking dependencies...
python -m pip install -r requirements.txt --quiet
echo.
echo Starting Hyundai Dealership Dashboard...
python run.py
pause
