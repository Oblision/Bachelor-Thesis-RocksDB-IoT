"""fig:cost — the cost of out-of-core processing at matched I/O granularity.

Three matched coordinates (~8 K buffers in every variant, same pool per arm):
(512/16k/r075), (1024/32k/r100), (2048/64k/r200). Arms: in-memory baseline (I8),
heap out-of-core (U8 control), bounded out-of-core (W). Medians over ok reps.
Panels: throughput delta vs in-memory, p99 ratio vs in-memory, steady resident
anon footprint (the -33% bounded advantage at 2048/r200).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

import data
import style

COORDS = [  # (display, in-mem cell, heap-ooc cell, bounded cell)
    ("512 MB\nr075", "I8_c512_p16k_r075", "U_c512_p16k_r075", "W_c512_p16k_r075"),
    ("1024 MB\nr100", "I8_c1024_p32k_r100", "U_c1024_p32k_r100", "W_c1024_p32k_r100"),
    ("2048 MB\nr200", "I8_c2048_p64k_r200", "U_c2048_p64k_r200", "W_c2048_p64k_r200"),
]
ARMS = [("heap OOC", style.ORANGE, 2), ("bounded OOC", style.BLUE, 3)]


def _cell_means(df, cell: str) -> dict[str, float]:
    """Means over ok reps — the aggregation used by the audited cost table."""
    ok = df[(df["cell"] == cell) & df["ok"]]
    return {"tps": ok["tps"].mean(), "p99": ok["lat_p99_us"].mean(),
            "steady": ok["steady_anon_mb"].mean()}


def main() -> None:
    style.use_style()
    df = data.load_runs()
    stats = [[_cell_means(df, c) for c in coord[1:]] for coord in COORDS]

    fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.5))
    x = np.arange(len(COORDS))
    width = 0.34

    ax = axes[0]
    for k, (label, color, idx) in enumerate(ARMS):
        delta = [100 * (s[idx - 1]["tps"] / s[0]["tps"] - 1) for s in stats]
        bars = ax.bar(x + (k - 0.5) * width, delta, width, color=color, label=label)
        for rect, value in zip(bars, delta):
            ax.annotate(f"{value:+.0f}%", (rect.get_x() + rect.get_width() / 2,
                                           rect.get_height() - 0.7 - 1.7 * k),
                        ha="center", va="top", fontsize=8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylim(-31, 0)
    ax.set_xticks(x, [c[0] for c in COORDS], fontsize=8.5)
    ax.set_ylabel("throughput vs in-memory (%)")
    ax.set_title("(a) throughput cost")
    ax.legend(fontsize=8, loc="lower right")

    ax = axes[1]
    for k, (label, color, idx) in enumerate(ARMS):
        ratio = [s[idx - 1]["p99"] / s[0]["p99"] for s in stats]
        bars = ax.bar(x + (k - 0.5) * width, ratio, width, color=color)
        for rect, value in zip(bars, ratio):
            ax.annotate(f"{value:.2f}x", (rect.get_x() + rect.get_width() / 2,
                                          rect.get_height() + 0.03 + 0.09 * k),
                        ha="center", va="bottom", fontsize=8)
    ax.axhline(1.0, color="black", lw=0.8, ls="--")
    ax.set_xticks(x, [c[0] for c in COORDS], fontsize=8.5)
    ax.set_ylabel("p99 task latency vs in-memory")
    ax.set_ylim(0, 2.45)
    ax.set_title("(b) tail-latency cost")

    ax = axes[2]
    width3 = 0.27
    for k, (label, color) in enumerate(
            [("in-memory", style.GREEN), ("heap OOC", style.ORANGE),
             ("bounded OOC", style.BLUE)]):
        vals = [s[k]["steady"] for s in stats]
        bars = ax.bar(x + (k - 1) * width3, vals, width3, color=color, label=label)
        for rect, value in zip(bars, vals):
            ax.annotate(f"{value:.0f}", (rect.get_x() + rect.get_width() / 2,
                                         rect.get_height() + 12),
                        ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(x, [c[0] for c in COORDS], fontsize=8.5)
    ax.set_ylabel("steady resident anon (MB)")
    ax.set_ylim(0, 1640)
    ax.set_title("(c) resident footprint")
    ax.legend(fontsize=8, loc="upper left")

    fig.suptitle("The cost of out-of-core at matched I/O granularity (~8 K buffers in every arm)",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="three pool-matched coordinates · bars = means over completing reps")
    style.save(fig, "fig_cost")


if __name__ == "__main__":
    main()
