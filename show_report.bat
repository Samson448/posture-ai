@echo off
rem Opens the posture report charts using the .venv312 environment in this folder.
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
    echo Could not find .venv312 in this folder: %~dp0
    pause
    exit /b 1
)
".venv312\Scripts\python.exe" posture_report.py
echo.
pause
