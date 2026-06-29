"""fig:regimes — the three-regime feasibility law (schematic).

x: peak pool demand relative to the pool size. y: whether the memory cap
constrains ingest (tight cap -> allocation backpressure paces ingest to the
drain rate). The same-workload contrast pair is annotated: r200 completes
23/24 at cap 512 MB / 16k pool (regime 2) and dies 0/8 at cap 1024 MB with
twice the pool (regime 3) — verified against the result TSVs (N_B0/B1/B2 =
7+8+8 of 24; W_c1024_p32k_r200 = 0/8, rc 141).

Layout uses fixed y-slots inside each region box (title / body / marker line)
so no element can overlap another.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

import style


def _box(ax, x0, y0, w, h, color) -> None:
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0.012",
                                fc=color, ec="black", lw=1.1))


def main() -> None:
    style.use_style()
    fig, ax = plt.subplots(figsize=(8.2, 5.4))
    ax.set_xlim(-0.02, 2.08)
    ax.set_ylim(-0.10, 1.06)
    ax.axis("off")

    # ---- Regime 1 (left, full height) ----
    _box(ax, 0.02, 0.0, 0.92, 1.0, "#d7ecd9")
    ax.text(0.48, 0.93, "Regime 1 — fits", ha="center", va="top",
            fontsize=10.5, fontweight="bold")
    ax.text(0.48, 0.78, "peak pool demand $\\leq$ pool", ha="center", va="top",
            fontsize=9)
    ax.text(0.48, 0.52,
            "completes at full speed\n\nexample: r500/r700 at 2048 MB\n"
            "10/10, zero slices spilled\nCPU 89–97%", ha="center", va="center",
            fontsize=8.8)

    # ---- Regime 2 (top right) ----
    _box(ax, 1.02, 0.52, 1.0, 0.48, "#fdf3d0")
    ax.text(1.52, 0.955, "Regime 2 — throttled", ha="center", va="top",
            fontsize=10.5, fontweight="bold")
    ax.text(1.52, 0.86,
            "tight cap $\\Rightarrow$ blocked allocations pace ingest\n"
            "to the spill-drain rate; completes slower (CPU $\\approx$ 63%)",
            ha="center", va="top", fontsize=8.6)
    ax.scatter([1.13], [0.60], c=style.GREEN, marker="*", s=190,
               edgecolors="black", linewidths=0.7, zorder=5)
    ax.text(1.20, 0.60, "r200, cap 512 MB / 16k pool: 23/24 complete",
            ha="left", va="center", fontsize=8.2)

    # ---- Regime 3 (bottom right) ----
    _box(ax, 1.02, 0.0, 1.0, 0.44, "#f6d7cd")
    ax.text(1.52, 0.395, "Regime 3 — gridlock", ha="center", va="top",
            fontsize=10.5, fontweight="bold")
    ax.text(1.52, 0.30,
            "unconstrained ingest pins the pool; emitted state\n"
            "needs workers, workers block on allocation $\\Rightarrow$ deadlock",
            ha="center", va="top", fontsize=8.6)
    ax.scatter([1.13], [0.075], c=style.RED, marker="*", s=190,
               edgecolors="black", linewidths=0.7, zorder=5)
    ax.text(1.20, 0.075, "same workload, cap 1024 MB, 2$\\times$ pool: 0/8 (rc 141)",
            ha="left", va="center", fontsize=8.2)

    # ---- axes ----
    ax.annotate("", xy=(2.06, -0.045), xytext=(0.02, -0.045),
                arrowprops=dict(arrowstyle="->", lw=1.1))
    ax.text(1.03, -0.075, "peak pool demand relative to pool size",
            ha="center", va="top", fontsize=9)
    ax.text(0.975, -0.038, "demand = pool", ha="center", va="bottom",
            fontsize=7.6, style="italic", color="#555555")
    ax.annotate("", xy=(-0.005, 1.04), xytext=(-0.005, -0.045),
                arrowprops=dict(arrowstyle="->", lw=1.1))
    ax.text(-0.045, 0.50, "memory cap constrains ingest $\\rightarrow$",
            ha="center", va="center", fontsize=9, rotation=90)

    fig.suptitle("The three-regime feasibility law of the pooled architecture",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="schematic; the two example cells are verified against the result TSVs")
    style.save(fig, "fig_regimes")


if __name__ == "__main__":
    main()
