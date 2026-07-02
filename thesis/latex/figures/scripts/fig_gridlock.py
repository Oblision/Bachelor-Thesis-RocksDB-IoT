"""fig:gridlock — anatomy of the allocation deadlock (B3 cells).

Panel (a): cgroup memory time series (0.2 s sampler) of one failing
B3_bp_c1024_p32k_r200 rep: anon working set climbs, the pre-allocated pool
(250 MB) pins, and the run aborts (rc 141) once no worker can allocate.
Panel (b): pool occupancy at its peak and at death for all eight reps —
the pool is full (249/250 MB) and stays pinned by emitted-but-unprocessed
window state, the gridlock signature. The per-event stall log (BPGAUGE,
118/138 stalls with no buffer returned for two seconds) lives in the run
logs on the measurement host; its aggregates are quoted in the text.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

import data
import style

CELL = "B3_bp_c1024_p32k_r200"
POOL_MB = 250
CAP_MB = 1024
REP = 3  # representative failing rep


def _load_trace(rep: int) -> np.ndarray:
    rows = []
    path = data.TRACES / f"{CELL}_r{rep}.trace"
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 6:
            rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
    arr = np.array(rows)
    arr[:, 0] -= arr[0, 0]
    return arr


def main() -> None:
    style.use_style()
    df = data.load_runs()
    runs = df[(df["cell"] == CELL)].sort_values("label")
    elapsed = runs["elapsed_s"].median()
    print(f"{CELL}: {int(runs['ok'].sum())}/{len(runs)} ok, modal rc",
          runs[~runs["ok"]]["rc"].mode().iloc[0])

    fig, axes = plt.subplots(2, 1, figsize=(7.2, 6.2), height_ratios=[1.5, 1])

    ax = axes[0]
    trace = _load_trace(REP)
    ax.plot(trace[:, 0], trace[:, 2], color=style.BLUE, lw=1.6,
            label="anon working set (cgroup)")
    ax.plot(trace[:, 0], trace[:, 1], color=style.GREY, lw=1.0, ls=":",
            label="memory.current (incl. cache)")
    # Shade the pool floor: the 250 MB pool is only the operator buffer arena,
    # the anonymous-memory floor, NOT the whole footprint. Everything above the
    # line is windowed state held on the heap outside the pool.
    ax.axhspan(0, POOL_MB, color=style.ORANGE, alpha=0.06, zorder=0)
    ax.axhline(POOL_MB, color=style.ORANGE, lw=1.2, ls="--")
    ax.text(0.3, POOL_MB - 45, "pool allocation (250 MB, pinned)",
            fontsize=8, color=style.ORANGE, va="center")
    # Label the band between the pool line and the anon curve in place: that gap
    # is windowed state (emitted-but-unprocessed slices) on the heap, outside the
    # pool. The pool is the floor; the climb above it is state, not pool.
    ax.text(2.6, 360, "everything above the pool line is\n"
            "windowed state on the heap, outside\n"
            "the 250 MB pool (only the floor)",
            fontsize=7.0, ha="left", va="center", color=style.BLUE,
            bbox=dict(boxstyle="round,pad=0.3", fc="white",
                      ec=style.BLUE, alpha=0.92, lw=0.7))
    t_end = trace[-1, 0]
    ax.axvline(t_end, color=style.RED, lw=1.2, ls="--")
    ax.annotate("allocation abort\n(rc 141)", xy=(t_end, trace[-1, 2]),
                xytext=(t_end - 7.5, trace[:, 2].max() * 0.62), fontsize=8.5,
                ha="left", arrowprops=dict(arrowstyle="->", lw=0.9))
    ax.set_xlabel("run time (s)")
    ax.set_ylabel("memory (MB)")
    ax.set_title(f"(a) death timeline, one rep ({CELL.replace('_', chr(92) + '_')})"
                 if False else "(a) death timeline of one failing rep")
    ax.legend(fontsize=8, loc="upper left")

    ax = axes[1]
    reps = np.arange(1, len(runs) + 1)
    width = 0.38
    ax.bar(reps - width / 2, runs["pool_used_peak_mb"], width,
           color=style.BLUE, label="pool used, peak")
    ax.bar(reps + width / 2, runs["pool_used_final_mb"], width,
           color=style.ORANGE, label="pool used, at death")
    ax.axhline(POOL_MB, color=style.RED, lw=1.2, ls="--")
    ax.text(0.55, POOL_MB + 7, "pool size", fontsize=8,
            color=style.RED, ha="left", va="bottom")
    ax.set_xticks(reps, [f"r{i}" for i in reps], fontsize=8)
    ax.set_xlabel("repetition (all 8 die)")
    ax.set_ylabel("pool occupancy (MB)")
    ax.set_ylim(0, POOL_MB * 1.30)
    ax.set_title("(b) the pool stays pinned")
    ax.legend(fontsize=7.5, loc="upper right", ncols=1)

    # Descriptive statement of the failure (replaces the old "role-blind" wording).
    axes[0].text(0.5, 1.10,
                 "allocation deadlock: emitted state pins the pool; the workers\n"
                 "that would drain it are all blocked allocating",
                 transform=axes[0].transAxes, ha="center", va="bottom",
                 fontsize=8.2, style="italic", color="#444444")

    fig.suptitle("Anatomy of the allocation deadlock (1024 MB / 32k pool / r200)",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="cgroup 0.2 s sampler · all 8 reps of the cell fail (rc 141)")
    style.save(fig, "fig_gridlock")


if __name__ == "__main__":
    main()
