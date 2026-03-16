@echo off
cd /d "%~dp0"
python session_manager.py
if errorlevel 1 (
    echo.
    echo ERROR: Claude Code GUI failed to start. See message above.
    pause
)
