# Posture AI

A webcam posture monitor for Windows. It watches your shoulders and head through the camera, warns you with a beep and a voice reminder when you slouch, and keeps a log so you can see your posture improve over the week.

Everything runs on your own computer. No video is saved or sent anywhere.

![Control panel](screenshots/control-panel.png)

## Features

- **Real-time posture detection** using MediaPipe Pose and OpenCV: shoulder tilt and forward-head position.
- **Calibration**: sit upright once and the app measures your own baseline instead of using fixed numbers.
- **Health bar game loop**: good posture refills the bar, bad posture drains it, so one slouch is not an alarm.
- **Alerts**: a beep when health runs low, and a spoken reminder only after bad posture persists (default 5 s).
- **Logging and reports**: every session is saved to CSV; a weekly report and charts show good vs bad posture over time.
- **Control panel (Tkinter)**: start/stop, calibrate, sensitivity sliders, sound and voice settings, weekly summary.
- **System tray icon**: green / red / orange for good / bad / idle, with a menu for the common actions.
- **Start with Windows** option, single-instance protection, and a validated `settings.json` that reloads while the app runs.
- **Windows installer** (PyInstaller + Inno Setup) with Start Menu and Desktop shortcuts.

## Install (Windows 10/11)

1. Download `PostureAI-Setup.exe` from the [Releases](../../releases) page.
2. Run it. Windows SmartScreen may warn because the installer is not code-signed: click **More info → Run anyway**.
3. Open **Posture AI**, press **Start monitoring**, then **Calibrate now** while sitting upright.

Your data (log, settings, charts) is stored in `%APPDATA%\PostureAI`.

## Run from source

Requires **Python 3.12** (MediaPipe's `mp.solutions.pose` API is only in `mediapipe<=0.10.21`).

```bat
py -3.12 -m venv .venv312
.venv312\Scripts\activate
pip install -r requirements.txt
python posture_app.py
```

`run_panel.bat` opens the control panel, `run_posture.bat` runs the monitor window directly, and `show_report.bat` opens the weekly report.

## Build the .exe and installer

```bat
build_exe.bat        :: PyInstaller -> dist\PostureAI\PostureAI.exe
make_installer.bat   :: Inno Setup 6 -> installer_output\PostureAI-Setup.exe
```

## How it works

- **Shoulder tilt**: the angle of the line between the two shoulder landmarks.
- **Head-forward**: the distance from the nose to the shoulder midpoint, divided by shoulder width, compared with your calibrated baseline.
- **Control panel to monitor**: the panel and the monitor are separate processes. The panel sends commands through small flag files and reads the monitor's `status.json`.

| File | Purpose |
| --- | --- |
| `posture_app.py` | Entry point (panel, monitor, or report) |
| `posture_monitor.py` | Camera loop, detection, alerts, tray icon |
| `posture_gui.py` | Tkinter control panel |
| `posture_report.py` | Weekly summary and charts |
| `posture_common.py` | Paths, settings, autostart, panel/monitor messaging |
| `PostureAI.spec`, `installer.iss` | Packaging |

## Tech stack

Python 3.12, MediaPipe Pose, OpenCV, Tkinter, pystray, pyttsx3, matplotlib, PyInstaller, Inno Setup.

## Limitations

- Windows only. A side-on or very dark camera view reduces accuracy.
- The installer is unsigned, so SmartScreen shows a warning on first run.

## Author

Onabiyi Samson Ayomide, Computer Engineering, University of Lagos.
