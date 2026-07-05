"""fig:race — completion as a race between ingest rate and spill-drain rate.

One point per spilling cell (L_ heap + W_ bounded grids, pooled together: the
race holds for both architectures). 16/8 RocksDB configuration only: the
32/32 backend-size twin runs (*_b32_*) are excluded, matching the rest of the
RQ1 figure family; that contrast is analysed separately in Section~sec:rb32. x = the cell's ingest rate: realized median
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
import gridcells as gc
import style

CEILING = 2.7   # right edge of the SD drain band; death zone starts here
BAND = (2.5, 2.7)
# rho<1 failing cells split into two demand populations (free-running M/s):
#   heavy TRUE-CAPACITY cells sit below SLOW_MAX; fast POOL-GEOMETRY cells above
#   FAST_MIN. The two bands never overlap in the data (see report).
SLOW_MAX = 2.0
FAST_MIN = 2.7


def _free_running(df) -> dict[int, float]:
    """Free-running ingest rate per workload from completing reference runs."""
    ref = df[df["ok"] & df["workload"].notna()
             & df["arm"].isin(["inmem", "uncapped"]) & (df["tps"] > 0)]
    return {int(w): float(v) for w, v in ref.groupby("workload")["tps"].median().items()}


def _drain_ceiling(df, rho_mb, grid) -> float:
    """Empirical drain ceiling: the max realized median tps (M/s) of any
    SURVIVING spilling cell above rho=1 (a backpressure-throttled survivor).
    Recomputed here from the TSVs so the reference line is data-derived."""
    best = 0.0
    for _, runs in grid.groupby("cell"):
        w = int(runs["workload"].iloc[0])
        rho = rho_mb[w] / runs["cap_mb"].iloc[0]
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        if rho <= 1 or n_ok < reps / 2:
            continue
        ok_spill = runs[runs["ok"] & (runs["slices"] > 0)]
        if not ok_spill.empty:
            best = max(best, float(ok_spill["tps"].median()) / 1e6)
    return best


def main() -> None:
    style.use_style()
    df = data.load_runs()
    df = df[~df["cell"].str.contains("b32")]  # 16/8 baseline only; see docstring
    rho_mb = data.rho_table(df)
    free = _free_running(df)

    fig, ax = plt.subplots(figsize=(8.2, 4.7))
    # grid_df drops the chk/ref verification reruns, whose rc141 teardown
    # status would otherwise appear as a spurious all-fail pool-abort cell.
    grid = gc.grid_df(df)
    grid = grid[grid["campaign"].isin(["L", "W"]) & grid["arm"].isin(["heap", "bounded"])
                & grid["workload"].notna() & grid["pool_buffers"].notna()
                & grid["cap_mb"].notna()]
    # Deterministic dodge: all-fail cells of one workload share the free-running
    # rate, so stagger them slightly by pool rank to keep every cell visible.
    pool_rank = grid.groupby(["arm", "cap_mb", "workload"])["pool_buffers"].rank(method="dense")
    grid = grid.assign(pool_rank=pool_rank)

    drain_ceiling = _drain_ceiling(df, rho_mb, grid)

    # Group by (architecture, outcome): shape encodes the arm (circle = heap,
    # square = bounded), fill encodes the outcome. Failing cells split further by
    # WHERE they sit relative to rho=1:
    #   completes                 : filled green
    #   fails, rho>1              : red hollow (the race death zone)
    #   fails, rho<1, slow demand : TRUE-CAPACITY (heavy cell, orange)
    #   fails, rho<1, fast demand : FAST-INGEST / POOL-GEOMETRY (state fits but a
    #                               mis-sized pool kills it, purple)
    pts = {(a, o): ([], []) for a in ("heap", "bounded")
           for o in ("ok", "fail_race", "fail_truecap", "fail_poolgeom")}
    death_fail = death_total = 0
    n_truecap = n_poolgeom = 0
    for cell, runs in grid.groupby("cell"):
        arm = runs["arm"].iloc[0]
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
        if completes:
            outcome = "ok"
        elif rho > 1:
            outcome = "fail_race"
        else:  # rho < 1 failure: split by free-running demand (M/s)
            demand = free.get(w, rate) / 1e6
            if demand < SLOW_MAX:
                outcome = "fail_truecap"
                n_truecap += 1
            else:  # demand > FAST_MIN
                outcome = "fail_poolgeom"
                n_poolgeom += 1
        xs, ys = pts[(arm, outcome)]
        xs.append(rateM)
        ys.append(rho)
        if rho > 1 and rateM >= CEILING:        # the race death zone
            death_total += 1
            death_fail += not completes

    xlo, xhi, ylo, yhi = 0.6, 3.6, 0.3, 3.3

    # Three shaded regions, split by the rho=1 memory boundary and the SD drain
    # ceiling. The shading IS the message; the points fall into it.
    #   green  rho<1        : state fits in memory, spilling is rare.
    #   blue   rho>1, slow  : must spill, but ingest is below the drain rate.
    #   red    rho>1, fast  : must spill and ingest outruns the drain (the race).
    ax.axhspan(ylo, 1.0, color=style.GREEN, alpha=0.07, zorder=0)
    ax.add_patch(Rectangle((xlo, 1.0), BAND[0] - xlo, yhi - 1.0,
                           facecolor=style.SKY, alpha=0.14, zorder=0, linewidth=0))
    ax.add_patch(Rectangle((CEILING, 1.0), xhi - CEILING, yhi - 1.0,
                           facecolor=style.RED, alpha=0.10, zorder=0, linewidth=0))
    ax.axvspan(*BAND, color=style.GREY, alpha=0.22, zorder=1)
    # rho=1 line: EXACT BY DEFINITION (footprint / cap), not fitted.
    ax.axhline(1.0, color="#666666", lw=0.9, ls="--", zorder=2)
    # Empirical drain ceiling: the fastest surviving spilling cell above rho=1.
    ax.axvline(drain_ceiling, color="#3a3a3a", lw=1.1, ls=(0, (4, 2)), zorder=2)

    # Points: shape = arm, colour/fill = outcome. Fails drawn first so completes
    # sit on top. The two rho<1 failure populations get distinct colours+markers.
    mark = {"heap": "o", "bounded": "s"}
    for arm in ("heap", "bounded"):
        tx, ty = pts[(arm, "fail_truecap")]
        ax.scatter(tx, ty, marker="v", facecolors=style.ORANGE, edgecolors="black",
                   s=46, linewidths=0.5, zorder=3, alpha=0.9)
        px, py = pts[(arm, "fail_poolgeom")]
        ax.scatter(px, py, marker="P", facecolors=style.PURPLE, edgecolors="black",
                   s=52, linewidths=0.5, zorder=3, alpha=0.9)
    for arm in ("heap", "bounded"):
        fx, fy = pts[(arm, "fail_race")]
        ax.scatter(fx, fy, marker=mark[arm], facecolors="none", edgecolors=style.RED,
                   s=46, linewidths=1.15, zorder=3, alpha=0.9)
    for arm in ("heap", "bounded"):
        cx, cy = pts[(arm, "ok")]
        ax.scatter(cx, cy, marker=mark[arm], c=style.GREEN, s=42, alpha=0.9,
                   edgecolors="black", linewidths=0.4, zorder=4)

    # Reference-line labels: one short tag per line, horizontal, in empty space.
    # The caption carries the full explanation; the plot only names the regions.
    ax.text(drain_ceiling, 3.16,
            f"drain ceiling $\\approx${drain_ceiling:.1f} M/s",
            ha="center", va="center", fontsize=8.2, style="italic", color="#3a3a3a",
            bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#bbbbbb", lw=0.5, alpha=0.9))
    ax.text(0.66, 1.05, "$\\rho = 1$: above, state must spill",
            ha="left", va="bottom", fontsize=8.2, color="#444444")

    # Region tags: three short labels, colored to their region, kept away from
    # the data clusters (upper strip and lower-left corner are point-free).
    GREEN_T, BLUE_T, RED_T = "#0b5c46", "#0a4a6e", "#8a1500"
    ax.text(1.45, 3.05, "throttled: spiller keeps up",
            ha="center", va="center", fontsize=8.6, color=BLUE_T, fontweight="bold")
    ax.text(3.14, 2.62,
            f"the race: ingest outruns drain\n{death_fail}/{death_total} fail",
            ha="center", va="center", fontsize=8.6, color=RED_T, fontweight="bold")
    ax.text(0.72, 0.44, "state fits\nin memory",
            ha="left", va="center", fontsize=8.6, color=GREEN_T, fontweight="bold")

    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_xlabel("ingest rate (million tuples/s; realized for survivors, "
                  "free-running demand for all-fail cells)")
    ax.set_ylabel("state pressure $\\rho$")
    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec="black", label="completes (heap)"),
        Line2D([], [], marker="s", ls="", mfc=style.GREEN, mec="black", label="completes (bounded)"),
        Line2D([], [], marker="o", ls="", mfc="none", mec=style.RED, mew=1.15, label="fails, $\\rho>1$ (heap)"),
        Line2D([], [], marker="s", ls="", mfc="none", mec=style.RED, mew=1.15, label="fails, $\\rho>1$ (bounded)"),
        Line2D([], [], marker="v", ls="", mfc=style.ORANGE, mec="black", label="fails, $\\rho<1$: capacity (slow; both arms)"),
        Line2D([], [], marker="P", ls="", mfc=style.PURPLE, mec="black", label="fails, $\\rho<1$: pool geometry (fast; both arms)"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.015),
              fontsize=7.9, framealpha=1.0, ncol=3, borderpad=0.45,
              columnspacing=1.2, handletextpad=0.4, labelspacing=0.4,
              borderaxespad=0.0)
    fig.suptitle("Cell outcomes across ingest rate and state pressure $\\rho$",
                 fontsize=11, fontweight="bold", y=1.02)
    style.caption(fig)
    print(f"drain ceiling (max realized tps of a surviving rho>1 spilling cell) = {drain_ceiling:.3f} M/s")
    print(f"race death zone (rho>1 & rate>={CEILING}): {death_fail}/{death_total} fail")
    print(f"rho<1 failures: true-capacity {n_truecap}, pool-geometry {n_poolgeom}")
    style.save(fig, "fig_race")


if __name__ == "__main__":
    main()
