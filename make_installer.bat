@echo off
rem Step 2 of 2: packs dist\PostureAI into one installer: installer_output\PostureAI-Setup.exe
rem Needs Inno Setup 6.3+ (free): https://jrsoftware.org/isdl.php
cd /d "%~dp0"

if not exist "dist\PostureAI\PostureAI.exe" (
    echo dist\PostureAI\PostureAI.exe not found. Run build_exe.bat first.
    pause
    exit /b 1
)

set "ISCC="
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Inno Setup was not found. Install it from https://jrsoftware.org/isdl.php and run this again.
    pause
    exit /b 1
)

"%ISCC%" installer.iss
if errorlevel 1 (
    echo.
    echo The installer build failed. Copy the last lines above and send them to Claude.
    pause
    exit /b 1
)

echo.
echo Done. Your installer is: %~dp0installer_output\PostureAI-Setup.exe
pause
