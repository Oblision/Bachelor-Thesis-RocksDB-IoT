"""fig:nexmark-cost — throughput cost of out-of-core on Nexmark.

Throughput of each out-of-core arm relative to the uncapped (no-cap) reference
for the same scenario, per query. In-order Nexmark pays the full spill/restore
round trip that the synthetic jitter masked, and the fixed cap-derived spill
threshold over-spills the fits cells (A/B) for no benefit. Source: NX_* runs;
median tps over completing reps vs the NX_*_cnone_unc reference.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

import data
import style

POINTS = [("A", "512", "A · 512 MB"), ("B", "512", "B · 512 MB"),
          ("B", "1024", "B · 1024 MB"), ("C", "1024", "C · 1024 MB")]
CFG = [("B2", "out-of-core, pooled", style.BLUE),
       ("U8", "out-of-core, heap", style.SKY)]
QUERIES = [("q3", "Nexmark q3"), ("q7", "Nexmark q7")]


def _tps(df, cell: str) -> float:
    runs = df[(df["cell"] == cell) & df["ok"]]
    return float(runs["tps"].median()) if len(runs) else float("nan")


def main() -> None:
    style.use_style()
    df = data.load_runs()
    fig, axes = plt.subplots(2, 1, figsize=(7.4, 6.0), sharey=True)
    for qi, (q, qlabel) in enumerate(QUERIES):
        ax = axes[qi]
        x = np.arange(len(POINTS))
        width = 0.36
        ref = {scen: _tps(df, f"NX_{q}_{scen}_cnone_unc") for scen, _, _ in POINTS}
        for k, (cfg, label, color) in enumerate(CFG):
            delta, labels = [], []
            for scen, cap, _ in POINTS:
                t, base = _tps(df, f"NX_{q}_{scen}_c{cap}_{cfg}"), ref.get(scen)
                if t == t and base and base == base:
                    d = 100 * (t / base - 1)
                    delta.append(d); labels.append(f"{d:+.0f}%")
                else:
                    delta.append(0.0); labels.append("n/a")
            bars = ax.bar(x + (k - 0.5) * width, delta, width, color=color,
                          edgecolor="black", linewidth=0.3,
                          label=label if qi == 0 else None)
            for rect, text in zip(bars, labels):
                h = rect.get_height()
                ax.annotate(text, (rect.get_x() + rect.get_width() / 2,
                                   h - 1.5 if h < 0 else h + 1.5),
                            ha="center", va="top" if h < 0 else "bottom", fontsize=7)
        ax.axhline(0, color="black", lw=0.8)
        ax.set_xticks(x, [p[2] for p in POINTS], fontsize=8)
        ax.set_title(qlabel)
        ax.set_ylim(-72, 8)
        if qi == 0:
            ax.set_ylabel("throughput vs uncapped reference (%)")
    axes[0].legend(loc="lower left", fontsize=8)

    fig.suptitle("Nexmark throughput cost: in-order streams pay the full spill/restore round trip",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="median tps over completing reps vs the uncapped reference · the fixed cap/10 threshold over-spills the fits cells")
    style.save(fig, "fig_nexmark_cost")


if __name__ == "__main__":
    main()
