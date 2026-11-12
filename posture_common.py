"""Shared helpers for Posture AI: file locations, settings-file access, start-with-Windows,
launching the app's parts, and the small "flag file" channel the control panel uses to talk
to the background monitor.

Deliberately light: no OpenCV / MediaPipe imports, so the control panel opens instantly.

Where data lives:
  - run as scripts:   next to the scripts (your POSTURE AI folder), as before
  - packaged .exe:    %APPDATA%\\PostureAI   (the install folder may be read-only)
  - override:         set the POSTURE_AI_HOME environment variable (used for testing)
"""
import json
import os
import subprocess
import sys
import time

try:
    import winreg  # Windows registry, used for "Start with Windows"
except Exception:
    winreg = None

FROZEN = bool(getattr(sys, "frozen", False))  # True inside the packaged .exe
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if os.environ.get("POSTURE_AI_HOME"):
    APP_DIR = os.path.abspath(os.environ["POSTURE_AI_HOME"])
elif FROZEN:
    APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PostureAI")
else:
    APP_DIR = SCRIPT_DIR
try:
    os.makedirs(APP_DIR, exist_ok=True)
except OSError:
    pass

SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOG_PATH = os.path.join(APP_DIR, "posture_log.csv")
APP_LOG_PATH = os.path.join(APP_DIR, "posture_monitor.log")
STATUS_PATH = os.path.join(APP_DIR, "status.json")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PostureAI"
AUTOSTART_SUPPORTED = winreg is not None


def resource_path(name):
    """Path of a bundled resource (like app.ico), both in scripts and in the .exe."""
    base = getattr(sys, "_MEIPASS", SCRIPT_DIR)
    return os.path.join(base, name)


# ------------------------------------------------------------------ launching parts
def python_gui_exe():
    """pythonw.exe (no console window) next to the current Python, if it exists."""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else sys.executable


def launch_command(*flags):
    """Command line that starts this app: the .exe itself, or posture_app.py in script mode.
    Flags: (none) control panel | --background hidden monitor | --monitor monitor with window |
           --report charts"""
    if FROZEN:
        return [sys.executable, *flags]
    return [python_gui_exe(), os.path.join(SCRIPT_DIR, "posture_app.py"), *flags]


def launch_detached(*flags):
    """Start part of the app as an independent process (it keeps running if we close)."""
    kwargs = {"cwd": APP_DIR, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    return subprocess.Popen(launch_command(*flags), **kwargs)


# --------------------------------------------------------------------- settings file
def read_settings_raw():
    """The settings.json contents as a dict ({} if missing or unreadable)."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def update_settings(changes):
    """Change only the given keys in settings.json. The monitor notices and reloads."""
    data = read_settings_raw()
    data.update(changes)
    try:
        _write_json_atomic(SETTINGS_PATH, data)
        return True
    except OSError as e:
        print(f"WARNING: could not save settings ({e}).")
        return False


# ------------------------------------------------------------------ start with Windows
def autostart_command():
    return subprocess.list2cmdline(launch_command("--background"))


def autostart_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enable):
    """Add or remove the per-user startup entry (no administrator rights needed)."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        print(f"WARNING: could not change the startup setting ({e}).")
        return False


# ------------------------------------------- control panel -> monitor: command flag files
COMMAND_NAMES = ("calibrate", "quit", "toggle_window")


def _flag_path(name):
    return os.path.join(APP_DIR, f"command_{name}.flag")


def send_command(name):
    """Ask the running monitor to do something (calibrate / quit / toggle_window)."""
    try:
        open(_flag_path(name), "w").close()
        return True
    except OSError:
        return False


def pop_command(name):
    """True (once) if a command was sent; removes the flag."""
    path = _flag_path(name)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


def clear_commands():
    for name in COMMAND_NAMES:
        pop_command(name)


# ------------------------------------------------ monitor -> control panel: status file
def write_status(data):
    try:
        _write_json_atomic(STATUS_PATH, data)
    except OSError:
        pass


def clear_status():
    try:
        os.remove(STATUS_PATH)
    except OSError:
        pass


def read_status(max_age=5.0):
    """The monitor's latest status dict, or None if it isn't running (no fresh status)."""
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and time.time() - float(data.get("time", 0)) <= max_age:
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None
