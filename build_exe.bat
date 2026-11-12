@echo off
rem Step 1 of 2: turns the Python scripts into dist\PostureAI\PostureAI.exe (needs no Python to run).
rem Run this from the POSTURE AI folder. The first build takes several minutes.
cd /d "%~dp0"

if not exist ".venv312\Scripts\python.exe" (
    echo Could not find .venv312 in this folder: %~dp0
    pause
    exit /b 1
)
if not exist "app.ico" (
    echo app.ico is missing. Put it next to this file.
    pause
    exit /b 1
)

echo Installing build tools into .venv312 ...
".venv312\Scripts\python.exe" -m pip install pyinstaller pystray pyttsx3 pillow --default-timeout=120 --retries 10
if errorlevel 1 (
    echo.
    echo The download failed. Run this file again, it resumes where it stopped.
    pause
    exit /b 1
)

echo.
echo Building PostureAI.exe ...
".venv312\Scripts\python.exe" -m PyInstaller --noconfirm --clean PostureAI.spec
if errorlevel 1 (
    echo.
    echo The build failed. Copy the last lines above and send them to Claude.
    pause
    exit /b 1
)

echo.
echo Done. The app is in: %~dp0dist\PostureAI\PostureAI.exe
echo Test it by double-clicking it. Then run make_installer.bat to create the installer.
pause
