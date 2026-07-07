"""fig:composition — composition of the state footprint.

Panel (a): hash-map state composition at a 26 M-entry working set, from the
HMGAUGE per-map census (headers 51%, payload 34%, bucket directory 14%,
page slack <1% — DATA-AUDIT.md RQ5; the raw gauge lines live in the run logs
on the measurement host, the audited shares are reproduced here).
Panel (b): on-disk spill footprint vs state pressure, from the result TSVs.
16/8 RocksDB configuration only: the 32/32 backend-size twin runs (*_b32_*)
are excluded, matching the rest of the figure family; that contrast is
analysed separately in Section~sec:rb32.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import style

COMPOSITION = [  # audited HMGAUGE shares at 26 M entries
    ("entry headers", 51, style.ORANGE),
    ("payload\n(keys + values)", 34, style.BLUE),
    ("bucket directory", 14, style.SKY),
    ("page slack", 1, style.GREY),
]


def main() -> None:
    style.use_style()
    df = data.load_runs()
    df = df[~df["cell"].str.contains("b32")]  # 16/8 baseline only; see docstring
    rho_mb = data.rho_table(df)

    fig, axes = plt.subplots(1, 2, figsize=(8.8, 4.6), width_ratios=[1, 1.5])

    ax = axes[0]
    bottom = 0.0
    for label, share, color in COMPOSITION:
        ax.bar([0], [share], 0.45, bottom=bottom, color=color)
        ax.annotate(f"{label}: {share}%", xy=(0.26, bottom + share / 2),
                    fontsize=8.5, va="center", ha="left")
        bottom += share
    # The 51% is what headers ARE. The =~20% is a SEPARATE quantity: the state
    # that removing the unused 8-byte vtable pointer (of the 24-byte header)
    # would recover. Annotate it distinctly so the two numbers do not read as one.
    ax.annotate("$\\approx$20% recoverable\n(unused header pointer)",
                xy=(0.26, 13), fontsize=7.1, va="center", ha="left",
                style="italic", color="#7a4a00")
    ax.set_xlim(-0.4, 1.6)
    ax.set_xticks([0], ["26 M entries"])
    ax.set_ylabel("share of state bytes (%)")
    ax.set_title("(a) hash-map state composition")
    # Provenance so the figure is self-describing: the 26 M-entry census is the
    # r150 workload (~996 maps, ~25.9 M entries) from the HMGAUGE per-map dump.
    ax.text(0.5, -0.14, "census: s736 workload, $\\approx$996 maps / $\\approx$25.9 M entries",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.0,
            color="#555555", style="italic")
    ax.grid(False)

    ax = axes[1]
    spill = df[df["ok"] & (df["slices"] > 0) & df["workload"].notna()
               & df["cap_mb"].notna() & (df["spill_dir_mb"] > 0)
               & df["campaign"].isin(["L", "W"])].copy()
    spill["rho"] = spill.apply(
        lambda r: rho_mb[int(r["workload"])] / r["cap_mb"], axis=1)
    # Draw bounded first, heap second on top: where both complete at low rho the
    # on-disk volume coincides (it is a workload/compression property, not an
    # architecture one), so heap's few orange points would otherwise hide under
    # the blue squares. A white edge makes the overlapping heap markers legible.
    specs = [("bounded", style.BLUE, "s", 3, 0.6, "black", 42),
             ("heap", style.ORANGE, "o", 5, 1.0, "white", 58)]
    for arm, color, marker, z, lw, ec, s in specs:
        cells = spill[spill["arm"] == arm].groupby("cell").agg(
            rho=("rho", "first"), disk=("spill_dir_mb", "median"))
        ax.scatter(cells["rho"], cells["disk"], c=color, marker=marker, s=s,
                   alpha=0.9, edgecolors=ec, linewidths=lw, zorder=z)
    ax.set_xlabel("state pressure $\\rho$")
    ax.set_ylabel("spill directory size (MB, lz4)")
    ax.set_title("(b) on-disk footprint of completing spilling cells")
    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.ORANGE, mec="white", mew=1.0,
               label="heap architecture (drawn on top)"),
        Line2D([], [], marker="s", ls="", mfc=style.BLUE, mec="black", mew=0.6,
               label="bounded architecture"),
    ]
    ax.legend(handles=handles, fontsize=8, loc="upper left")

    fig.suptitle("Composition and on-disk footprint of the spilled state",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="(a) HMGAUGE per-map census at 26 M entries; (b) one point per completing spilling cell")
    style.save(fig, "fig_composition")


if __name__ == "__main__":
    main()
