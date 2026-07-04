"""fig:heatmap — operating windows of both architectures (completion only).

Completion per cell over cap x pool x workload(rho), for the unbounded (heap,
L_ matrix incl. in-memory baselines) and bounded (pooled, W_ grid)
architectures. This figure carries ONE message: where each architecture
completes. The failure-reason breakdown lives in fig_geometry_law (bounded
two-sided law) and the r75 pool sweep in fig_knife; both were split out of
this figure to keep it readable. Untested cells are a visible grey dot.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

import data
import gridcells as gc
import style


def _panel(ax, sub, rho_mb, cap: int, with_inmem) -> None:
    _pools, cols, ws = gc.cols_workloads(sub, with_inmem, rho_mb)
    for i, j, _w, runs in gc.iter_cells(sub, with_inmem, rho_mb):
        if runs.empty:
            gc.draw_untested_dot(ax, j, i)
            continue
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        frac = n_ok / reps
        fc = gc.blend(style.GREEN, frac) if n_ok else gc.blend(style.RED, 0.30)
        ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=fc,
                                   edgecolor="white", linewidth=0.6, zorder=1))
        ax.text(j, i, f"{n_ok}/{reps}", ha="center", va="center",
                fontsize=6.6, color="black", zorder=3)
    gc.dress_axes(ax, cols, ws, rho_mb, cap)


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    g = gc.grid_df(df)

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 6.4))
    fig.subplots_adjust(wspace=0.52)
    for col, cap in enumerate(gc.CAPS):
        heap, inmem, bounded = gc.arm_frames(g, cap)
        _panel(axes[0, col], heap, rho_mb, cap, inmem)
        _panel(axes[1, col], bounded, rho_mb, cap, None)

    axes[0, 0].set_ylabel("unbounded (heap)\nengine default, 4096 B buffers\n"
                          "$\\rho$ at cap  (workload id)", fontsize=8.0)
    axes[1, 0].set_ylabel("bounded (pooled)\ncorrected flag, 8192 B buffers\n"
                          "$\\rho$ at cap  (workload id)", fontsize=8.0)
    for ax in axes[1]:
        ax.set_xlabel("pool size (buffers)", fontsize=9)

    legend_handles = [
        Patch(facecolor=gc.blend(style.GREEN, 1.0), edgecolor="white",
              label="all reps complete"),
        Patch(facecolor=gc.blend(style.GREEN, 0.55), edgecolor="white",
              label="partial (label = completed/attempted)"),
        Patch(facecolor=gc.blend(style.RED, 0.30), edgecolor="white",
              label="no completion"),
        Line2D([], [], marker="o", ls="", mfc=style.GREY, mec=style.GREY,
               ms=4.2, label="not in probed grid"),
    ]
    fig.legend(handles=legend_handles, loc="outside lower center", ncol=4,
               fontsize=7.8, frameon=False)
    fig.suptitle("Operating windows: completion across cap × pool × workload",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="cell = completed/attempted; dot = not run; "
                             "failure reasons in the geometry-law figure")
    style.save(fig, "fig_heatmap")


if __name__ == "__main__":
    main()
