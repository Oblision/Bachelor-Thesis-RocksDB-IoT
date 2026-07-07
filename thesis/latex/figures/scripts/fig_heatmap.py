"""fig:heatmap-{512,1024,2048} — operating windows of both architectures
(completion only), one standalone figure per cap.

Completion per cell over pool x workload(rho), for the unbounded (heap, L_
matrix incl. in-memory baselines) and bounded (pooled, W_ grid) architectures.
16/8 RocksDB configuration only (write buffer / block cache); the paired
32/32 backend-size contrast is a separate, single-variable experiment
analysed on its own in Section~sec:rb32 / Appendix E.6, not pooled in here.
This figure carries ONE message: where each architecture completes. The
failure-reason breakdown lives in fig_geometry_law (bounded two-sided law)
and the s75 pool sweep in fig_knife; both were split out of this figure to
keep it readable. Every cell in the probed 16/8 grid has data (no untested
cells occur here), so there is no "not tested" marker.

Split into one figure per cap (was a single 2x3 grid, then briefly a
split-cell design that combined 16/8 with 32/32 — retired because the two
configurations disagree too sharply cell by cell to read as one dataset;
see TERM-MAP.md) because the pattern differs enough cap to cap that a
single caption could not honestly describe all three: the bounded
architecture's completion rises with pool size almost monotonically at 1024
and 2048 MB, while the heap architecture does not (see the docstring note on
r500/r700 below and Section 7.2.4b's up-front-reservation mechanism).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import data
import gridcells as gc
import style


def _cell_face(n_ok: int, reps: int):
    return gc.blend(style.GREEN, n_ok / reps) if n_ok else gc.blend(style.RED, 0.30)


def _draw_cell(ax, j: int, i: int, runs) -> None:
    n_ok, reps = int(runs["ok"].sum()), len(runs)
    ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=_cell_face(n_ok, reps),
                               edgecolor="white", linewidth=0.6, zorder=1))
    ax.text(j, i, f"{n_ok}/{reps}", ha="center", va="center",
            fontsize=6.6, color="black", zorder=3)


def _panel(ax, sub, rho_mb, cap: int, with_inmem) -> None:
    _pools, cols, ws = gc.cols_workloads(sub, with_inmem, rho_mb)
    for i, j, _w, runs in gc.iter_cells(sub, with_inmem, rho_mb):
        if runs.empty:
            gc.draw_untested_dot(ax, j, i)
            continue
        _draw_cell(ax, j, i, runs)
    gc.dress_axes(ax, cols, ws, rho_mb, cap)


def main() -> None:
    style.use_style()
    df = data.load_runs()
    df16 = df[~df["cell"].str.contains("b32")]  # 16/8 baseline only; see docstring
    rho_mb = data.rho_table(df16)
    g = gc.grid_df(df16)

    for cap in gc.CAPS:
        heap, inmem, bounded = gc.arm_frames(g, cap)
        fig, axes = plt.subplots(2, 1, figsize=(6.6, 7.4))
        _panel(axes[0], heap, rho_mb, cap, inmem)
        _panel(axes[1], bounded, rho_mb, cap, None)
        for ax in axes:
            ax.set_title("")  # the cap is in the figure's own suptitle instead

        axes[0].set_ylabel("unbounded (heap)\nengine default, 4096 B buffers\n"
                           "$\\rho$ at cap  (workload id)", fontsize=8.0)
        axes[1].set_ylabel("bounded (pooled)\ncorrected flag, 8192 B buffers\n"
                           "$\\rho$ at cap  (workload id)", fontsize=8.0)
        axes[1].set_xlabel("pool size (buffers)", fontsize=9)

        legend_handles = [
            Patch(facecolor=gc.blend(style.GREEN, 1.0), edgecolor="white",
                  label="all reps complete"),
            Patch(facecolor=gc.blend(style.GREEN, 0.55), edgecolor="white",
                  label="partial (label = completed/attempted)"),
            Patch(facecolor=gc.blend(style.RED, 0.30), edgecolor="white",
                  label="no completion"),
        ]
        fig.legend(handles=legend_handles, loc="outside lower center", ncol=1,
                   fontsize=7.8, frameon=False)
        fig.suptitle(f"Operating windows at {cap} MB: completion across pool "
                     "x workload (16/8 baseline)",
                     fontsize=10.5, fontweight="bold")
        style.caption(fig, extra="cell = completed/attempted; "
                                 "failure reasons in the geometry-law figures")
        style.save(fig, f"fig_heatmap_{cap}")


if __name__ == "__main__":
    main()
