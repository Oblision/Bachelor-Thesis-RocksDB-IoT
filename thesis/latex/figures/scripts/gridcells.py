"""Shared helpers for the operating-window figure family
(fig_heatmap, fig_geometry_law, fig_knife).

Failure-categorisation policy (mirrors the thesis accounting in
Sec. "Geometry, the Valley, and the Crash Cluster"):
  - rc 137 / 137c is the cgroup OOM kill; rc 134, the allocator abort under
    the cap, shares its memory signature (early death with memory pinned at
    the cap) and is counted WITH the pressure failures, not as corruption.
  - rc 141 is the pool-exhaustion abort (pool too small for the demand).
  - rc 139 / 11 is the corruption segfault (restore path), counted
    separately as "crash"; it appears only inside cells that fail regardless.
"""
from __future__ import annotations

from matplotlib.colors import to_rgb

import style

CAPS = (512, 1024, 2048)

OOM_RCS = {"137", "137c", "134"}
POOL_RCS = {"141"}
CRASH_RCS = {"139", "11"}

# category -> (display token, base colour for failing cells)
REASON_STYLE = {
    "OOM": ("OOM", style.RED),
    "POOL": ("abort", style.ORANGE),
    "CRASH": ("crash", style.PURPLE),
    "OTHER": ("err", style.GREY),
}


def classify_rc(rc: str) -> str:
    if rc in OOM_RCS:
        return "OOM"
    if rc in POOL_RCS:
        return "POOL"
    if rc in CRASH_RCS:
        return "CRASH"
    return "OTHER"


def blend(color, frac: float):
    """Blend `color` toward white; frac in [0,1], 1 = full colour."""
    r, g, b = to_rgb(color)
    t = 0.25 + 0.75 * frac  # keep a minimum tint so even 1/10 fails reads
    return (1 - t + t * r, 1 - t + t * g, 1 - t + t * b)


def pool_label(buffers: float) -> str:
    return f"{int(buffers / 1000)}k"


def grid_df(df):
    """Feasibility-grid rows only: drop checksum-verification (chk) and
    reference (ref) reruns, which exit with the rc141 teardown status and
    would inject a spurious pool-abort into an otherwise clean cell."""
    return df[~df["cell"].str.contains("chk|ref", case=False, na=False)]


def arm_frames(g, cap: int):
    """(heap, in-mem, bounded) frames of one cap panel."""
    heap = g[(g["campaign"] == "L") & (g["arm"] == "heap") & (g["cap_mb"] == cap)]
    inm = g[(g["campaign"] == "L") & (g["arm"] == "inmem") & (g["cap_mb"] == cap)]
    bnd = g[(g["campaign"] == "W") & (g["arm"] == "bounded") & (g["cap_mb"] == cap)]
    return heap, inm, bnd


def cols_workloads(sub, with_inmem, rho_mb):
    pools = sorted(p for p in sub["pool_buffers"].dropna().unique() if p > 0)
    cols = ((["in-mem"] if with_inmem is not None else [])
            + [pool_label(p) for p in pools])
    ws = sorted(sub["workload"].dropna().unique(), key=lambda w: rho_mb[int(w)])
    return pools, cols, ws


def iter_cells(sub, with_inmem, rho_mb):
    """Yield (row, col, workload, runs) over one cap panel, heavy rows on top."""
    pools, cols, ws = cols_workloads(sub, with_inmem, rho_mb)
    for i, w in enumerate(ws):
        sources = ([(0, with_inmem)] if with_inmem is not None else []) + [
            (cols.index(pool_label(p)), sub[sub["pool_buffers"] == p])
            for p in pools]
        for j, frame in sources:
            yield i, j, w, frame[frame["workload"] == w]


def dress_axes(ax, cols, ws, rho_mb, cap: int) -> None:
    ax.set_xlim(-0.5, len(cols) - 0.5)
    ax.set_ylim(-0.5, len(ws) - 0.5)
    ax.set_xticks(range(len(cols)), cols, fontsize=7.5)
    ax.set_yticks(range(len(ws)),
                  [f"$\\rho$ {rho_mb[int(w)] / cap:.2f}  (r{int(w)})" for w in ws],
                  fontsize=7.5)
    ax.set_title(f"cap {cap} MB")
    ax.grid(False)
    ax.tick_params(length=0)
    ax.set_aspect("auto")
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_untested_dot(ax, j: int, i: int) -> None:
    ax.plot(j, i, marker="o", ms=3.4, mfc=style.GREY, mec=style.GREY,
            ls="", zorder=1)
