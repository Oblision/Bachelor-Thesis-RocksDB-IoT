"""fig:heatmap — operating windows of both architectures.

Completion fraction per cell over cap x pool x workload(rho), for the unbounded
(heap, L_ matrix incl. in-memory baselines) and bounded (pooled, W_ grid)
architectures. Untested cells are blank.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colormaps

import data
import style

CAPS = (512, 1024, 2048)


def _pool_label(buffers: float) -> str:
    return f"{int(buffers / 1000)}k"


def _panel(ax, sub, rho_mb, cap: int, with_inmem) -> None:
    """One cap-panel: rows = workloads (heavy at the top), cols = pools."""
    pools = sorted(p for p in sub["pool_buffers"].dropna().unique() if p > 0)
    cols = (["in-mem"] if with_inmem is not None else []) + [_pool_label(p) for p in pools]
    workloads = sorted(sub["workload"].dropna().unique(), key=lambda w: rho_mb[int(w)])
    cmap = colormaps[style.OK_CMAP]

    grid = np.full((len(workloads), len(cols)), np.nan)
    text = [["" for _ in cols] for _ in workloads]
    for i, w in enumerate(workloads):
        sources = ([(0, with_inmem)] if with_inmem is not None else []) + [
            (cols.index(_pool_label(p)), sub[sub["pool_buffers"] == p]) for p in pools]
        for j, frame in sources:
            row = frame[frame["workload"] == w]
            if row.empty or row["reps"].sum() == 0:
                continue
            n_ok, reps = int(row["n_ok"].sum()), int(row["reps"].sum())
            grid[i, j] = n_ok / reps
            text[i][j] = f"{n_ok}/{reps}"

    ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto", origin="lower")
    for i in range(len(workloads)):
        for j in range(len(cols)):
            if text[i][j]:
                lum = cmap(grid[i, j])
                dark = 0.299 * lum[0] + 0.587 * lum[1] + 0.114 * lum[2] < 0.5
                ax.text(j, i, text[i][j], ha="center", va="center", fontsize=7,
                        color="white" if dark else "black")
            else:
                ax.text(j, i, "·", ha="center", va="center",
                        fontsize=7, color=style.GREY)
    ax.set_xticks(range(len(cols)), cols, fontsize=7.5)
    ax.set_yticks(range(len(workloads)),
                  [f"r{int(w)} ({rho_mb[int(w)] / cap:.2f})" for w in workloads],
                  fontsize=8)
    ax.set_title(f"cap {cap}\\,MB" if False else f"cap {cap} MB")
    ax.grid(False)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    cells = data.per_cell(df)

    fig, axes = plt.subplots(2, 3, figsize=(9.8, 6.4))
    for col, cap in enumerate(CAPS):
        heap = cells[(cells["campaign"] == "L") & (cells["arm"] == "heap")
                     & (cells["cap_mb"] == cap)]
        inmem = cells[(cells["campaign"] == "L") & (cells["arm"] == "inmem")
                      & (cells["cap_mb"] == cap)]
        bounded = cells[(cells["campaign"] == "W") & (cells["arm"] == "bounded")
                        & (cells["cap_mb"] == cap)]
        _panel(axes[0, col], heap, rho_mb, cap, with_inmem=inmem)
        _panel(axes[1, col], bounded, rho_mb, cap, with_inmem=None)

    axes[0, 0].set_ylabel("unbounded (heap)\nengine default, 4096 B buffers\nworkload ($\\rho$ at cap)",
                          fontsize=8.5)
    axes[1, 0].set_ylabel("bounded (pooled)\ncorrected flag, 8192 B buffers\nworkload ($\\rho$ at cap)",
                          fontsize=8.5)
    for ax in axes[1]:
        ax.set_xlabel("pool size (buffers)", fontsize=9)

    sm = plt.cm.ScalarMappable(cmap=colormaps[style.OK_CMAP],
                               norm=plt.Normalize(0, 1))
    cbar = fig.colorbar(sm, ax=axes, shrink=0.75, pad=0.02)
    cbar.set_label("completion fraction", fontsize=9)
    style.save(fig, "fig_heatmap")


if __name__ == "__main__":
    main()
