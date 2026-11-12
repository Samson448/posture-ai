"""Posture AI control panel (Tkinter).

Start/stop the monitor, calibrate, adjust sensitivity and alerts, switch "Start with Windows",
and see this week's summary. The monitor runs as a separate background process (with its tray
icon), so closing this window does NOT stop it: use "Stop monitoring" for that.

Open with:  PostureAI.exe        or        python posture_app.py
"""
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import posture_common as common

try:
    import pyttsx3  # noqa: F401  (only checked: tells us whether voice reminders are possible)
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False

# What the sliders show if settings.json doesn't have a value yet (same as the monitor's defaults).
DEFAULTS = {
    "max_tilt_degrees": 6.0,
    "max_drop_ratio": 0.18,
    "voice_after_seconds": 5.0,
    "voice_volume": 1.0,
    "sound_on": True,
    "voice_on": True,
}
COLORS = {"good": "#2e9e5b", "bad": "#d9534f", "idle": "#f0ad4e", "off": "#9aa0a6"}
START_WAIT_SECONDS = 70   # the monitor may wait up to a minute for the camera after start
STOP_WAIT_SECONDS = 10


class App:
    def __init__(self, root):
        self.root = root
        root.title("Posture AI")
        root.resizable(False, False)
        try:
            root.iconbitmap(common.resource_path("app.ico"))
        except Exception:
            pass
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        self.saved = common.read_settings_raw()
        self.pending = {}
        self.save_job = None
        self.starting_until = 0.0
        self.stopping_until = 0.0
        self.summary_text = None
        self.summary_lock = threading.Lock()

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)

        # ---- status ----
        top = ttk.Frame(outer)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=18, height=18, highlightthickness=0)
        self.dot_item = self.dot.create_oval(2, 2, 16, 16, fill=COLORS["off"], outline="")
        self.dot.pack(side="left")
        self.status_var = tk.StringVar(value="Checking...")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=8)
        self.hint_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.hint_var, foreground="#666666").pack(anchor="w", pady=(2, 8))

        # ---- buttons ----
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(buttons, text="Start monitoring", command=self.start_monitor)
        self.stop_btn = ttk.Button(buttons, text="Stop monitoring", command=self.stop_monitor)
        self.calib_btn = ttk.Button(buttons, text="Calibrate now", command=self.calibrate)
        self.window_btn = ttk.Button(buttons, text="Show / hide camera", command=self.toggle_window)
        for b in (self.start_btn, self.stop_btn, self.calib_btn, self.window_btn):
            b.pack(side="left", padx=(0, 6))

        # ---- sensitivity ----
        sens = ttk.LabelFrame(outer, text="Sensitivity (lower = stricter)", padding=8)
        sens.pack(fill="x", pady=4)
        self._slider(sens, "Shoulder tilt limit", "max_tilt_degrees", 2, 20, 0.5,
                     lambda v: f"{v:.1f}°")
        self._slider(sens, "Head-forward limit", "max_drop_ratio", 5, 40, 1,
                     lambda v: f"{v:.0f}%", shown_scale=100.0)

        # ---- alerts ----
        alerts = ttk.LabelFrame(outer, text="Alerts", padding=8)
        alerts.pack(fill="x", pady=4)
        self.sound_var = tk.BooleanVar(value=bool(self._value("sound_on")))
        ttk.Checkbutton(alerts, text="Sound alerts (beep)", variable=self.sound_var,
                        command=lambda: common.update_settings({"sound_on": self.sound_var.get()})
                        ).pack(anchor="w")
        self.voice_var = tk.BooleanVar(value=bool(self._value("voice_on")) and VOICE_AVAILABLE)
        voice_cb = ttk.Checkbutton(
            alerts, text="Voice reminders" + ("" if VOICE_AVAILABLE else "  (voice not installed)"),
            variable=self.voice_var,
            command=lambda: common.update_settings({"voice_on": self.voice_var.get()}))
        voice_cb.pack(anchor="w")
        if not VOICE_AVAILABLE:
            voice_cb.state(["disabled"])
        self._slider(alerts, "Speak after bad posture for", "voice_after_seconds", 1, 30, 1,
                     lambda v: f"{v:.0f} s")
        self._slider(alerts, "Voice volume", "voice_volume", 0, 100, 5,
                     lambda v: f"{v:.0f}%", shown_scale=100.0)

        # ---- startup ----
        self.auto_var = tk.BooleanVar(value=common.autostart_enabled())
        auto_cb = ttk.Checkbutton(outer, text="Start Posture AI with Windows (runs in the tray)",
                                  variable=self.auto_var, command=self.toggle_autostart)
        auto_cb.pack(anchor="w", pady=(8, 2))
        if not common.AUTOSTART_SUPPORTED:
            auto_cb.state(["disabled"])

        # ---- weekly summary ----
        week = ttk.LabelFrame(outer, text="This week", padding=8)
        week.pack(fill="x", pady=6)
        self.week_var = tk.StringVar(value="Loading...")
        ttk.Label(week, textvariable=self.week_var, font=("Consolas", 9), justify="left").pack(anchor="w")
        wb = ttk.Frame(week)
        wb.pack(anchor="w", pady=(6, 0))
        ttk.Button(wb, text="Refresh", command=self.refresh_summary).pack(side="left", padx=(0, 6))
        ttk.Button(wb, text="Open charts", command=self.open_charts).pack(side="left")

        # ---- footer ----
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="Open settings file", command=self.open_settings).pack(side="left", padx=(0, 6))
        ttk.Button(foot, text="Open data folder", command=self.open_folder).pack(side="left")
        ttk.Label(outer, text="Closing this window does not stop the monitor (see the tray icon).",
                  foreground="#666666").pack(anchor="w", pady=(8, 0))

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_summary()
        self.poll()

    # ------------------------------------------------------------------ helpers
    def _value(self, key):
        value = self.saved.get(key, DEFAULTS[key])
        default = DEFAULTS[key]
        if isinstance(default, bool):
            return value if isinstance(value, bool) else default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return value

    def _slider(self, parent, text, key, lo, hi, step, fmt, shown_scale=1.0):
        """A labelled slider. Shown value = stored value * shown_scale (0.18 -> 18%)."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=text, width=27, anchor="w").pack(side="left")
        var = tk.DoubleVar(value=min(max(self._value(key) * shown_scale, lo), hi))
        label = ttk.Label(row, width=7, anchor="e", text=fmt(var.get()))

        def on_move(_=None):
            v = round(var.get() / step) * step
            label.config(text=fmt(v))
            self.queue_save(key, round(v / shown_scale, 4))

        ttk.Scale(row, from_=lo, to=hi, variable=var, command=on_move, length=170).pack(
            side="left", padx=6)
        label.pack(side="left")

    def queue_save(self, key, value):
        """Write slider changes to settings.json shortly after the slider stops moving."""
        self.pending[key] = value
        if self.save_job is not None:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(600, self.flush_saves)

    def flush_saves(self):
        self.save_job = None
        if self.pending:
            changes, self.pending = self.pending, {}
            common.update_settings(changes)

    # ------------------------------------------------------------------ status
    def poll(self):
        status = common.read_status()
        now = time.time()
        running = status is not None
        if running:
            self.starting_until = 0.0
        stopping = now < self.stopping_until and running
        if not running:
            self.stopping_until = 0.0

        if stopping:
            text, color, hint = "Stopping...", COLORS["idle"], ""
        elif running:
            posture = status.get("posture", "idle")
            health = status.get("health")
            note = status.get("note")
            if note:
                text, color = f"Running: {note}", COLORS["idle"]
            elif posture == "good":
                text, color = f"Running: good posture (health {health})", COLORS["good"]
            elif posture == "bad":
                text, color = f"Running: bad posture (health {health})", COLORS["bad"]
            else:
                text, color = "Running: no one in view", COLORS["idle"]
            hint = "" if status.get("calibrated") else \
                "Not calibrated yet: sit upright and press Calibrate now."
        elif now < self.starting_until:
            text, color, hint = "Starting...", COLORS["idle"], "Waiting for the camera."
        else:
            text, color, hint = "Not running", COLORS["off"], \
                "Press Start monitoring. It runs in the system tray."

        self.status_var.set(text)
        self.hint_var.set(hint)
        self.dot.itemconfig(self.dot_item, fill=color)
        can_start = not running and now >= self.starting_until
        self.start_btn.state(["!disabled"] if can_start else ["disabled"])
        self.stop_btn.state(["!disabled"] if running and not stopping else ["disabled"])
        for b in (self.calib_btn, self.window_btn):
            b.state(["!disabled"] if running and not stopping else ["disabled"])

        with self.summary_lock:
            text_ready, self.summary_text = self.summary_text, None
        if text_ready is not None:
            self.week_var.set(text_ready)
        self.root.after(1000, self.poll)

    # ------------------------------------------------------------------ actions
    def start_monitor(self):
        self.flush_saves()
        try:
            common.launch_detached("--background")
        except Exception as e:
            messagebox.showerror("Posture AI", f"Could not start the monitor:\n{e}")
            return
        self.starting_until = time.time() + START_WAIT_SECONDS

    def stop_monitor(self):
        if common.send_command("quit"):
            self.stopping_until = time.time() + STOP_WAIT_SECONDS

    def calibrate(self):
        # Ask first, so you have time to sit upright; the command is only sent after you click OK.
        if messagebox.askokcancel("Posture AI", "Sit upright and look at the screen the way you "
                                  "normally would, then click OK.\n\nYour posture is measured as "
                                  "soon as you click, and a tray notification confirms it."):
            common.send_command("calibrate")

    def toggle_window(self):
        common.send_command("toggle_window")

    def toggle_autostart(self):
        if not common.set_autostart(self.auto_var.get()):
            self.auto_var.set(common.autostart_enabled())
            messagebox.showwarning("Posture AI", "Windows did not allow changing the startup setting.")

    def open_charts(self):
        try:
            common.launch_detached("--report")
        except Exception as e:
            messagebox.showerror("Posture AI", f"Could not open the charts:\n{e}")

    def open_settings(self):
        if not os.path.exists(common.SETTINGS_PATH):
            messagebox.showinfo("Posture AI", "The settings file is created the first time the "
                                "monitor starts.")
            return
        self._open(common.SETTINGS_PATH)

    def open_folder(self):
        self._open(common.APP_DIR)

    @staticmethod
    def _open(path):
        try:
            os.startfile(path)  # Windows only
        except Exception as e:
            messagebox.showinfo("Posture AI", f"Open this yourself:\n{path}\n\n({e})")

    # ------------------------------------------------------------------ weekly summary
    def refresh_summary(self):
        self.week_var.set("Loading...")
        threading.Thread(target=self._compute_summary, daemon=True).start()

    def _compute_summary(self):
        try:
            import posture_report as report
            if not os.path.exists(common.LOG_PATH):
                text = "No sessions yet. Start monitoring and calibrate to begin."
            else:
                rows = report.load(common.LOG_PATH)
                if not rows:
                    text = "No sessions yet. Start monitoring and calibrate to begin."
                else:
                    text = "\n".join(report.weekly_summary(report.daily_counts(rows),
                                                           datetime.now().date()))
        except Exception as e:
            text = f"Could not read the log: {e}"
        with self.summary_lock:
            self.summary_text = text

    def close(self):
        self.flush_saves()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
