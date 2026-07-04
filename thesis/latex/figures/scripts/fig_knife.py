"""fig:knife — pool-size sensitivity of the heap architecture at 512 MB, r75.

One workload, one cap, one knob: completion against pool size for the
L_ heap grid at 512 MB, r75. The pool is the only variable across the three
bars; enlarging it alone moves the cell from full completion (20/20 at 16k)
to total OOM (0/20 at 64k). Counts pool the 16/8 baseline with its 32/32
backend-size twin, as in the grid figures; the caption reports the 16/8-only
counts alongside.
"""
from __future__ import annotations

import matplotlib.pyplot as plt

import data
import gridcells as gc
import style


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    g = gc.grid_df(df)

    heap512, _inm, _bnd = gc.arm_frames(g, 512)
    sub = heap512[heap512["workload"] == 75]
    pools = sorted(p for p in sub["pool_buffers"].dropna().unique() if p > 0)

    # Same canvas width as the grid-figure family (11.8 in), so the figure
    # shares the family's boundaries and font scale when included with
    # \widefig; the bars occupy the centre third, the flanks stay empty.
    fig = plt.figure(figsize=(11.8, 3.0))
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1.15, 1])
    for side in (0, 2):
        fig.add_subplot(gs[0, side]).set_axis_off()
    ax = fig.add_subplot(gs[0, 1])
    for k, p in enumerate(pools):
        runs = sub[sub["pool_buffers"] == p]
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        frac = n_ok / reps
        if frac == 1:
            color = gc.blend(style.GREEN, 1.0)
        elif frac >= 0.5:
            color = gc.blend(style.GREEN, 0.55)
        else:
            color = gc.blend(style.RED, 0.75)
        ax.bar(k, frac, 0.62, color=color, edgecolor="black", linewidth=0.5)
        ax.text(k, frac + 0.03, f"{n_ok}/{reps}", ha="center", fontsize=8)
        print(f"knife: pool {gc.pool_label(p)} -> {n_ok}/{reps}")
    ax.set_xticks(range(len(pools)), [gc.pool_label(p) for p in pools])
    ax.set_ylim(0, 1.14)
    ax.set_yticks((0, 0.5, 1.0))
    ax.set_xlabel("pool size (buffers)")
    ax.set_ylabel("completion fraction")
    fig.suptitle("Pool-size sensitivity of the heap architecture "
                 "(512 MB cap, workload r75)",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="bars = completed/attempted; "
                             "$\\rho \\approx 0.64$; the pool is the only variable")
    style.save(fig, "fig_knife")


if __name__ == "__main__":
    main()
