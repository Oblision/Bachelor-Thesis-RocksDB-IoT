"""fig:geometry-law — the two-sided geometry law of the bounded architecture.

One panel per cap, bounded (W_) grid only. Every failing cell is coloured by
its dominant failure reason and annotated "N ok / failure split": the
pool-exhaustion aborts (rc 141) gather at the small-pool edge, the memory-
pressure failures (rc 137 OOM kill / rc 134 allocator abort, counted
together per the thesis accounting policy) at the large-pool edge. Cells
mixing categories are hatched and write the split out; the corruption
segfaults (rc 139/11) appear as "crash" and only inside cells that fail
regardless. The heap arm and the in-memory baseline are NOT in this figure:
the law is a bounded-architecture statement (fig_heatmap has the windows).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import data
import gridcells as gc
import style


def _reason(runs) -> tuple[str, bool, list[tuple[str, int]]]:
    """(modal reason, is_mixed, [(reason, count) desc]) over failing reps."""
    bad = runs[~runs["ok"]]
    if bad.empty:
        return "", False, []
    counts = bad["rc"].map(gc.classify_rc).value_counts()
    parts = [(str(r), int(n)) for r, n in counts.items()]
    return parts[0][0], len(parts) > 1, parts


def _panel(ax, sub, rho_mb, cap: int) -> list[tuple]:
    _pools, cols, ws = gc.cols_workloads(sub, None, rho_mb)
    mixed_here: list[tuple] = []
    for i, j, w, runs in gc.iter_cells(sub, None, rho_mb):
        if runs.empty:
            gc.draw_untested_dot(ax, j, i)
            continue
        n_ok, reps = int(runs["ok"].sum()), len(runs)
        reason, mixed, parts = _reason(runs)
        tokens = [f"{n} {gc.REASON_STYLE[r][0]}" for r, n in parts]
        # wrap the failure split: <=2 categories on one line, 3+ on two
        if len(tokens) <= 2:
            split = "+".join(tokens)
        else:
            split = tokens[0] + "\n+" + "+".join(tokens[1:])
        if n_ok == reps:
            fc, label, tc = gc.blend(style.GREEN, 1.0), f"{n_ok} ok", "black"
        elif n_ok >= reps / 2:
            fc, tc = gc.blend(style.GREEN, 0.55), "black"
            label = f"{n_ok} ok\n{split}" if mixed else f"{n_ok} ok / {split}"
        else:
            base = gc.REASON_STYLE[reason][1]
            fc = gc.blend(base, (reps - n_ok) / reps)
            lum = 0.299 * fc[0] + 0.587 * fc[1] + 0.114 * fc[2]
            tc = "white" if lum < 0.55 else "black"
            label = f"{n_ok} ok\n{split}" if mixed else f"{n_ok} ok / {split}"
        if mixed:
            mixed_here.append((cap, int(w), cols[j], split.replace("\n", "")))
        ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=fc,
                                   edgecolor="white", linewidth=0.6, zorder=1))
        if mixed:
            # soft hatch so the written-out split stays legible on top
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                       facecolor="none",
                                       edgecolor=(0, 0, 0, 0.38), hatch="//",
                                       linewidth=0.0, zorder=2))
        ax.text(j, i, label, ha="center", va="center",
                fontsize=5.2 if mixed else 5.9, color=tc, zorder=3,
                bbox=(dict(boxstyle="round,pad=0.12", fc="white", ec="none",
                           alpha=0.62) if mixed else None))
    gc.dress_axes(ax, cols, ws, rho_mb, cap)
    ax.set_xlabel("pool size (buffers)", fontsize=9)
    # The two bounds of the law, tagged INSIDE the axes in a reserved strip
    # above the top row (constrained layout does not account for artists
    # floating outside the axes box, so the tags must live within it).
    ax.set_ylim(-0.5, len(ws) + 1.15)
    ax.annotate("pool too small $\\to$ abort", xy=(0, len(ws) - 0.42),
                xytext=(max(-0.4, -0.5 + 0.1), len(ws) + 0.52), ha="left",
                fontsize=6.4, color="#8a5200",
                arrowprops=dict(arrowstyle="->", lw=0.8, color="#8a5200"))
    ax.annotate("pool $+$ overhead $>$ cap $\\to$ OOM",
                xy=(len(cols) - 1, len(ws) - 0.42),
                xytext=(len(cols) - 0.6, len(ws) + 0.52), ha="right",
                fontsize=6.4, color="#8a1500",
                arrowprops=dict(arrowstyle="->", lw=0.8, color="#8a1500"))
    return mixed_here


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    g = gc.grid_df(df)

    fig, axes = plt.subplots(1, 3, figsize=(11.8, 4.2))
    all_mixed: list[tuple] = []
    for col, cap in enumerate(gc.CAPS):
        _heap, _inm, bounded = gc.arm_frames(g, cap)
        all_mixed += _panel(axes[col], bounded, rho_mb, cap)
    axes[0].set_ylabel("bounded (pooled)\n$\\rho$ at cap  (workload id)",
                       fontsize=8.0)

    legend_handles = [
        Patch(facecolor=gc.blend(style.GREEN, 1.0), edgecolor="white",
              label="all reps complete"),
        Patch(facecolor=gc.blend(style.RED, 0.85), edgecolor="white",
              label="fails: memory pressure (rc 137 OOM kill / rc 134 alloc abort)"),
        Patch(facecolor=gc.blend(style.ORANGE, 0.85), edgecolor="white",
              label="fails: pool-abort (rc 141, pool too small)"),
        Patch(facecolor="white", edgecolor="black", hatch="///",
              label="mixes failure modes; split written out\n"
                    "(crash = corruption segfault, rc 139/11)"),
    ]
    fig.legend(handles=legend_handles, loc="outside lower center", ncol=2,
               fontsize=7.4, frameon=False)
    fig.suptitle("The two-sided geometry law: failure reason across pool size "
                 "(bounded architecture)",
                 fontsize=11, fontweight="bold")
    print(f"geometry-law: mixed-mode cells = {len(all_mixed)}")
    for m in all_mixed:
        print("   mixed:", m)
    style.caption(fig, extra="cell = N ok / failure split; colour = modal "
                             "failure reason; hatch = mixed modes; dot = not run")
    style.save(fig, "fig_geometry_law")


if __name__ == "__main__":
    main()
