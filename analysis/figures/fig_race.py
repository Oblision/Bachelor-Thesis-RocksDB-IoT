"""fig:race — completion as a race between ingest rate and spill-drain rate.

One point per spilling cell (L_ heap + W_ bounded grids, pooled together: the
race holds for both architectures). 16/8 RocksDB configuration only. x = the
cell's ingest rate: realized median tps of completing reps, or, for all-fail
cells, the workload's unthrottled demand (median tps of its completing in-memory
/ uncapped reference runs; a dead run has no realized rate of its own). y =
state pressure rho.

Encoding: colour AND shape both carry outcome (redundant, so outcome reads
at a glance without checking colour); fill carries architecture.
  green circle    = completes
  orange square   = pool-exhaustion abort (the pool is too small for the hot band)
  red X           = OOM kill (the pool plus its overhead breaks the cap)
  filled          = heap architecture
  hollow (white fill, coloured edge) = bounded architecture

The plane reads as three bands, split by the rho=1 memory boundary and the
storage-drain ceiling (~2.6 M/s):
  - rho < 1: the state fits in memory. The capped in-memory baseline completes
    here, yet the backend still fails a number of these cells at a mis-sized
    pool: too small aborts (orange), too large OOMs (red). Both edges appear.
  - rho > 1, ingest below the ceiling: survivors, paced to the drain rate.
  - rho > 1, ingest above the ceiling: ingest out-runs the drain and the run
    fails.
"""
from __future__ import annotations

from collections import Counter

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

import data
import gridcells as gc
import style

CEILING = 2.7   # right edge of the SD drain band; death zone starts here
BAND = (2.5, 2.7)


def _free_running(df) -> dict[int, float]:
    """Unthrottled ingest rate per workload from completing reference runs."""
    ref = df[df["ok"] & df["workload"].notna()
             & df["arm"].isin(["inmem", "uncapped"]) & (df["tps"] > 0)]
    return {int(w): float(v) for w, v in ref.groupby("workload")["tps"].median().items()}


def _drain_ceiling(df, rho_mb, grid) -> float:
    """Empirical drain ceiling: the max realized median tps (M/s) of any
    SURVIVING spilling cell above rho=1 (a paced survivor). Recomputed here from
    the TSVs so the reference line is data-derived."""
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
    df = df[~df["cell"].str.contains("b32")]  # 16/8 baseline only
    rho_mb = data.rho_table(df)
    free = _free_running(df)

    fig, ax = plt.subplots(figsize=(8.2, 4.7))
    # grid_df drops the chk/ref verification reruns, whose rc141 teardown status
    # would otherwise appear as a spurious all-fail pool-abort cell.
    grid = gc.grid_df(df)
    grid = grid[grid["campaign"].isin(["L", "W"]) & grid["arm"].isin(["heap", "bounded"])
                & grid["workload"].notna() & grid["pool_buffers"].notna()
                & grid["cap_mb"].notna()]
    # Deterministic dodge: all-fail cells of one workload share the demand rate,
    # so stagger them slightly by pool rank to keep every cell visible.
    pool_rank = grid.groupby(["arm", "cap_mb", "workload"])["pool_buffers"].rank(method="dense")
    grid = grid.assign(pool_rank=pool_rank)

    drain_ceiling = _drain_ceiling(df, rho_mb, grid)

    # Categorise each cell: completes, or fails by dominant reason. Colour
    # encodes the reason (pool abort vs OOM), shape encodes the architecture.
    pts = {(a, o): ([], []) for a in ("heap", "bounded") for o in ("ok", "POOL", "OOM")}
    lowrho_pool = lowrho_oom = 0
    for cell, runs in grid.groupby("cell"):
        arm = runs["arm"].iloc[0]
        w = int(runs["workload"].iloc[0])
        rho = rho_mb[w] / runs["cap_mb"].iloc[0]
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        completes = n_ok >= reps / 2
        if completes:
            rate = float(runs[runs["ok"]]["tps"].median())
            cat = "ok"
        else:
            rate = free.get(w)
            if rate is None:
                continue
            rate *= 1 + 0.012 * (runs["pool_rank"].iloc[0] - 2)  # dodge proxy points
            cats = Counter(gc.classify_rc(rc) for rc in runs[~runs["ok"]]["rc"])
            cat = "POOL" if cats.get("POOL", 0) >= cats.get("OOM", 0) else "OOM"
            if rho < 1:
                lowrho_pool += cat == "POOL"
                lowrho_oom += cat == "OOM"
        xs, ys = pts[(arm, cat)]
        xs.append(rate / 1e6)
        ys.append(rho)

    xlo, xhi, ylo, yhi = 0.6, 3.6, 0.3, 3.3

    # Light, neutral reference bands so the green/orange/red markers stand out.
    ax.axhspan(ylo, 1.0, color=style.GREEN, alpha=0.05, zorder=0)
    ax.add_patch(Rectangle((xlo, 1.0), drain_ceiling - xlo, yhi - 1.0,
                           facecolor=style.SKY, alpha=0.10, zorder=0, linewidth=0))
    ax.add_patch(Rectangle((drain_ceiling, 1.0), xhi - drain_ceiling, yhi - 1.0,
                           facecolor=style.GREY, alpha=0.14, zorder=0, linewidth=0))
    ax.axhline(1.0, color="#666666", lw=0.9, ls="--", zorder=2)         # rho=1, exact
    ax.axvline(drain_ceiling, color="#3a3a3a", lw=1.1, ls=(0, (4, 2)), zorder=2)

    # Markers: shape = outcome (circle/square/X), redundant with colour so the
    # outcome reads without checking colour; fill = architecture (filled =
    # heap, hollow white fill + coloured edge = bounded). Shape previously
    # doubled for architecture, which collided with "circle" also meaning
    # heap architecture elsewhere in this figure family; fill now carries
    # architecture instead, freeing shape for outcome alone.
    SHAPE = {"ok": "o", "POOL": "s", "OOM": "X"}
    REASON = {"ok": style.GREEN, "POOL": style.ORANGE, "OOM": "#D62728"}
    for cat in ("POOL", "OOM", "ok"):
        for arm in ("heap", "bounded"):
            xs, ys = pts[(arm, cat)]
            hollow = arm == "bounded"
            size = 42 if cat == "ok" else 48
            ax.scatter(xs, ys, marker=SHAPE[cat],
                       facecolors=("white" if hollow else REASON[cat]),
                       edgecolors=(REASON[cat] if hollow else "black"),
                       s=size, linewidths=(1.1 if hollow else 0.5),
                       zorder=(4 if cat == "ok" else 3), alpha=0.92)

    # Reference-line labels.
    ax.text(drain_ceiling, 3.16,
            f"storage-bandwidth ceiling $\\approx${drain_ceiling:.1f} M/s",
            ha="center", va="center", fontsize=8.2, style="italic", color="#3a3a3a",
            bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#bbbbbb", lw=0.5, alpha=0.9))
    ax.text(0.66, 1.05, "$\\rho = 1$", ha="left", va="bottom", fontsize=8.2, color="#444444")

    # Region tags, kept away from the data clusters.
    BLUE_T, GREY_T, GREEN_T = "#0a4a6e", "#444444", "#0b5c46"
    ax.text(1.45, 3.05, "throttled\n(ingest below the ceiling)",
            ha="center", va="center", fontsize=8.4, color=BLUE_T, fontweight="bold")
    ax.text(3.12, 2.98, "ingest exceeds\nstorage bandwidth",
            ha="center", va="center", fontsize=8.4, color=GREY_T, fontweight="bold")
    ax.text(0.70, 0.40, "state fits in memory",
            ha="left", va="center", fontsize=8.6, color=GREEN_T, fontweight="bold")

    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_xlabel("ingest rate (million tuples/s)")
    ax.set_ylabel("state pressure $\\rho$")

    # Legend: shape = outcome (redundant with colour), fill = architecture.
    # Single legend so constrained_layout reserves its space (two stacked
    # legends do not, and collide with the title). Column-major fill order lays
    # the three outcomes across the top row and the two architectures on the
    # bottom row.
    G, O, R, S = style.GREEN, style.ORANGE, "#D62728", "#888888"
    handles = [
        Line2D([], [], marker="o", ls="", mfc=G, mec="black", label="completes"),
        Line2D([], [], marker="o", ls="", mfc=S, mec="black", label="heap (filled)"),
        Line2D([], [], marker="s", ls="", mfc=O, mec="black", label="pool abort (pool too small)"),
        Line2D([], [], marker="o", ls="", mfc="white", mec=S, mew=1.4, label="bounded (hollow)"),
        Line2D([], [], marker="X", ls="", mfc=R, mec="black", label="OOM (pool + overhead $>$ cap)"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.015),
              fontsize=7.9, framealpha=1.0, ncol=3, borderpad=0.4,
              columnspacing=1.2, handletextpad=0.4, labelspacing=0.5, borderaxespad=0.0)
    fig.suptitle("Cell outcomes across ingest rate and state pressure $\\rho$",
                 fontsize=11, fontweight="bold", y=1.05)
    style.caption(fig)
    print(f"drain ceiling = {drain_ceiling:.3f} M/s")
    print(f"rho<1 backend failures: pool-abort {lowrho_pool}, OOM {lowrho_oom}")
    style.save(fig, "fig_race")


if __name__ == "__main__":
    main()
