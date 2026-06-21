"""fig:effectiveness — beyond-memory completion across all three caps.

Per cap: the capped in-memory baseline (which OOMs once ρ>1) versus the best
out-of-core operating point (max completion over pool sizes and both
architectures) for each workload, ordered by state pressure ρ. Shows that
out-of-core extends the feasible region past ρ=1 at every cap, and where the
absolute edge sits. Source: L_/W_ matrices + the L_ in-memory baselines.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

import data
import style

CAPS = (512, 1024, 2048)


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    cells = data.per_cell(df)

    fig, axes = plt.subplots(1, 3, figsize=(11.2, 4.2), sharey=True)
    for col, cap in enumerate(CAPS):
        ax = axes[col]
        inm = cells[(cells["campaign"] == "L") & (cells["arm"] == "inmem")
                    & (cells["cap_mb"] == cap)]
        ooc = cells[cells["arm"].isin(["heap", "bounded"]) & (cells["cap_mb"] == cap)
                    & cells["campaign"].isin(["L", "W"]) & cells["workload"].notna()]
        ws = sorted(set(inm["workload"].dropna()) | set(ooc["workload"].dropna()),
                    key=lambda w: rho_mb[int(w)])
        x = np.arange(len(ws))
        width = 0.38

        def frac_lab(frame):
            if frame.empty:
                return np.nan, ""
            nok, rp = int(frame["n_ok"].sum()), int(frame["reps"].sum())
            return nok / rp, f"{nok}/{rp}"

        inm_f, ooc_f, inm_l, ooc_l = [], [], [], []
        for w in ws:
            f, l = frac_lab(inm[inm["workload"] == w])
            inm_f.append(f); inm_l.append(l)
            o = ooc[ooc["workload"] == w]
            if o.empty:
                ooc_f.append(np.nan); ooc_l.append("")
            else:
                r = (o["n_ok"] / o["reps"])
                best = o.loc[r.idxmax()]
                ooc_f.append(best["n_ok"] / best["reps"])
                ooc_l.append(f"{int(best['n_ok'])}/{int(best['reps'])}")

        bi = ax.bar(x - width / 2, np.nan_to_num(inm_f), width,
                    color=style.ORANGE, label="in-memory, capped")
        bo = ax.bar(x + width / 2, np.nan_to_num(ooc_f), width,
                    color=style.BLUE, label="out-of-core (best config)")
        for rect, lab in list(zip(bi, inm_l)) + list(zip(bo, ooc_l)):
            if lab:
                ax.annotate(lab, (rect.get_x() + rect.get_width() / 2,
                                  rect.get_height() + 0.02),
                            ha="center", va="bottom", fontsize=7)
        # rho = 1 boundary between the fits and beyond-memory workloads
        rhos = [rho_mb[int(w)] / cap for w in ws]
        cross = next((i for i, r in enumerate(rhos) if r > 1), None)
        if cross is not None and cross > 0:
            ax.axvline(cross - 0.5, color=style.GREY, lw=1.0, ls="--")
            ax.text(cross - 0.5, 1.14, "ρ = 1", ha="center", va="top",
                    fontsize=8, color="#555555")

        ax.set_xticks(x, [f"r{int(w)}\n{rho_mb[int(w)] / cap:.2f}" for w in ws],
                      fontsize=7.5)
        ax.set_title(f"cap {cap} MB")
        ax.set_xlabel("workload (ρ at cap)")
        ax.set_ylim(0, 1.20)
        if col == 0:
            ax.set_ylabel("completion fraction (completed / attempted)")
    axes[0].legend(loc="lower left", fontsize=8)

    fig.suptitle("Out-of-core extends beyond-memory completion at every cap",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="bar labels = completed/attempted · out-of-core = best operating point over pool sizes and both architectures")
    style.save(fig, "fig_effectiveness")


if __name__ == "__main__":
    main()
