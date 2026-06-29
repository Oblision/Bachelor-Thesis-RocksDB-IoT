"""fig:geometry — the two-sided geometry law, regenerated from the final grid.

Per cap and architecture: completion per (workload, pool) cell, with the failure
MODE distinguished — allocation abort (pool too small for the demand) vs OOM kill
(pool + induced overhead + base footprint breaks the cap). The knife-edge cell
(512 MB heap, r75: 10/10 at 16k, 6/10 at 32k, 0/10 at 64k) is annotated.
Replaces the outdated img/geometry-feasibility.png.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import style

CAPS = (512, 1024, 2048)
OOM_RCS = {"137", "137c"}
CRASH_RCS = {"11"}


def _modal_rc(runs) -> str:
    bad = runs[~runs["ok"]]["rc"]
    return bad.mode().iloc[0] if len(bad) else ""


def _cell_marker(n_ok: int, reps: int, rc: str) -> tuple[str, str]:
    """(color, marker) for a cell outcome."""
    if n_ok >= reps / 2:
        return style.GREEN, "o"
    if n_ok > 0:
        return style.YELLOW, "o"
    if rc in CRASH_RCS:
        return style.PURPLE, "X"
    if rc in OOM_RCS:
        return style.RED, "^"
    return style.ORANGE, "v"


def _panel(ax, df, rho_mb, cap: int, campaign: str, arm: str) -> None:
    # Feasibility-grid cells only: drop checksum-verification (chk) and reference
    # (ref) runs — they re-test an existing coordinate and exit with the rc141
    # teardown status, which would draw a spurious "dies" marker over a real one.
    sub = df[(df["campaign"] == campaign) & (df["arm"] == arm)
             & (df["cap_mb"] == cap) & df["workload"].notna()
             & df["pool_buffers"].notna()
             & ~df["cell"].str.contains("chk|ref", case=False, na=False)]
    workloads = sorted(sub["workload"].unique(), key=lambda w: rho_mb[int(w)])
    y_of = {w: i for i, w in enumerate(workloads)}
    pools = sorted(sub["pool_buffers"].unique())
    plotted: set[tuple[float, float]] = set()
    for cell, runs in sub.groupby("cell"):
        pool_buf = runs["pool_buffers"].iloc[0]
        w = runs["workload"].iloc[0]
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        color, marker = _cell_marker(n_ok, reps, _modal_rc(runs))
        ax.scatter(pool_buf * 8192 / 2**20, y_of[w], s=110, c=color, marker=marker,
                   edgecolors="black", linewidths=0.5, zorder=3)
        plotted.add((w, pool_buf))
    # (workload, pool) combinations never run: explicit hollow marker so a blank
    # cell reads as "not in the probed grid", not as a dropped/lost data point.
    for w in workloads:
        for pool_buf in pools:
            if (w, pool_buf) not in plotted:
                ax.scatter(pool_buf * 8192 / 2**20, y_of[w], s=30, facecolors="none",
                           edgecolors=style.GREY, linewidths=0.8, marker="o",
                           alpha=0.7, zorder=1)
    ax.set_yticks(range(len(workloads)),
                  [f"r{int(w)} ({rho_mb[int(w)] / cap:.2f})" for w in workloads],
                  fontsize=8)
    ax.set_xscale("log", base=2)
    ticks = sorted(set((sub["pool_buffers"] * 8192 / 2**20).round().astype(int)))
    ax.set_xticks(ticks, [str(t) for t in ticks], fontsize=8)
    ax.set_xlabel("pool size (MB)", fontsize=9)
    ax.set_ylim(-0.6, len(workloads) - 0.4)
    arch = ("heap, engine default (4096 B)" if arm == "heap"
            else "pooled, corrected flag (8192 B)")
    ax.set_title(f"cap {cap} MB  ·  {arch}", fontsize=9)


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)

    # 3 rows (caps) x 2 cols (arms): heap and pooled sit side by side per cap,
    # so each cap is a direct heap-vs-bounded comparison (e.g. 512 heap | 512
    # pooled). Wider panels than the old 2x3, and the portrait shape suits the
    # narrow text column.
    fig, axes = plt.subplots(3, 2, figsize=(8.6, 7.0))
    for row, cap in enumerate(CAPS):
        _panel(axes[row, 0], df, rho_mb, cap, "L", "heap")
        _panel(axes[row, 1], df, rho_mb, cap, "W", "bounded")
    for row in axes:
        row[0].set_ylabel("workload ($\\rho$ at cap)", fontsize=9)

    # Knife edge (512 MB heap r75: 10/10 at 16k -> 6/10 at 32k -> 0/10 at 64k) is
    # described in the caption rather than annotated in-panel: the heap/512 grid is
    # fully populated, so any in-axes callout would occlude real data markers.

    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec="black", label="completes ($\\geq$ half reps)"),
        Line2D([], [], marker="o", ls="", mfc=style.YELLOW, mec="black", label="partial"),
        Line2D([], [], marker="v", ls="", mfc=style.ORANGE, mec="black", label="dies: allocation abort (pool too small)"),
        Line2D([], [], marker="^", ls="", mfc=style.RED, mec="black", label="dies: OOM kill (pool + overhead $>$ cap)"),
        Line2D([], [], marker="X", ls="", mfc=style.PURPLE, mec="black", label="dies: crash cluster"),
        Line2D([], [], marker="o", ls="", mfc="none", mec=style.GREY, label="not run (outside probed grid)"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=3, fontsize=8.5,
               frameon=False)
    fig.suptitle("Feasibility corridor of both architectures (cap × pool size × workload)",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="each marker = one cell; filled = ≥ half of its reps complete")
    style.save(fig, "fig_geometry")


if __name__ == "__main__":
    main()
