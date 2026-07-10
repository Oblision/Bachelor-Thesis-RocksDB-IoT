"""fig:throttle-trace — the valley mechanism over one run's lifetime.

Anonymous working set (the OOM driver) sampled every 0.2 s for single
representative runs at the same cell coordinate (512 MB cap, 16k bounded pool),
one per workload:
  - s1034 (rho 2.02): the heaviest load that still fits. Its per-window state
    fills the pool EARLY, backpressure throttles ingest to the spiller's drain
    rate, the working set holds a long plateau, and the run COMPLETES.
  - s736 (rho 1.44) and s458 (rho 0.89): lighter loads that free-run above the
    drain ceiling; the concurrently live state overruns the pool before the
    spiller catches up and the run ABORTS early.

The lighter loads die fast; the heavier one, throttled, runs long and finishes.
That inversion is the valley of fig:valley seen in time.

Trace columns (run-cell.sh sampler): t_s current_mb anon_mb file_mb shmem_mb
rss_anon_mb. We plot anon_mb. Rep outcome is read from the result TSVs so the
completing / aborting reps are labelled from the data, not guessed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import style

CAP = 512
TRACES = data.TRACES
# (cell, thesis label, rho, colour, kind, reason). One representative run per
# workload at the same cell coordinate (512 MB cap, 16k bounded pool); "kind"
# sets the end marker and whether a completing or a failing rep is drawn.
SERIES = [
    ("W_c512_p16k_r075", "s327",  0.64, style.SKY,    "complete", "completes (fits)"),
    ("W_c512_p16k_r100", "s458",  0.89, style.ORANGE, "pool",     "pool abort"),
    ("W_c512_p16k_r150", "s736",  1.44, style.PURPLE, "pool",     "pool abort"),
    ("W_c512_p16k_r200", "s1034", 2.02, style.GREEN,  "complete", "completes (throttled)"),
    ("W_c512_p16k_r300", "s1582", 3.09, "#D62728",   "oom",      "OOM (state $>$ cap)"),
]
OOM_COLOR = "#D62728"

_MARK = {"complete": "o", "pool": "s", "oom": "X"}


def _pick_rep(df, cell: str, want_complete: bool) -> int:
    """Rep index whose outcome matches; median-duration representative."""
    rows = df[df["cell"] == cell].copy()
    rows["rep"] = rows["label"].str.extract(r"_r(\d+)$").astype(int)
    cand = rows[rows["ok"] == want_complete]
    if cand.empty:
        cand = rows
    # representative = the rep closest to the median elapsed time of its group
    med = cand["elapsed_s"].median()
    return int(cand.iloc[(cand["elapsed_s"] - med).abs().argmin()]["rep"])


def _load_trace(cell: str, rep: int):
    f = Path(TRACES) / f"{cell}_r{rep}.trace"
    rows = [ln.split() for ln in f.read_text().splitlines() if ln.strip()]
    t = np.array([float(r[0]) for r in rows])
    anon = np.array([float(r[2]) for r in rows])
    return t - t[0], anon


def main() -> None:
    style.use_style()
    df = data.load_runs()
    df = df[~df["cell"].str.contains("b32")]

    fig, ax = plt.subplots(figsize=(6.6, 4.1))
    ax.axhline(CAP, color=style.GREY, lw=1.3, ls="--")
    ax.text(0.99, CAP + 3, f"{CAP} MB cap", fontsize=8.3, color=style.GREY,
            va="bottom", ha="right", transform=ax.get_yaxis_transform())

    handles = []
    for cell, label, rho, color, kind, reason in SERIES:
        rep = _pick_rep(df, cell, kind == "complete")
        t, anon = _load_trace(cell, rep)
        ax.plot(t, anon, color=color, lw=1.8, zorder=3, alpha=0.95)
        mk = _MARK[kind]
        hollow = kind == "pool"
        ax.scatter([t[-1]], [anon[-1]], s=90, zorder=5, marker=mk,
                   facecolors=("white" if hollow else color),
                   edgecolors=color, linewidths=1.4)
        handles.append(Line2D([], [], color=color, lw=2, marker=mk,
                              mfc=("white" if hollow else color), mec=color,
                              label=f"{label} ($\\rho$ {rho:.2f}) — {reason}"))

    ax.annotate("throttled: ingest paced to the\ndrain rate, the run completes",
                xy=(0.50, 0.09), xycoords="axes fraction",
                fontsize=8.2, color=style.GREEN, ha="left")
    ax.annotate("throttled too, but the resident\nstate exceeds the cap: OOM",
                xy=(0.37, 0.88), xycoords="axes fraction",
                fontsize=8.2, color=OOM_COLOR, ha="left")
    ax.annotate("lighter loads pool-abort early", xy=(0.13, 0.44),
                xycoords="axes fraction", fontsize=8.2, color="#555555",
                ha="left")

    ax.set_xlabel("time since run start (s)")
    ax.set_ylabel("anonymous working set (MB)")
    ax.set_ylim(0, CAP + 40)
    ax.set_xlim(left=0)
    ax.set_title("One run each at cap 512 MB, 16k bounded pool")
    fig.legend(handles=handles, loc="outside lower center", ncol=3,
               fontsize=8.0)
    style.save(fig, "fig_throttle_trace")


if __name__ == "__main__":
    main()
