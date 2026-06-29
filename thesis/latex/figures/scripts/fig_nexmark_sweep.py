"""fig:nexmark-sweep — completion across Nexmark scenarios and caps.

The full honest picture: in-memory fits the small/medium scenarios (A, B) and
fails only at the beyond-memory point (C @ 1024 MB), which is exactly where
out-of-core wins. Elsewhere out-of-core is unnecessary and can over-spill.
Source: NX_* runs (q3, q7), 8 reps per cell.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

import data
import style

POINTS = [("A", "512", "A · 512 MB"), ("B", "512", "B · 512 MB"),
          ("B", "1024", "B · 1024 MB"), ("C", "1024", "C · 1024 MB")]
CFG = [("inm", "in-memory, capped", style.ORANGE),
       ("B2", "out-of-core, pooled", style.BLUE),
       ("U8", "out-of-core, heap", style.SKY)]
QUERIES = [("q3", "Nexmark q3"), ("q7", "Nexmark q7")]


def main() -> None:
    style.use_style()
    df = data.load_runs()
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 7.0), sharey=True)
    for qi, (q, qlabel) in enumerate(QUERIES):
        ax = axes[qi]
        x = np.arange(len(POINTS))
        width = 0.27
        for k, (cfg, label, color) in enumerate(CFG):
            frac, labels = [], []
            for scen, cap, _ in POINTS:
                runs = df[df["cell"] == f"NX_{q}_{scen}_c{cap}_{cfg}"]
                if len(runs):
                    nok, rp = int(runs["ok"].sum()), len(runs)
                    frac.append(nok / rp); labels.append(f"{nok}/{rp}")
                else:
                    frac.append(0.0); labels.append("")
            bars = ax.bar(x + (k - 1) * width, frac, width, color=color,
                          edgecolor="black", linewidth=0.3,
                          label=label if qi == 0 else None)
            for rect, text in zip(bars, labels):
                if text:
                    ax.annotate(text, (rect.get_x() + rect.get_width() / 2,
                                       rect.get_height() + 0.02),
                                ha="center", va="bottom", fontsize=7)
        ax.axvline(2.5, color=style.GREY, lw=1.0, ls="--")
        ax.text(2.52, 1.13, "beyond memory →", fontsize=7.5, color="#555555", va="top")
        ax.set_xticks(x, [p[2] for p in POINTS], fontsize=8)
        ax.set_title(qlabel)
        ax.set_ylim(0, 1.18)
        if qi == 0:
            ax.set_ylabel("completion fraction (completed / attempted)")
    axes[0].legend(loc="lower left", fontsize=8)

    fig.suptitle("Nexmark across scenarios and caps — out-of-core matters only beyond memory",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="bar labels = completed/attempted · in-memory fits A and B, fails only at C · 1024 MB")
    style.save(fig, "fig_nexmark_sweep")


if __name__ == "__main__":
    main()
