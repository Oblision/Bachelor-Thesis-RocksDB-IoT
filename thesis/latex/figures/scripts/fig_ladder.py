"""fig:ladder — the attribution ladder at the decisive cell (1024 MB / 32k / r150).

Five arms, counts straight from the result TSVs:
  default (4096, heap)      L_c1024_p32000_ooc_r150   0/10
  granularity only (8128)   U_c1024_p32k_r150         3/8
  pooling, no levers        N_B0_c1024_p32k_r150      0/8
  pooling + reserve         N_B1_c1024_p32k_r150      5/8
  pooling + reserve + paced N_B2_c1024_p32k_r150      7/8
Annotated p: levers within pooling, 0/8 -> 7/8, Fisher one-sided = 7.0e-4 —
both arms are in the figure and the value reproduces from the TSV counts.
(The prose's 8.1e-6 is pooled-lever arms 12/16 vs unpooled levers 0/16,
a comparison whose 0/16 arm is not part of this five-step ladder.)
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

import data
import style

STEPS = [
    ("baseline\n(4096, heap)", "L_c1024_p32000_ooc_r150", style.GREY),
    ("granularity only\n(8128, heap)", "U_c1024_p32k_r150", style.ORANGE),
    ("pooling alone\n(no levers)", "N_B0_c1024_p32k_r150", style.SKY),
    ("pooling\n+ reserve", "N_B1_c1024_p32k_r150", style.BLUE),
    ("pooling + reserve\n+ paced restore", "N_B2_c1024_p32k_r150", "#024B73"),
]


def main() -> None:
    style.use_style()
    df = data.load_runs()

    counts = []
    for _, cell, _ in STEPS:
        runs = df[df["cell"] == cell]
        counts.append((int(runs["ok"].sum()), len(runs)))
    print("ladder:", counts)
    p_levers = data.fisher_one_sided(counts[4][0], counts[4][1],
                                     counts[2][0], counts[2][1])
    print(f"levers within pooling (7/8 vs 0/8): Fisher one-sided p = {p_levers:.1e}")

    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    x = np.arange(len(STEPS))
    fracs = [o / n for o, n in counts]
    bars = ax.bar(x, fracs, 0.62, color=[s[2] for s in STEPS])
    for rect, (o, n) in zip(bars, counts):
        ax.annotate(f"{o}/{n}", (rect.get_x() + rect.get_width() / 2,
                                 rect.get_height() + 0.025),
                    ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x, [s[0] for s in STEPS], fontsize=8.5)
    ax.set_ylim(0, 1.06)
    ax.set_ylabel("completion fraction")
    ax.text(0.02, 0.96,
            "decisive cell: 1024 MB / 32k pool / r150\n"
            "levers within pooling (0/8 $\\to$ 7/8): Fisher $p = 7.0\\times10^{-4}$",
            transform=ax.transAxes, va="top", ha="left", fontsize=8.5,
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=style.GREY, lw=0.8))
    fig.suptitle("The one-flag unlock, decomposed at the decisive cell "
                 "(1024 MB cap / 32k pool / r150)", fontsize=11, fontweight="bold")
    style.caption(fig, extra="bar labels = completed/attempted · Fisher's exact test, one-sided")
    style.save(fig, "fig_ladder")


if __name__ == "__main__":
    main()
