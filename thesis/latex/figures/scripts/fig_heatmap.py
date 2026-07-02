"""fig:heatmap — operating windows of both architectures (extended).

Completion per cell over cap x pool x workload(rho), for the unbounded (heap,
L_ matrix incl. in-memory baselines) and bounded (pooled, W_ grid) architectures.
This figure absorbs the retired geometry figure: every failing cell is now
coloured by its dominant FAILURE REASON — OOM-kill (rc 137/137c: pool + induced
overhead breaks the cap) vs pool-abort (rc 141: pool too small for the demand) —
and each cell is annotated "N ok / M REASON". Completing cells stay on the
green completion ramp. The rho-in-parentheses annotation and the two-row layout
(heap top / pooled bottom) are preserved. Untested cells are a dot.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.lines import Line2D

import data
import style

CAPS = (512, 1024, 2048)

OOM_RCS = {"137", "137c"}      # cap broken: pool + overhead + base > cap
POOL_RCS = {"141"}            # allocation abort: pool too small for demand

# Reason -> (display token, base colour for failing cells). Failing cells are
# shaded from a pale tint (few reps failed) to the full colour (all reps failed)
# of their MODAL reason, so the reason is legible at a glance and severity shows
# through saturation.
REASON_STYLE = {
    "OOM": ("OOM", style.RED),
    "POOL": ("abort", style.ORANGE),
    "OTHER": ("err", style.PURPLE),
}


def _pool_label(buffers: float) -> str:
    return f"{int(buffers / 1000)}k"


def _classify(rc: str) -> str:
    if rc in OOM_RCS:
        return "OOM"
    if rc in POOL_RCS:
        return "POOL"
    return "OTHER"


def _reason(runs) -> tuple[str, bool]:
    """(modal failure reason, is_mixed) over the failing reps of a cell.

    is_mixed flags a cell whose failing reps genuinely span BOTH OOM (rc137) and
    pool-abort (rc141): the spec asks us to check for these and not to fake a
    single reason where the data mixes. Such cells are drawn with a hatch.
    """
    bad = runs[~runs["ok"]]
    if bad.empty:
        return "", False
    reasons = bad["rc"].map(_classify)
    present = set(reasons) & {"OOM", "POOL"}
    modal = reasons.mode().iloc[0]
    return modal, len(present) > 1


def _blend(color, frac: float):
    """Blend `color` toward white; frac in [0,1], 1 = full colour."""
    r, g, b = to_rgb(color)
    t = 0.25 + 0.75 * frac  # keep a minimum tint so even 1/10 fails reads
    return (1 - t + t * r, 1 - t + t * g, 1 - t + t * b)


def _panel(ax, sub, rho_mb, cap: int, with_inmem) -> list[tuple]:
    """One cap-panel: rows = workloads (heavy at the top), cols = pools.

    Returns the list of mixed (rc137+rc141) cells found in this panel so the
    caller can report the check result.
    """
    pools = sorted(p for p in sub["pool_buffers"].dropna().unique() if p > 0)
    cols = (["in-mem"] if with_inmem is not None else []) + [_pool_label(p) for p in pools]
    workloads = sorted(sub["workload"].dropna().unique(), key=lambda w: rho_mb[int(w)])

    mixed_here: list[tuple] = []
    # column label per index j (for reporting mixed cells)
    col_label = {j: c for j, c in enumerate(cols)}
    for i, w in enumerate(workloads):
        sources = ([(0, with_inmem)] if with_inmem is not None else []) + [
            (cols.index(_pool_label(p)), sub[sub["pool_buffers"] == p]) for p in pools]
        for j, frame in sources:
            runs = frame[frame["workload"] == w]
            if runs.empty:
                ax.text(j, i, "·", ha="center", va="center",
                        fontsize=7, color=style.GREY)
                continue
            n_ok, reps = int(runs["ok"].sum()), len(runs)
            reason, mixed = _reason(runs)
            if n_ok == reps:
                facecolor = _blend(style.GREEN, 1.0)
                label = f"{n_ok} ok"
                txtcol = "black"
            elif n_ok >= reps / 2:
                facecolor = _blend(style.GREEN, 0.55)
                token = REASON_STYLE.get(reason, ("err", style.GREY))[0]
                label = f"{n_ok} ok / {reps - n_ok} {token}"
                txtcol = "black"
            else:
                token, base = REASON_STYLE.get(reason, ("err", style.GREY))
                facecolor = _blend(base, (reps - n_ok) / reps)
                label = f"{n_ok} ok / {reps - n_ok} {token}"
                lum = 0.299 * facecolor[0] + 0.587 * facecolor[1] + 0.114 * facecolor[2]
                txtcol = "white" if lum < 0.55 else "black"
            if mixed:
                mixed_here.append((cap, int(w), col_label[j]))
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=facecolor,
                                       edgecolor="white", linewidth=0.6, zorder=1))
            if mixed:
                ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor="none",
                                           edgecolor="black", hatch="///",
                                           linewidth=0.0, zorder=2))
            ax.text(j, i, label, ha="center", va="center", fontsize=5.9,
                    color=txtcol, zorder=3)

    ax.set_xlim(-0.5, len(cols) - 0.5)
    ax.set_ylim(-0.5, len(workloads) - 0.5)
    ax.set_xticks(range(len(cols)), cols, fontsize=7.5)
    ax.set_yticks(range(len(workloads)),
                  [f"$\\rho$ {rho_mb[int(w)] / cap:.2f}  (r{int(w)})" for w in workloads],
                  fontsize=7.5)
    ax.set_title(f"cap {cap} MB")
    ax.grid(False)
    ax.tick_params(length=0)
    ax.set_aspect("auto")
    for spine in ax.spines.values():
        spine.set_visible(False)
    return mixed_here


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)

    # Feasibility-grid cells only: drop checksum-verification (chk) and reference
    # (ref) reruns, which exit with the rc141 teardown status and would inject a
    # spurious pool-abort into an otherwise clean cell.
    grid_df = df[~df["cell"].str.contains("chk|ref", case=False, na=False)]

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 6.8))
    all_mixed: list[tuple] = []
    for col, cap in enumerate(CAPS):
        heap = grid_df[(grid_df["campaign"] == "L") & (grid_df["arm"] == "heap")
                       & (grid_df["cap_mb"] == cap)]
        inmem = grid_df[(grid_df["campaign"] == "L") & (grid_df["arm"] == "inmem")
                        & (grid_df["cap_mb"] == cap)]
        bounded = grid_df[(grid_df["campaign"] == "W") & (grid_df["arm"] == "bounded")
                          & (grid_df["cap_mb"] == cap)]
        all_mixed += _panel(axes[0, col], heap, rho_mb, cap, inmem)
        all_mixed += _panel(axes[1, col], bounded, rho_mb, cap, None)

    axes[0, 0].set_ylabel("unbounded (heap)\nengine default, 4096 B buffers\n$\\rho$ at cap  (workload id)",
                          fontsize=8.0)
    axes[1, 0].set_ylabel("bounded (pooled)\ncorrected flag, 8192 B buffers\n$\\rho$ at cap  (workload id)",
                          fontsize=8.0)
    for ax in axes[1]:
        ax.set_xlabel("pool size (buffers)", fontsize=9)

    # Knife-edge sweep annotation: 512 MB heap, r75, across pools.
    # The plain L_ batch reads 10/10 (16k) -> 6/10 (32k) -> 0/10 (64k);
    # merged with the _b32_ re-run the panel shows 20/20 -> 11/20 -> 0/20.
    ax512 = axes[0, 0]
    # find the r75 row index and pool columns
    heap512 = grid_df[(grid_df["campaign"] == "L") & (grid_df["arm"] == "heap")
                      & (grid_df["cap_mb"] == 512)]
    pools512 = sorted(p for p in heap512["pool_buffers"].dropna().unique() if p > 0)
    workloads512 = sorted(heap512["workload"].dropna().unique(), key=lambda w: rho_mb[int(w)])
    ws512 = [int(w) for w in workloads512]
    cols512 = (["in-mem"] if len(grid_df[(grid_df["campaign"] == "L")
               & (grid_df["arm"] == "inmem") & (grid_df["cap_mb"] == 512)]) else []) \
        + [_pool_label(p) for p in pools512]
    if 75 in ws512 and "32k" in cols512:
        row75 = ws512.index(75)
        col32 = cols512.index("32k")
        ax512.annotate("r75 knife edge: 10/10 @16k\n"
                       "$\\to$ 6/10 @32k $\\to$ 0/10 @64k",
                       xy=(col32, row75 - 0.35),
                       xytext=(col32 - 0.3, row75 + 1.15), fontsize=6.2, ha="center",
                       va="bottom", color="#222222",
                       bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=style.GREY, lw=0.7),
                       arrowprops=dict(arrowstyle="->", lw=0.8, color="#555555"))

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=_blend(style.GREEN, 1.0), edgecolor="white", label="all reps complete"),
        Patch(facecolor=_blend(style.RED, 0.85), edgecolor="white",
              label="fails: OOM-kill (rc 137, pool+overhead $>$ cap)"),
        Patch(facecolor=_blend(style.ORANGE, 0.85), edgecolor="white",
              label="fails: pool-abort (rc 141, pool too small)"),
        Patch(facecolor="white", edgecolor="black", hatch="///",
              label="cell mixes OOM+abort reps"),
        Line2D([], [], marker="$\\cdot$", ls="", color=style.GREY, ms=11,
               label="not in probed grid"),
    ]
    fig.legend(handles=legend_handles, loc="outside lower center", ncol=3, fontsize=7.8,
               frameon=False)

    fig.suptitle("Operating windows: completion and failure reason across cap × pool × workload",
                 fontsize=11, fontweight="bold")
    print(f"heatmap: mixed rc137+rc141 cells = {len(all_mixed)}")
    for m in all_mixed:
        print("   mixed:", m)
    style.caption(fig, extra="cell = N ok / M reason; colour = modal failure reason; hatch = cell mixes OOM+abort reps; dot = not run")
    style.save(fig, "fig_heatmap")


if __name__ == "__main__":
    main()
