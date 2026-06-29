"""fig:race — completion as a race between ingest rate and spill-drain rate.

One point per spilling cell (L_ heap + W_ bounded grids, pooled together: the
race holds for both architectures). x = the cell's ingest rate: realized median
tps of completing reps, or, for all-fail cells, the workload's free-running rate
(median tps of its completing in-memory / uncapped reference runs — a dead run
has no realized rate of its own). y = state pressure rho.

The plane reads as three regions:
  - rho < 1: state fits in memory, spilling is rare, sprinting is safe.
  - rho > 1, ingest below the SD drain ceiling (~2.6 M/s): survivors, throttled
    by backpressure to the rate the spiller can drain.
  - rho > 1, ingest above the ceiling: the cell out-sprints the drain and dies.
The third region is the race: it is a near-empty-of-survivors death zone.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

import data
import style

CEILING = 2.7   # right edge of the SD drain band; death zone starts here
BAND = (2.5, 2.7)


def _free_running(df) -> dict[int, float]:
    """Free-running ingest rate per workload from completing reference runs."""
    ref = df[df["ok"] & df["workload"].notna()
             & df["arm"].isin(["inmem", "uncapped"]) & (df["tps"] > 0)]
    return {int(w): float(v) for w, v in ref.groupby("workload")["tps"].median().items()}


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    free = _free_running(df)

    fig, ax = plt.subplots(figsize=(7.8, 4.9))
    grid = df[df["campaign"].isin(["L", "W"]) & df["arm"].isin(["heap", "bounded"])
              & df["workload"].notna() & df["pool_buffers"].notna()
              & df["cap_mb"].notna()]
    # Deterministic dodge: all-fail cells of one workload share the free-running
    # rate, so stagger them slightly by pool rank to keep every cell visible.
    pool_rank = grid.groupby(["arm", "cap_mb", "workload"])["pool_buffers"].rank(method="dense")
    grid = grid.assign(pool_rank=pool_rank)

    cx, cy, fx, fy = [], [], [], []   # complete / fail point coords
    death_fail = death_total = 0
    for cell, runs in grid.groupby("cell"):
        w = int(runs["workload"].iloc[0])
        rho = rho_mb[w] / runs["cap_mb"].iloc[0]
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        completes = n_ok >= reps / 2
        ok_tps = runs[runs["ok"]]["tps"]
        rate = float(ok_tps.median()) if n_ok else free.get(w)
        if rate is None:
            continue
        if not n_ok:  # dodge only proxy-rate points
            rate *= 1 + 0.012 * (runs["pool_rank"].iloc[0] - 2)
        rateM = rate / 1e6
        (cx if completes else fx).append(rateM)
        (cy if completes else fy).append(rho)
        if rho > 1 and rateM >= CEILING:        # the race death zone
            death_total += 1
            death_fail += not completes

    xlo, xhi, ylo, yhi = 0.6, 3.6, 0.3, 3.3

    # Region shading: the "fits" band (rho<1) and the race death zone (rho>1 and
    # ingest past the drain ceiling). The shading IS the message; the points fall
    # into it.
    ax.axhspan(ylo, 1.0, color=style.GREEN, alpha=0.06, zorder=0)
    ax.add_patch(Rectangle((CEILING, 1.0), xhi - CEILING, yhi - 1.0,
                           facecolor=style.RED, alpha=0.09, zorder=0, linewidth=0))
    ax.axvspan(*BAND, color=style.GREY, alpha=0.22, zorder=1)
    ax.axhline(1.0, color="#666666", lw=0.9, ls="--", zorder=2)

    # Points: single shape, colored by outcome (the race is arm-independent).
    ax.scatter(fx, fy, marker="o", facecolors="none", edgecolors=style.RED,
               s=44, linewidths=1.15, zorder=3, alpha=0.9)
    ax.scatter(cx, cy, marker="o", c=style.GREEN, s=40, alpha=0.9,
               edgecolors="black", linewidths=0.4, zorder=4)

    # Reference-line labels.
    ax.text(sum(BAND) / 2, 2.35, "SD drain ceiling $\\approx$2.5–2.7 M/s",
            rotation=90, ha="center", va="center", fontsize=8.2, style="italic",
            color="#3a3a3a")
    ax.text(2.62, 1.04, "$\\rho = 1$: above, state must spill",
            ha="left", va="bottom", fontsize=8.3, color="#444444")

    # Region narration.
    ax.text(xhi - 0.05, yhi - 0.06,
            f"the race: spill + out-sprint the drain\n"
            f"$\\rightarrow$ {death_fail} of {death_total} cells die",
            ha="right", va="top", fontsize=8.8, color="#8a1500", fontweight="bold")
    ax.text(1.18, 1.92, "must spill, but throttled below\nthe ceiling $\\rightarrow$ survives",
            ha="center", va="center", fontsize=8.4, color="#0b5c46",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.7))
    ax.text(1.5, 0.52, "fits in memory ($\\rho<1$): little to drain, sprinting is safe",
            ha="center", va="center", fontsize=8.4, color="#0b5c46",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.7))

    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_xlabel("ingest rate (million tuples/s; realized for survivors, "
                  "free-running demand for all-fail cells)")
    ax.set_ylabel("state pressure $\\rho$")
    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec="black",
               label="completes (plotted at its realized, throttled rate)"),
        Line2D([], [], marker="o", ls="", mfc="none", mec=style.RED, mew=1.15,
               label="fails (plotted at its free-running demand rate)"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8.3, framealpha=0.9)
    fig.suptitle("Completion is a race between ingest rate and spill-drain rate",
                 fontsize=11, fontweight="bold")
    style.caption(fig)
    print(f"race death zone (rho>1 & rate>={CEILING}): {death_fail}/{death_total} fail")
    style.save(fig, "fig_race")


if __name__ == "__main__":
    main()
