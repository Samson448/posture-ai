@echo off
rem Opens the Posture AI control panel (no console window) using the .venv312 environment.
cd /d "%~dp0"
if not exist ".venv312\Scripts\pythonw.exe" (
    echo Could not find .venv312 in this folder: %~dp0
    pause
    exit /b 1
)
start "" ".venv312\Scripts\pythonw.exe" posture_app.py
