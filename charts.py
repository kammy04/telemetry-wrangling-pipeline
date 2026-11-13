"""charts.py - static PNG charts for the README / quick review."""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
RED = "#e34948"
BLUE_RAMP = LinearSegmentedColormap.from_list(
    "blue", ["#f4f8fe", "#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#104281", "#0d366b"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def uptime_by_system(by_system, path, sla=95.0):
    s = by_system.groupby("system_id")["uptime_pct"].mean().round(1).sort_values()
    fig, ax = plt.subplots(figsize=(8, 0.32 * len(s) + 1.2))
    colors = [RED if v < sla else BLUE for v in s.values]
    ax.barh(s.index, s.values, color=colors, height=0.6)
    ax.axvline(sla, color=INK_2, linewidth=1, linestyle="--")
    ax.text(sla, -0.9, f" {sla:.0f}% target", color=INK_2, va="center", fontsize=9)
    for y, v in enumerate(s.values):
        ax.text(v + 0.3, y, f"{v:.1f}%", va="center", ha="left", color=INK, fontsize=8)
    lo = max(0, s.min() - 10)
    ax.set_xlim(lo, 101)
    ax.set_ylim(-1.3, len(s) - 0.5)
    ax.set_xlabel("Average daily uptime (%)")
    ax.set_title("Uptime by system: below-target systems in red")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def error_heatmap(heatmap, path):
    fig, ax = plt.subplots(figsize=(1.6 * heatmap.shape[1] + 3, 0.32 * heatmap.shape[0] + 1.4))
    im = ax.imshow(heatmap.values, cmap=BLUE_RAMP, aspect="auto")
    ax.set_xticks(range(heatmap.shape[1]), [str(c) for c in heatmap.columns])
    ax.set_yticks(range(heatmap.shape[0]), heatmap.index)
    vmax = heatmap.values.max() or 1
    for i in range(heatmap.shape[0]):
        for j in range(heatmap.shape[1]):
            v = heatmap.values[i, j]
            ax.text(j, i, int(v), ha="center", va="center", fontsize=8,
                    color="white" if v > vmax * 0.55 else INK)
    ax.grid(False)
    ax.set_title("Error count per system per day")
    fig.colorbar(im, ax=ax, shrink=0.8, label="errors")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fleet_cpu_and_down(clean, path):
    hourly = clean.groupby("timestamp").agg(
        avg_cpu=("cpu_usage_pct", "mean"),
        down=("status", lambda s: (s == "DOWN").sum()),
    )
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(hourly.index, hourly["avg_cpu"], color=BLUE, linewidth=2)
    ax1.set_ylabel("Fleet avg CPU (%)")
    ax1.set_title("Fleet CPU load and systems down, hour by hour")
    ax2.bar(hourly.index, hourly["down"], width=0.03, color=RED)
    ax2.set_ylabel("Systems down")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def make_all(clean, by_system, heatmap, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    uptime_by_system(by_system, os.path.join(out_dir, "uptime_by_system.png"))
    error_heatmap(heatmap, os.path.join(out_dir, "error_heatmap.png"))
    fleet_cpu_and_down(clean, os.path.join(out_dir, "fleet_cpu_hourly.png"))
