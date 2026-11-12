@echo off
rem Starts the posture monitor hidden: only the tray icon (bottom-right) shows.
cd /d "%~dp0"
if not exist ".venv312\Scripts\pythonw.exe" (
    echo Could not find .venv312 in this folder: %~dp0
    pause
    exit /b 1
)
start "" ".venv312\Scripts\pythonw.exe" posture_monitor.py --background
