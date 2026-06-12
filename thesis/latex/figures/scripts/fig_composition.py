"""fig:composition — composition of the state footprint.

Panel (a): hash-map state composition at a 26 M-entry working set, from the
HMGAUGE per-map census (headers 51%, payload 34%, bucket directory 14%,
page slack <1% — DATA-AUDIT.md RQ5; the raw gauge lines live in the run logs
on the measurement host, the audited shares are reproduced here).
Panel (b): on-disk spill footprint vs state pressure, from the result TSVs.
"""
from __future__ import annotations

import matplotlib.pyplot as plt

import data
import style

COMPOSITION = [  # audited HMGAUGE shares at 26 M entries
    ("entry headers\n(8 B unused vtable ptr\n= 20% of state)", 51, style.ORANGE),
    ("payload\n(keys + values)", 34, style.BLUE),
    ("bucket directory", 14, style.SKY),
    ("page slack", 1, style.GREY),
]


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)

    fig, axes = plt.subplots(1, 2, figsize=(8.8, 3.7), width_ratios=[1, 1.5])

    ax = axes[0]
    bottom = 0.0
    for label, share, color in COMPOSITION:
        ax.bar([0], [share], 0.45, bottom=bottom, color=color)
        ax.annotate(f"{label}: {share}%", xy=(0.26, bottom + share / 2),
                    fontsize=8.5, va="center", ha="left")
        bottom += share
    ax.set_xlim(-0.4, 1.6)
    ax.set_xticks([0], ["26 M entries"])
    ax.set_ylabel("share of state bytes (%)")
    ax.set_title("(a) hash-map state composition")
    ax.grid(False)

    ax = axes[1]
    spill = df[df["ok"] & (df["slices"] > 0) & df["workload"].notna()
               & df["cap_mb"].notna() & (df["spill_dir_mb"] > 0)
               & df["campaign"].isin(["L", "W"])].copy()
    spill["rho"] = spill.apply(
        lambda r: rho_mb[int(r["workload"])] / r["cap_mb"], axis=1)
    for arm, color, marker in [("heap", style.ORANGE, "o"), ("bounded", style.BLUE, "s")]:
        cells = spill[spill["arm"] == arm].groupby("cell").agg(
            rho=("rho", "first"), disk=("spill_dir_mb", "median"))
        ax.scatter(cells["rho"], cells["disk"], c=color, marker=marker, s=42,
                   alpha=0.8, edgecolors="black", linewidths=0.4,
                   label=f"{arm} architecture")
    ax.set_xlabel("state pressure $\\rho$")
    ax.set_ylabel("spill directory size (MB, lz4)")
    ax.set_title("(b) on-disk footprint of completing spilling cells")
    ax.legend(fontsize=8, loc="upper left")

    style.save(fig, "fig_composition")


if __name__ == "__main__":
    main()
