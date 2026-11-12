"""Posture report: a weekly text report plus charts, built from posture_log.csv.

Usage:  python posture_report.py            (reads posture_log.csv next to this script)
        python posture_report.py path\\to\\other_log.csv
Prints the weekly report, saves it as weekly_report.txt, saves posture_report.png
next to the log, and opens the chart window.
Needs matplotlib, which is already installed in your venv.
"""
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import posture_common as common

# matplotlib is imported inside main(), so the control panel can use weekly_summary() quickly.

GAP_SECONDS = 30       # a gap longer than this between rows is drawn as a break in the line
MIN_DAY_SECONDS = 300  # a day needs at least 5 tracked minutes to count as best/worst day
CHART_DAYS = 14        # how many recent days the daily bar chart shows


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((datetime.fromisoformat(r["timestamp"]), r["posture"], float(r["health"])))
            except (KeyError, ValueError):
                continue  # skip malformed lines
    rows.sort(key=lambda x: x[0])
    return rows


def say(message):
    """Print a message; in the packaged .exe (no console) also show it in a small popup."""
    print(message)
    if common.FROZEN:
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showinfo("Posture report", message)
            root.destroy()
        except Exception:
            pass


def daily_counts(rows):
    """Seconds of good/bad posture per calendar day: {date: [good, bad]}.
    Each log row is one logged second (the monitor's log_interval is 1 s by default)."""
    daily = defaultdict(lambda: [0, 0])
    for ts, posture, _ in rows:
        daily[ts.date()][0 if posture == "good" else 1] += 1
    return daily


def fmt_minutes(seconds):
    return f"{seconds / 60:.0f} min"


def fmt_hm(seconds):
    minutes = int(seconds // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def window_stats(daily, start, end):
    """Add up the per-day [good, bad] second counts for dates start..end (inclusive)."""
    good = bad = 0
    per_day = {}
    for d, (g, b) in daily.items():
        if start <= d <= end:
            good += g
            bad += b
            per_day[d] = (g, b)
    return good, bad, per_day


def weekly_summary(daily, today):
    """Last 7 days (ending today) compared with the 7 days before, as a list of text lines."""
    start = today - timedelta(days=6)
    prev_start, prev_end = today - timedelta(days=13), today - timedelta(days=7)

    title = f"Weekly report: {start.strftime('%d %b')} to {today.strftime('%d %b %Y')}"
    lines = [title, "-" * len(title)]

    good, bad, per_day = window_stats(daily, start, today)
    total = good + bad
    if total == 0:
        lines.append("No sessions recorded in the last 7 days.")
        return lines

    pct = 100 * good / total
    lines.append(f"Tracked:       {fmt_hm(total)} over {len(per_day)} day(s)")
    lines.append(f"Good posture:  {pct:.0f}%")

    pgood, pbad, _ = window_stats(daily, prev_start, prev_end)
    ptotal = pgood + pbad
    if ptotal == 0:
        lines.append("Last week:     no data yet to compare with.")
    else:
        ppct = 100 * pgood / ptotal
        diff = pct - ppct
        if abs(diff) < 1:
            change = "about the same as last week"
        elif diff > 0:
            change = f"up {diff:.0f} points from last week"
        else:
            change = f"down {abs(diff):.0f} points from last week"
        lines.append(f"Last week:     {change} ({ppct:.0f}% then)")

    solid = {d: 100 * g / (g + b) for d, (g, b) in per_day.items() if g + b >= MIN_DAY_SECONDS}
    if len(solid) >= 2:
        best, worst = max(solid, key=solid.get), min(solid, key=solid.get)
        lines.append(f"Best day:      {best.strftime('%a %d %b')} ({solid[best]:.0f}%)")
        lines.append(f"Worst day:     {worst.strftime('%a %d %b')} ({solid[worst]:.0f}%)")
    return lines


def main():
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    args = [a for a in sys.argv[1:] if not a.startswith("--")]  # ignore flags like --report
    path = args[0] if args else common.LOG_PATH
    if not os.path.exists(path):
        say(f"No log found at {path}.\nStart the monitor, calibrate, work for a while, then quit.")
        return

    rows = load(path)
    if not rows:
        say("The log has no usable rows yet. Calibrate and work for a minute, then try again.")
        return

    daily = daily_counts(rows)

    # ---- Weekly text report ----
    summary = weekly_summary(daily, datetime.now().date())
    print("\n" + "\n".join(summary) + "\n")
    try:
        txt_path = os.path.join(os.path.dirname(os.path.abspath(path)), "weekly_report.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")
        print(f"Weekly report saved to {txt_path}\n")
    except OSError as e:
        print(f"(Could not save weekly_report.txt: {e})\n")

    days = sorted(daily)[-CHART_DAYS:]
    pct_good = [100 * daily[d][0] / sum(daily[d]) for d in days]
    tracked = [sum(daily[d]) for d in days]

    print("Day          Tracked   Good posture")
    for d, t, p in zip(days, tracked, pct_good):
        print(f"{d}   {fmt_minutes(t):>7}   {p:5.0f}%")

    # ---- Figure ----
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    labels = [d.strftime("%a %d %b") for d in days]
    bars = ax1.bar(labels, pct_good, color="#2e9e5b")
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("Good posture (%)")
    ax1.set_title("Good posture per day")
    ax1.axhline(100, color="#cccccc", linewidth=0.8)
    for bar, t in zip(bars, tracked):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                 fmt_minutes(t), ha="center", fontsize=9, color="#555555")

    last_day = days[-1]
    times, health = [], []
    prev = None
    for ts, _, hp in rows:
        if ts.date() != last_day:
            continue
        if prev is not None and (ts - prev).total_seconds() > GAP_SECONDS:
            times.append(ts)          # break the line across pauses
            health.append(float("nan"))
        times.append(ts)
        health.append(hp)
        prev = ts

    ax2.plot(times, health, color="#2c6fbb", linewidth=1.5)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Health")
    ax2.set_title(f"Health over time: {last_day.strftime('%a %d %b')}")
    ax2.axhline(40, color="#d9534f", linestyle="--", linewidth=1)
    ax2.text(times[0], 43, "alert level (40)", color="#d9534f", fontsize=8)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    fig.tight_layout()
    out = os.path.join(os.path.dirname(os.path.abspath(path)), "posture_report.png")
    fig.savefig(out, dpi=150)
    print(f"\nChart saved to {out}")
    plt.show()


if __name__ == "__main__":
    main()
