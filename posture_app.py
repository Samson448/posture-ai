"""Posture AI: single entry point (this is what PostureAI.exe runs).

    PostureAI.exe               control panel (start/stop, settings, weekly summary)
    PostureAI.exe --background  the monitor, hidden, with a tray icon (used by Start with Windows)
    PostureAI.exe --monitor     the monitor with the camera window shown
    PostureAI.exe --report      the weekly report and charts

The same flags work in script mode:  python posture_app.py [flag]
"""
import multiprocessing
import sys


def main():
    args = sys.argv[1:]
    if "--report" in args:
        import posture_report
        posture_report.main()
    elif "--monitor" in args or "--background" in args:
        import posture_monitor  # loads OpenCV + MediaPipe, so only done when really monitoring
        posture_monitor.main()
    else:
        import posture_gui
        posture_gui.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
