"""Posture Monitor: MediaPipe Pose + OpenCV, with a health-bar game loop.

Run:   python posture_monitor.py               window + system-tray icon
       python posture_monitor.py --background  hidden, tray icon only (used by Start with Windows)

Window keys:  c = calibrate (sit with good posture first)   m = mute everything
              v = voice on/off   q = quit
Tray icon:    right-click for: show/hide window, control panel, calibrate, sound, voice,
              start with Windows, weekly report, settings, quit.
Control panel: posture_gui.py (opened by posture_app.py / PostureAI.exe) edits settings.json
              and sends commands to this monitor; changes apply within a second or two.
              Closing the window just hides it; use Quit to stop the monitor.

Settings live in settings.json next to this script (created on first run) and are
remembered between restarts, including your calibration.
Alerts: a quick beep when posture goes bad; a spoken reminder only if it stays bad
for voice_after_seconds in a row.

Needs:   mediapipe==0.10.21, opencv-python, and optionally
         pyttsx3 (voice) and pystray (tray icon):  pip install pyttsx3 pystray
"""
import csv
import json
import math
import os
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

import posture_common as common
from posture_common import (AUTOSTART_SUPPORTED, autostart_enabled, launch_detached,
                            set_autostart)

APP_DIR = common.APP_DIR
SETTINGS_PATH = common.SETTINGS_PATH
LOG_PATH = common.LOG_PATH
APP_LOG_PATH = common.APP_LOG_PATH
BACKGROUND = "--background" in sys.argv

# Under pythonw (no console) there is no stdout/stderr: send messages to a log file instead.
if sys.stdout is None or sys.stderr is None:
    try:
        if os.path.exists(APP_LOG_PATH) and os.path.getsize(APP_LOG_PATH) > 1_000_000:
            os.remove(APP_LOG_PATH)  # keep the log small
        _app_log = open(APP_LOG_PATH, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _app_log
        if sys.stderr is None:
            sys.stderr = _app_log
    except OSError:
        pass


def fail(message):
    """Print a friendly error and stop. In background mode, also pop up a message box."""
    print(f"\nERROR: {message}")
    if (BACKGROUND or common.FROZEN) and os.name == "nt":  # no console to read it in
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Posture Monitor", 0x10)
        except Exception:
            pass
    sys.exit(1)


try:
    import cv2
except ImportError:
    fail("OpenCV is not installed in this Python environment.\n"
         "  Fix: pip install opencv-python==4.10.0.84\n"
         "  Also check that you are using the .venv312 environment (use run_posture.bat).")

try:
    import mediapipe as mp
    mp_pose = mp.solutions.pose
    mp_draw = mp.solutions.drawing_utils
except ImportError:
    fail("MediaPipe is not installed in this Python environment.\n"
         "  Fix: pip install mediapipe==0.10.21\n"
         "  Also check that you are using the .venv312 environment (use run_posture.bat).")
except AttributeError:
    fail("This MediaPipe version is too new: it no longer has 'solutions'.\n"
         "  Fix: pip install mediapipe==0.10.21   (needs Python 3.12 or older)")

# Optional extras: the app still works without them.
try:
    import winsound  # built into Windows
except Exception:
    winsound = None

try:
    import pyttsx3  # offline text-to-speech (pip install pyttsx3)
except Exception:
    pyttsx3 = None

# ---- Settings (stored in settings.json; edit that file, not this one) --------
DEFAULTS = {
    "max_tilt_degrees": 6.0,     # shoulder tilt allowed before it counts as bad
    "max_drop_ratio": 0.18,      # how much the nose-to-shoulder gap may shrink vs calibration
    "smoothing_window": 5,       # frames averaged to reduce jitter
    "health_gain": 0.5,          # health gained per 1/30 s of good posture
    "health_loss": 1.0,          # health lost per 1/30 s of bad posture
    "camera_index": 0,
    "alert_below": 40,           # beep while health is under this and posture is bad
    "alert_cooldown": 3.0,       # seconds between beeps
    "voice_after_seconds": 5.0,  # speak only after posture has been bad this long in a row
    "voice_cooldown": 8.0,       # seconds between spoken reminders
    "voice_rate": 170,           # speaking speed (words per minute)
    "voice_volume": 1.0,         # 0.0 to 1.0 (beeps use the system volume)
    "log_interval": 1.0,         # seconds between CSV log rows
    "sound_on": True,
    "voice_on": True,
    "baseline_ratio": None,      # your saved calibration (set by pressing c / Calibrate now)
}
LIMITS = {
    "max_tilt_degrees": (1, 45), "max_drop_ratio": (0.02, 0.9), "smoothing_window": (1, 30),
    "health_gain": (0, 10), "health_loss": (0.1, 10), "camera_index": (0, 10),
    "alert_below": (0, 100), "alert_cooldown": (0.5, 60), "voice_after_seconds": (0, 120),
    "voice_cooldown": (2, 120), "voice_rate": (80, 300), "voice_volume": (0, 1),
    "log_interval": (0.2, 60),
}
BOOL_KEYS = {"sound_on", "voice_on"}
cfg = dict(DEFAULTS)  # the live settings

REFERENCE_FPS = 30           # health rates are defined per 1/30 s so they don't depend on frame rate
WINDOW = "Posture Monitor"
BACKGROUND_SLEEP = 0.03      # pause per frame while the window is hidden, to save CPU
L = mp_pose.PoseLandmark


# ---------------------------------------------------------------- settings file
_settings_stamp = None  # settings.json modification time as we last saw it


def _file_stamp():
    try:
        return os.stat(SETTINGS_PATH).st_mtime_ns
    except OSError:
        return None


def _note_settings_stamp():
    global _settings_stamp
    _settings_stamp = _file_stamp()


def settings_changed_externally():
    """True if settings.json was changed by someone else (the control panel, or by hand)."""
    return _file_stamp() != _settings_stamp


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def save_settings(settings):
    data = {"_readme": "Changes are picked up automatically within a second or two."}
    data.update({k: settings.get(k) for k in DEFAULTS})
    tmp = SETTINGS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, SETTINGS_PATH)
        _note_settings_stamp()
    except OSError as e:
        print(f"WARNING: could not save settings ({e}).")


def load_settings():
    """Read settings.json, falling back to defaults for anything missing or invalid."""
    settings = dict(DEFAULTS)
    if not os.path.exists(SETTINGS_PATH):
        save_settings(settings)
        return settings
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the file must contain a JSON object")
    except (OSError, ValueError) as e:
        print(f"WARNING: could not read settings.json ({e}). Using defaults; the old file was "
              "kept as settings.json.bad")
        try:
            os.replace(SETTINGS_PATH, SETTINGS_PATH + ".bad")
        except OSError:
            pass
        save_settings(settings)
        return settings

    _note_settings_stamp()
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if key in BOOL_KEYS:
            if isinstance(value, bool):
                settings[key] = value
            else:
                print(f"WARNING: settings.json: '{key}' must be true or false; using {default}.")
        elif key == "baseline_ratio":
            if value is None:
                settings[key] = None
            elif _is_number(value) and value > 0:
                settings[key] = float(value)
            else:
                print("WARNING: settings.json: 'baseline_ratio' must be a positive number or null.")
        else:
            if not _is_number(value):
                print(f"WARNING: settings.json: '{key}' must be a number; using {default}.")
                continue
            lo, hi = LIMITS[key]
            clamped = min(max(value, lo), hi)
            if clamped != value:
                print(f"WARNING: settings.json: '{key}' should be between {lo} and {hi}; using {clamped}.")
            settings[key] = int(round(clamped)) if isinstance(default, int) else float(clamped)
    return settings


# ---------------------------------------------------------------------- one copy only
_mutex_handle = None


def already_running():
    """True if another copy is running (two copies would fight over the camera)."""
    global _mutex_handle
    if os.name != "nt":
        return False
    try:
        import ctypes
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\PostureAIMonitor")
        return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


# ------------------------------------------------------------------------ camera / log
def open_camera(preferred):
    """Try the preferred camera, then the others, with two Windows backends each."""
    indexes = [preferred] + [i for i in range(3) if i != preferred]
    for idx in indexes:
        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None:
                    if idx != preferred:
                        print(f"Camera {preferred} not available; using camera {idx} instead.")
                    return cap
            cap.release()
    return None


def open_log(path):
    """Open the CSV log. If it can't be opened (e.g. it is open in Excel), carry on without it."""
    try:
        is_new = not os.path.exists(path)
        f = open(path, "a", newline="", encoding="utf-8")
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "tilt_deg", "head_ratio", "posture", "health"])
            f.flush()
        return f, writer
    except OSError as e:
        print(f"WARNING: could not open {path} ({e}).\n"
              "         If the file is open in Excel, close it. Logging is OFF for this session.")
        return None, None


# ---------------------------------------------------------------------------- alerts
def play_alert():
    """Two-tone beep in a background thread so the video doesn't freeze."""
    if winsound is None:
        print("\a", end="", flush=True)  # fallback terminal bell
        return

    def _beep():
        try:
            winsound.Beep(900, 200)
            winsound.Beep(600, 300)
        except Exception:
            pass

    threading.Thread(target=_beep, daemon=True).start()


_speak_lock = threading.Lock()


def speak(text):
    """Speak text out loud in a background thread (skipped if already speaking)."""
    if pyttsx3 is None:
        return

    def _run():
        if not _speak_lock.acquire(blocking=False):
            return
        try:
            engine = pyttsx3.init()  # fresh engine per call avoids threading issues
            engine.setProperty("rate", cfg["voice_rate"])
            engine.setProperty("volume", cfg["voice_volume"])
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            print(f"Voice error: {e}")
            play_alert()  # the voice engine failed: at least make a sound
        finally:
            _speak_lock.release()

    threading.Thread(target=_run, daemon=True).start()


def alert_action(sound_on, voice_on, bad_streak, health, now, last_alert, last_voice):
    """Decide what to play right now: 'voice', 'beep' or None.
    Voice is only for persistent bad posture; a quick beep covers the first moments."""
    if not sound_on:
        return None
    if voice_on and bad_streak >= cfg["voice_after_seconds"]:
        return "voice" if now - last_voice > cfg["voice_cooldown"] else None
    if health < cfg["alert_below"] and now - last_alert > cfg["alert_cooldown"]:
        return "beep"
    return None


def voice_message(reasons):
    if len(reasons) > 1:
        return "Bad posture. Please level your shoulders and sit up straight."
    if "shoulders tilted" in reasons:
        return "Your shoulders are uneven. Please level them."
    return "Your head is leaning forward. Please sit up straight."


# ------------------------------------------------------------------------- tray icon
COLORS = {"good": (46, 158, 91, 255), "bad": (217, 83, 79, 255), "idle": (240, 173, 78, 255)}


def make_icon_image(category):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=COLORS[category],
                                outline=(255, 255, 255, 255), width=4)
    return img


class State:
    """Shared between the monitor loop and the tray menu thread."""

    def __init__(self):
        self.sound_on = True
        self.voice_on = False
        self.show_window = True
        self.tray_active = False
        self.calibrate_requested = False
        self.reload_requested = False
        self.quit_requested = False


def persist(state):
    cfg["sound_on"] = state.sound_on
    if pyttsx3 is not None:
        cfg["voice_on"] = state.voice_on
    save_settings(cfg)


def start_tray(state):
    """Create the system-tray icon (returns None if pystray isn't available)."""
    try:
        import pystray
        from pystray import MenuItem as Item
        make_icon_image("idle")  # makes sure Pillow works too
    except Exception:
        return None

    def toggle_window(icon, item):
        state.show_window = not state.show_window

    def calibrate(icon, item):
        state.calibrate_requested = True

    def toggle_sound(icon, item):
        state.sound_on = not state.sound_on
        persist(state)

    def toggle_voice(icon, item):
        state.voice_on = not state.voice_on
        persist(state)

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def open_report(icon, item):
        try:
            launch_detached("--report")
        except Exception as e:
            print(f"Could not open the report: {e}")

    def open_panel(icon, item):
        try:
            launch_detached()  # no flags = the control panel
        except Exception as e:
            print(f"Could not open the control panel: {e}")

    def open_settings(icon, item):
        try:
            os.startfile(SETTINGS_PATH)  # Windows: opens in the default editor
        except Exception as e:
            print(f"Could not open settings.json: {e}")

    def reload_settings(icon, item):
        state.reload_requested = True

    def quit_app(icon, item):
        state.quit_requested = True

    menu = pystray.Menu(
        Item("Show window", toggle_window, default=True, checked=lambda i: state.show_window),
        Item("Open control panel", open_panel),
        Item("Calibrate now", calibrate),
        pystray.Menu.SEPARATOR,
        Item("Sound alerts", toggle_sound, checked=lambda i: state.sound_on),
        Item("Voice reminders", toggle_voice, checked=lambda i: state.voice_on,
             enabled=pyttsx3 is not None),
        Item("Start with Windows", toggle_autostart, checked=lambda i: autostart_enabled(),
             enabled=AUTOSTART_SUPPORTED),
        pystray.Menu.SEPARATOR,
        Item("Open weekly report", open_report),
        Item("Open settings file", open_settings),
        Item("Reload settings", reload_settings),
        pystray.Menu.SEPARATOR,
        Item("Quit", quit_app),
    )
    try:
        icon = pystray.Icon("PostureAI", make_icon_image("idle"), "Posture Monitor", menu)
        icon.run_detached()
        return icon
    except Exception as e:
        print(f"Tray icon could not start: {e}")
        return None


class TrayUpdater:
    """Updates the tray icon colour/tooltip only when something changed."""

    def __init__(self, icon):
        self.icon = icon
        self.category = None
        self.title = None
        self.last_t = 0.0

    def update(self, category, title):
        if self.icon is None:
            return
        now = time.time()
        if category != self.category or (title != self.title and now - self.last_t >= 2.0):
            try:
                if category != self.category:
                    self.icon.icon = make_icon_image(category)
                self.icon.title = title[:120]
            except Exception:
                pass
            self.category, self.title, self.last_t = category, title, now

    def notify(self, message):
        if self.icon is None:
            return
        try:
            self.icon.notify(message, "Posture Monitor")
        except Exception:
            pass


# ------------------------------------------------------------------------------ maths
def fmt_duration(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def shoulder_tilt_deg(left, right, w, h):
    """Angle of the shoulder line vs horizontal, in degrees (0 = level).
    Landmarks are normalized, so scale by frame size before using atan2."""
    dx = (right.x - left.x) * w
    dy = (right.y - left.y) * h
    angle = math.degrees(math.atan2(dy, dx))
    # Because of mirroring, the line may read near 0 or near +/-180; fold it to -90..90.
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    return angle


def head_ratio(nose, left, right, w, h):
    """Vertical gap between nose and shoulder midpoint, divided by shoulder width.
    Leaning forward / dropping the head shrinks this number."""
    mid_y = (left.y + right.y) / 2 * h
    gap = mid_y - nose.y * h
    width = math.hypot((right.x - left.x) * w, (right.y - left.y) * h)
    return gap / width if width > 0 else 0.0


# ------------------------------------------------------------------------ main loop
def run_monitor(state, icon):
    updater = TrayUpdater(icon)
    updater.update("idle", "Posture Monitor - starting...")
    common.clear_commands()  # forget any old button presses from a previous run
    common.write_status({"time": time.time(), "pid": os.getpid(), "posture": "idle",
                         "note": "starting", "health": 100, "calibrated": False})

    cap = open_camera(cfg["camera_index"])
    if cap is None and BACKGROUND:
        # Right after Windows starts, the camera may not be ready yet: keep trying for a minute.
        updater.update("idle", "Posture Monitor - waiting for the camera...")
        for _ in range(12):
            time.sleep(5)
            if state.quit_requested:
                return
            cap = open_camera(cfg["camera_index"])
            if cap is not None:
                break
    if cap is None:
        fail("Could not open any camera.\n"
             "  - Close other apps that use it (Teams, Zoom, the Camera app).\n"
             "  - Check Windows Settings > Privacy > Camera is allowed for desktop apps.\n"
             "  - If you have several cameras, set camera_index to 1 or 2 in settings.json.")

    tilt_hist = deque(maxlen=cfg["smoothing_window"])
    ratio_hist = deque(maxlen=cfg["smoothing_window"])
    baseline_ratio = cfg["baseline_ratio"]
    recalibrated = False
    health = 100.0
    if pyttsx3 is None:
        print("Voice alerts unavailable (run: pip install pyttsx3). Using beeps instead.")
        state.voice_on = False
    last_alert = 0.0
    last_voice = 0.0
    bad_streak = 0.0  # seconds of continuous bad posture

    # ---- session log + stats ----
    log_file, log = open_log(LOG_PATH)
    last_frame_t = time.time()
    last_log = 0.0
    good_secs = bad_secs = 0.0
    bad_run = longest_bad = 0.0
    lowest_health = 100.0

    # ---- camera-loss tracking ----
    lost_since = None
    reconnect_attempts = 0
    window_open = False

    last_status = 0.0
    last_poll = 0.0

    def publish_status(category, note=""):
        """Tell the control panel what we're doing (at most once a second)."""
        nonlocal last_status
        now = time.time()
        if now - last_status < 1.0:
            return
        last_status = now
        common.write_status({"time": now, "pid": os.getpid(), "posture": category, "note": note,
                             "health": int(health), "calibrated": baseline_ratio is not None,
                             "window": state.show_window})

    pose = mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5)
    try:
        while not state.quit_requested:
            # ---- twice a second: commands from the control panel, settings changes ----
            now_poll = time.time()
            if now_poll - last_poll >= 0.4:
                last_poll = now_poll
                if common.pop_command("quit"):
                    break
                if common.pop_command("calibrate"):
                    state.calibrate_requested = True
                if common.pop_command("toggle_window"):
                    state.show_window = not state.show_window
                manual_reload = state.reload_requested
                if manual_reload or settings_changed_externally():
                    state.reload_requested = False
                    cfg.update(load_settings())
                    tilt_hist = deque(tilt_hist, maxlen=cfg["smoothing_window"])
                    ratio_hist = deque(ratio_hist, maxlen=cfg["smoothing_window"])
                    state.sound_on = cfg["sound_on"]
                    state.voice_on = cfg["voice_on"] and pyttsx3 is not None
                    baseline_ratio = cfg["baseline_ratio"]
                    if manual_reload:
                        updater.notify("Settings reloaded.")

            ok, frame = cap.read()
            if not ok or frame is None:
                # Camera feed dropped (unplugged, or taken by another app): try to reconnect.
                if lost_since is None:
                    lost_since = time.time()
                    print("Camera feed lost. Trying to reconnect...")
                    updater.update("idle", "Posture Monitor - camera lost, reconnecting...")
                publish_status("idle", "camera lost, reconnecting")
                if time.time() - lost_since > 2.0:
                    cap.release()
                    new_cap = open_camera(cfg["camera_index"])
                    reconnect_attempts += 1
                    if new_cap is not None:
                        cap = new_cap
                        lost_since = None
                        reconnect_attempts = 0
                        last_frame_t = time.time()  # don't count the outage as posture time
                        print("Camera reconnected.")
                    elif reconnect_attempts >= 5:
                        print("Could not reconnect to the camera. Stopping.")
                        updater.notify("Camera lost. The monitor has stopped.")
                        break
                    else:
                        lost_since = time.time()
                if state.show_window and window_open:
                    if cv2.waitKey(100) & 0xFF == ord("q"):
                        break
                else:
                    time.sleep(0.1)
                continue
            lost_since = None

            visible = state.show_window
            frame = cv2.flip(frame, 1)  # mirror view
            h, w = frame.shape[:2]
            result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            now_f = time.time()
            dt = min(now_f - last_frame_t, 1.0)  # cap so a stall isn't counted as posture time
            last_frame_t = now_f
            step = min(dt, 0.25) * REFERENCE_FPS  # frame-rate independent health changes

            status = "No person detected"
            color = (0, 165, 255)
            tilt = ratio = None
            posture = None

            if result.pose_landmarks:
                lm = result.pose_landmarks.landmark
                ls, rs, nose = lm[L.LEFT_SHOULDER], lm[L.RIGHT_SHOULDER], lm[L.NOSE]

                if min(ls.visibility, rs.visibility, nose.visibility) > 0.5:
                    tilt_hist.append(shoulder_tilt_deg(ls, rs, w, h))
                    ratio_hist.append(head_ratio(nose, ls, rs, w, h))
                    tilt = sum(tilt_hist) / len(tilt_hist)
                    ratio = sum(ratio_hist) / len(ratio_hist)

                    bad_tilt = abs(tilt) > cfg["max_tilt_degrees"]
                    bad_head = False
                    if baseline_ratio:
                        bad_head = ratio < baseline_ratio * (1 - cfg["max_drop_ratio"])

                    if bad_tilt or bad_head:
                        health = max(0.0, health - cfg["health_loss"] * step)
                        reasons = []
                        if bad_tilt:
                            reasons.append("shoulders tilted")
                        if bad_head:
                            reasons.append("head forward/down")
                        status, color = "Bad: " + ", ".join(reasons), (0, 0, 255)
                        posture = "bad"
                        bad_streak += dt
                        action = alert_action(state.sound_on, state.voice_on, bad_streak, health,
                                              now_f, last_alert, last_voice)
                        if action == "voice":
                            speak(voice_message(reasons))
                            last_voice = now_f
                        elif action == "beep":
                            play_alert()
                            last_alert = now_f
                    else:
                        health = min(100.0, health + cfg["health_gain"] * step)
                        status, color = "Good posture", (0, 200, 0)
                        posture = "good"
                        bad_streak = 0.0

                    if visible:
                        # Green guide line at shoulder-midpoint height
                        y_mid = int((ls.y + rs.y) / 2 * h)
                        cv2.line(frame, (0, y_mid), (w, y_mid), (0, 255, 0), 1)

                if visible:
                    mp_draw.draw_landmarks(frame, result.pose_landmarks, mp_pose.POSE_CONNECTIONS)

            if posture is None:
                bad_streak = 0.0  # nobody in view: the streak starts over

            # ---- tray icon colour + tooltip ----
            category = posture if posture else "idle"
            label = {"good": "good posture", "bad": "bad posture"}.get(posture, "no one in view")
            updater.update(category, f"Posture Monitor - {label} (health {int(health)})")
            publish_status(category)

            # ---- stats + CSV log (only after calibration) ----
            if posture and baseline_ratio:
                if posture == "good":
                    good_secs += dt
                    bad_run = 0.0
                else:
                    bad_secs += dt
                    bad_run += dt
                    longest_bad = max(longest_bad, bad_run)
                lowest_health = min(lowest_health, health)
                if log is not None and now_f - last_log >= cfg["log_interval"]:
                    try:
                        log.writerow([datetime.now().isoformat(timespec="seconds"),
                                      f"{tilt:.1f}", f"{ratio:.3f}", posture, int(health)])
                        log_file.flush()
                    except OSError as e:
                        print(f"WARNING: logging stopped ({e}).")
                        log = None
                    last_log = now_f

            # ---- window (HUD + keys), or just a short pause when hidden ----
            key = -1
            if visible:
                cv2.putText(frame, status, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
                if tilt is not None:
                    cv2.putText(frame, f"Tilt: {tilt:+.1f} deg", (15, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    cv2.putText(frame, f"Head ratio: {ratio:.2f}", (15, 95),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(frame, f"Sound: {'ON' if state.sound_on else 'OFF'} (m)", (w - 190, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
                voice_label = "N/A" if pyttsx3 is None else ("ON" if state.voice_on else "OFF")
                cv2.putText(frame, f"Voice: {voice_label} (v)", (w - 190, 55),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
                if log is None:
                    cv2.putText(frame, "Logging OFF", (w - 190, 80),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 1)
                if baseline_ratio is None:
                    cv2.putText(frame, "Sit straight and press 'c' to calibrate", (15, h - 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                elif not recalibrated:
                    cv2.putText(frame, "Using saved calibration (press 'c' to redo)", (15, h - 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

                bar_w = int(200 * health / 100)
                bar_color = (0, 200, 0) if health > 50 else (0, 165, 255) if health > 25 else (0, 0, 255)
                cv2.rectangle(frame, (15, h - 30), (215, h - 12), (60, 60, 60), -1)
                cv2.rectangle(frame, (15, h - 30), (15 + bar_w, h - 12), bar_color, -1)
                cv2.putText(frame, f"Health {int(health)}", (225, h - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                if health <= 0:
                    cv2.putText(frame, "GAME OVER - sit up straight!", (w // 2 - 190, h // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

                cv2.imshow(WINDOW, frame)
                window_open = True
                key = cv2.waitKey(1) & 0xFF
            else:
                if window_open:
                    try:
                        cv2.destroyWindow(WINDOW)
                        cv2.waitKey(1)
                    except cv2.error:
                        pass
                    window_open = False
                time.sleep(BACKGROUND_SLEEP)

            if key == ord("q"):
                break
            if key == ord("m"):
                state.sound_on = not state.sound_on
                persist(state)
            if key == ord("v") and pyttsx3 is not None:
                state.voice_on = not state.voice_on  # spoken reminders on/off (beeps stay on)
                persist(state)

            # ---- calibration: 'c' key or tray menu ----
            if key == ord("c") or state.calibrate_requested:
                state.calibrate_requested = False
                if ratio is not None and ratio_hist:
                    baseline_ratio = sum(ratio_hist) / len(ratio_hist)
                    recalibrated = True
                    health = 100.0
                    cfg["baseline_ratio"] = round(baseline_ratio, 4)
                    persist(state)
                    print(f"Calibrated. Baseline head ratio = {baseline_ratio:.3f}")
                    updater.notify("Calibrated. Your posture baseline is saved.")
                else:
                    print("Calibration skipped: no one in view.")
                    updater.notify("Couldn't calibrate: sit in front of the camera and try again.")

            # Closing the window with the X hides it if the tray icon is running, else quits.
            if visible and window_open:
                try:
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        if state.tray_active:
                            state.show_window = False
                        else:
                            break
                except cv2.error:
                    pass

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception:
        print("\nUnexpected error. The monitor stopped:\n")
        traceback.print_exc()
    finally:
        # Always release the camera and close the log, even after an error.
        common.clear_status()
        try:
            cap.release()
            pose.close()
            cv2.destroyAllWindows()
        except Exception:
            pass
        if log_file is not None:
            try:
                log_file.close()
            except OSError:
                pass

    total = good_secs + bad_secs
    if total > 0:
        print("\n=== Session summary ===")
        print(f"Tracked time:        {fmt_duration(total)}")
        print(f"Good posture:        {fmt_duration(good_secs)} ({100 * good_secs / total:.0f}%)")
        print(f"Bad posture:         {fmt_duration(bad_secs)} ({100 * bad_secs / total:.0f}%)")
        print(f"Longest bad streak:  {fmt_duration(longest_bad)}")
        print(f"Lowest health:       {int(lowest_health)}")
        if log is not None:
            print(f"Log saved to:        {LOG_PATH}")
    else:
        print("\nNo posture data recorded (calibrate to start tracking).")


def main():
    if already_running():
        print("Posture Monitor is already running. Look for its icon in the system tray "
              "(bottom-right, maybe under the ^ arrow).")
        return

    cfg.update(load_settings())
    state = State()
    state.sound_on = cfg["sound_on"]
    state.voice_on = cfg["voice_on"] and pyttsx3 is not None
    state.show_window = not BACKGROUND

    print("Starting Posture Monitor...  (c = calibrate, m = mute, v = voice on/off, q = quit)")
    icon = start_tray(state)
    state.tray_active = icon is not None
    if icon is None:
        if BACKGROUND:
            print("Tray icon unavailable (run: pip install pystray), so the window stays visible.")
            state.show_window = True
        else:
            print("Tray icon unavailable (run: pip install pystray). Running in window mode.")

    try:
        run_monitor(state, icon)
    finally:
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass


if __name__ == "__main__":
    main()
