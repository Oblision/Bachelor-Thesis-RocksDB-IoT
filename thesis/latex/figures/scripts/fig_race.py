"""fig:race — completion as a race between ingest rate and spill-drain rate.

One point per spilling cell (L_ heap + W_ bounded grids). x = the cell's ingest
rate: realized median tps of completing reps, or, for all-fail cells, the
workload's free-running rate (median tps of its completing in-memory /
uncapped reference runs — a dead run's own tps is not meaningful).
y = state pressure rho. The survivor band sits below ~2.5 M tuples/s.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import style


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

    fig, ax = plt.subplots(figsize=(7.6, 5.4))
    seen: set[tuple[str, bool]] = set()
    grid = df[df["campaign"].isin(["L", "W"]) & df["arm"].isin(["heap", "bounded"])
              & df["workload"].notna() & df["pool_buffers"].notna()
              & df["cap_mb"].notna()]
    # Deterministic dodge: all-fail cells of one workload share the free-running
    # rate, so stagger them slightly by pool rank to keep every cell visible.
    pool_rank = grid.groupby(["arm", "cap_mb", "workload"])["pool_buffers"].rank(method="dense")
    grid = grid.assign(pool_rank=pool_rank)
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
        marker = "o" if arm == "heap" else "s"
        # Completing cells are filled at their REALIZED rate; failing cells are
        # hollow and sit at the free-running REFERENCE rate (a dead run has no
        # realized rate). The hollow fill is the visual cue that a red point's
        # x-position is a proxy, not a measured ingest rate.
        if completes:
            ax.scatter(rate / 1e6, rho, marker=marker, c=style.GREEN, s=46,
                       alpha=0.85, edgecolors="black", linewidths=0.4, zorder=3)
        else:
            ax.scatter(rate / 1e6, rho, marker=marker, facecolors="none",
                       edgecolors=style.RED, s=48, linewidths=1.1, zorder=3)
        seen.add((arm, completes))

    ax.axvspan(2.5, 2.7, color=style.GREY, alpha=0.25, zorder=1)
    ax.text(2.44, ax.get_ylim()[1] * 0.86, "survivor band edge\n2.5–2.7 M tuples/s",
            ha="right", va="top", fontsize=8.5, style="italic", color="#444444")
    ax.axhline(1.0, color=style.GREY, lw=0.8, ls="--", zorder=1)
    ax.text(ax.get_xlim()[1], 1.02, "$\\rho = 1$ ", ha="right", va="bottom",
            fontsize=8.5, color="#444444")

    ax.set_xlabel("ingest rate (million tuples/s; free-running reference for all-fail cells)")
    ax.set_ylabel("state pressure $\\rho$")
    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec="black", label="heap, completes (realized rate)"),
        Line2D([], [], marker="s", ls="", mfc=style.GREEN, mec="black", label="bounded, completes (realized rate)"),
        Line2D([], [], marker="o", ls="", mfc="none", mec=style.RED, mew=1.1, label="heap, fails (free-running rate)"),
        Line2D([], [], marker="s", ls="", mfc="none", mec=style.RED, mew=1.1, label="bounded, fails (free-running rate)"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8.2)
    fig.suptitle("Completion is a race between ingest rate and spill-drain rate",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="each point = one cell; all-fail cells plotted at the free-running reference rate")
    style.save(fig, "fig_race")


if __name__ == "__main__":
    main()
