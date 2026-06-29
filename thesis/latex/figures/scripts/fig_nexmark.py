"""fig:nexmark — realism transfer at the beyond-memory coordinate.

Scenario C at the 1024 MB cap is the cell where the in-memory engine cannot fit
(0/8) on a realistic Nexmark bid stream. Completion of the three configurations,
with the out-of-core variants' genuine device I/O annotated. Source: NX_* runs.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

import data
import style

CONFIGS = [
    ("inm", "in-memory, capped", style.ORANGE),
    ("B2", "out-of-core, pooled", style.BLUE),
    ("U8", "out-of-core, heap", style.SKY),
]
QUERIES = [("q3", "Nexmark q3"), ("q7", "Nexmark q7")]


def _cell(df, query: str, cfg: str):
    return df[df["cell"] == f"NX_{query}_C_c1024_{cfg}"]


def main() -> None:
    style.use_style()
    df = data.load_runs()

    fig, ax = plt.subplots(figsize=(8.2, 5.5))
    x = np.arange(len(QUERIES))
    width = 0.26

    for i, (cfg, label, color) in enumerate(CONFIGS):
        heights, texts = [], []
        for q, _ in QUERIES:
            runs = _cell(df, q, cfg)
            ok, reps = int(runs["ok"].sum()), len(runs)
            heights.append(ok / reps if reps else 0.0)
            texts.append(f"{ok}/{reps}")
        bars = ax.bar(x + (i - 1) * width, heights, width, color=color,
                      edgecolor="black", linewidth=0.4, label=label)
        for bar, text in zip(bars, texts):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                    text, ha="center", va="bottom", fontsize=9, fontweight="bold")

    for xi, (q, _) in enumerate(QUERIES):
        b, m = _cell(df, q, "B2"), _cell(df, q, "inm")
        p = data.fisher_one_sided(int(b["ok"].sum()), len(b),
                                  int(m["ok"].sum()), len(m))
        ax.text(xi, 1.11, f"pooled vs in-mem: $p = {p:.1e}$",
                ha="center", fontsize=8.5)

    ax.set_xticks(x, [lbl for _, lbl in QUERIES])
    ax.set_ylabel("completion fraction")
    ax.set_ylim(0, 1.20)
    ax.set_title("Nexmark beyond memory — scenario C, 1024 MB cap "
                 "(in-memory cannot fit the state)")
    ax.legend(loc="center", bbox_to_anchor=(0.5, 0.82), fontsize=8.5, framealpha=0.95)

    lines = ["out-of-core does genuine device I/O (pooled arm):"]
    for q, _ in QUERIES:
        b = _cell(df, q, "B2")
        spill = b["spill_dir_mb"].median()
        restored = b["io_r_b"].median() / 2**20
        lines.append(f"   {q}: {spill:.0f} MB spilled · {restored:.0f} MB restored from SD")
    ax.text(0.5, 0.18, "\n".join(lines), transform=ax.transAxes, ha="center",
            va="center", fontsize=8.0,
            bbox=dict(boxstyle="round,pad=0.4", fc="#f1f3f4", ec="#bdc1c6"))

    style.caption(fig, extra="Nexmark q3/q7 bid stream, scenario C (beyond memory) · Fisher's exact test, one-sided")
    style.save(fig, "fig_nexmark")


if __name__ == "__main__":
    main()
