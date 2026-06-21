"""fig:cpu-io — processor and disk behavior across state pressure.

Per completing cell of the L_/W_/I8_ grids plus failing throttled/bounded cells'
ok reps. CPU utilization = cgroup cpu usage / (elapsed x 2 cores, the pinned
budget). Disk = cgroup io.stat bytes read/written. One point per cell (medians).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import style

CORES = 2.0


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)

    ok = df[df["ok"] & df["workload"].notna() & df["cap_mb"].notna()
            & (df["elapsed_s"] > 0) & (df["cpu_usec"] > 0)
            & df["campaign"].isin(["L", "W", "I8"])].copy()
    ok["rho"] = ok.apply(lambda r: rho_mb[int(r["workload"])] / r["cap_mb"], axis=1)
    ok["cpu_util"] = ok["cpu_usec"] / (ok["elapsed_s"] * 1e6 * CORES) * 100
    ok["spilling"] = (ok["arm"] != "inmem") & (ok["slices"] > 0)

    groups = [
        ("in-memory", ok["arm"] == "inmem", style.GREEN, "o"),
        ("out-of-core, no spill", (ok["arm"] != "inmem") & (ok["slices"] == 0),
         style.SKY, "s"),
        ("out-of-core, spilling", (ok["arm"] != "inmem") & (ok["slices"] > 0),
         style.BLUE, "D"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.8))
    ax = axes[0]
    for label, mask, color, marker in groups:
        cells = ok[mask].groupby("cell").agg(
            rho=("rho", "first"), cpu=("cpu_util", "median"))
        ax.scatter(cells["rho"], cells["cpu"], c=color, marker=marker, s=42,
                   alpha=0.8, edgecolors="black", linewidths=0.4, label=label)
    ax.set_xlabel("state pressure $\\rho$")
    ax.set_ylabel("CPU utilization (%, 2 pinned cores)")
    ax.set_ylim(0, 105)
    ax.set_title("(a) processor utilization")
    ax.legend(fontsize=8, loc="lower left")

    ax = axes[1]
    spill = ok[(ok["arm"] != "inmem") & (ok["slices"] > 0)].groupby("cell").agg(
        rho=("rho", "first"), r=("io_r_b", "median"), w=("io_w_b", "median"))
    ax.scatter(spill["rho"], spill["w"] / 2**20, c=style.BLUE, marker="D", s=42,
               alpha=0.8, edgecolors="black", linewidths=0.4, label="written")
    ax.scatter(spill["rho"], spill["r"] / 2**20, c=style.ORANGE, marker="o", s=42,
               alpha=0.8, edgecolors="black", linewidths=0.4, label="read")
    ax.set_xlabel("state pressure $\\rho$")
    ax.set_ylabel("disk volume (MB, spilling cells)")
    ax.set_title("(b) disk traffic")
    ax.legend(fontsize=8, loc="upper left")

    fig.suptitle("Processor and disk behaviour across state pressure",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="each point = one cell (median of its completing reps)")
    style.save(fig, "fig_cpu_io")


if __name__ == "__main__":
    main()
