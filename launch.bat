@echo off
if not defined MINIMIZED (
    set MINIMIZED=1
    start /min "" "%~f0"
    exit /b
)
cd /d "%~dp0"
python session_manager.py
if errorlevel 1 (
    echo.
    echo ERROR: VibeNode failed to start. See message above.
    pause
)
