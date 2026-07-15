"""fig:flagship — completion at the 512 MB flagship cell.

Out-of-core (b_ooc, 8 reps per rho point) vs capped in-memory (b_inm, 6 reps)
at the three beyond-memory rho points r150/r200/r300. Totals 14/24 vs 0/18,
Fisher two-sided p = 4.1e-5 (matches DATA-AUDIT.md; one-sided is 3.7e-5).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

import data
import style


def main() -> None:
    style.use_style()
    df = data.load_runs()
    # 16/8 baseline only: campaign 'b' has no *_b32_* cells, but rho_table
    # would otherwise mix b32 calibration reps.
    df = df[~df["cell"].str.contains("b32")]
    rho_mb = data.rho_table(df)
    cap = 512.0
    workloads = [150, 200, 300]

    fl = df[(df["campaign"] == "b") & df["workload"].isin(workloads)]
    counts: dict[str, list[tuple[int, int]]] = {}
    for arm_tag in ("ooc", "inm"):
        sub = fl[fl["cell"].str.contains(f"_{arm_tag}_")]
        counts[arm_tag] = [
            (int(sub[(sub["workload"] == w)]["ok"].sum()), int((sub["workload"] == w).sum()))
        for w in workloads]

    ok_a = sum(o for o, _ in counts["ooc"]); n_a = sum(n for _, n in counts["ooc"])
    ok_b = sum(o for o, _ in counts["inm"]); n_b = sum(n for _, n in counts["inm"])
    p = data.fisher_one_sided(ok_a, n_a, ok_b, n_b)
    print(f"flagship: ooc {ok_a}/{n_a} vs inm {ok_b}/{n_b}, Fisher p = {p:.2e}")

    fig, ax = plt.subplots(figsize=(6.8, 4.8))
    x = np.arange(len(workloads))
    width = 0.36
    frac_ooc = [o / n for o, n in counts["ooc"]]
    frac_inm = [o / n for o, n in counts["inm"]]
    bars_o = ax.bar(x - width / 2, frac_ooc, width, color=style.BLUE,
                    label="out-of-core, heap arm (8 reps)")
    bars_i = ax.bar(x + width / 2, frac_inm, width, color=style.ORANGE,
                    label="in-memory, capped (6 reps)")
    for bars, cnt in ((bars_o, counts["ooc"]), (bars_i, counts["inm"])):
        for rect, (o, n) in zip(bars, cnt):
            ax.annotate(f"{o}/{n}", (rect.get_x() + rect.get_width() / 2,
                                     rect.get_height() + 0.02),
                        ha="center", va="bottom", fontsize=9)

    labels = [r"$\rho \approx $" f"{rho_mb[w] / cap:.2f}\n({data.wlabel(w)})" for w in workloads]
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("completion fraction (completed / attempted)")
    ax.set_xlabel("state pressure ρ at the 512 MB cap (workload id in parentheses)")
    ax.set_title("Out-of-core completes beyond the budget where capped in-memory cannot",
                 fontsize=10)
    ax.legend(loc="upper right", title="backend (reps per ρ point)")
    ax.text(0.02, 0.97,
            f"total: {ok_a}/{n_a} vs {ok_b}/{n_b}\nFisher $p = 4.1\\times10^{{-5}}$",
            transform=ax.transAxes, va="top", ha="left", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=style.GREY, lw=0.8))
    style.caption(fig, extra="bar labels = completed/attempted · Fisher's exact test, two-sided")
    style.save(fig, "fig_flagship")


if __name__ == "__main__":
    main()
