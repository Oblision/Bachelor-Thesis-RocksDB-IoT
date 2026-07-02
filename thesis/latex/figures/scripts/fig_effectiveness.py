"""fig:effectiveness — completion at the memory boundary, two focused panels.

Panel (a): the capped in-memory baseline alone, completion fraction against
state pressure rho, all caps in one axis. Its only message is the boundary:
every baseline cell with rho < 1 completes, no cell with rho > 1 does.

Panel (b): the head-to-head that matters — capped in-memory versus the best
out-of-core operating point (max completion over pool sizes and both
architectures), restricted to the coordinates at and beyond the boundary
(rho >= RHO_MIN). The lighter fits-workloads, where both arms complete
everything, are omitted; the full grid is in fig:heatmap.

Source: L_/W_ matrices + the L_ in-memory baselines.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import data
import style

CAPS = (512, 1024, 2048)
RHO_MIN = 0.75          # panel (b): keep pairs at and beyond the boundary
CAP_MARK = {512: "o", 1024: "s", 2048: "^"}


def _frac_lab(frame) -> tuple[float, str]:
    """Pooled completion fraction and its n/m label for one coordinate."""
    if frame.empty:
        return np.nan, ""
    nok, rp = int(frame["n_ok"].sum()), int(frame["reps"].sum())
    return nok / rp, f"{nok}/{rp}"


def main() -> None:
    style.use_style()
    df = data.load_runs()
    rho_mb = data.rho_table(df)
    cells = data.per_cell(df)

    inm_all = cells[(cells["campaign"] == "L") & (cells["arm"] == "inmem")
                    & cells["workload"].notna() & cells["cap_mb"].isin(CAPS)]
    ooc_all = cells[cells["arm"].isin(["heap", "bounded"]) & cells["cap_mb"].isin(CAPS)
                    & cells["campaign"].isin(["L", "W"]) & cells["workload"].notna()]

    fig, (ax_a, ax_b) = plt.subplots(
        2, 1, figsize=(7.6, 5.8), height_ratios=[1.0, 1.45])

    # ---------------- panel (a): the baseline collapse ----------------
    for cap in CAPS:
        sub = inm_all[inm_all["cap_mb"] == cap]
        xs, ys = [], []
        for w in sorted(sub["workload"].dropna().unique()):
            f, _ = _frac_lab(sub[sub["workload"] == w])
            xs.append(rho_mb[int(w)] / cap)
            ys.append(f)
        ax_a.plot(xs, ys, ls="", marker=CAP_MARK[cap], ms=6.5,
                  mfc=style.ORANGE, mec="black", mew=0.5, alpha=0.9,
                  label=f"{cap} MB cap")
    ax_a.axvline(1.0, color="#666666", lw=1.0, ls="--")
    ax_a.text(0.985, 0.5, "$\\rho = 1$", ha="right", va="center",
              fontsize=8.5, color="#555555", rotation=90)
    ax_a.text(1.06, 0.62, "no capped in-memory cell\ncompletes past $\\rho = 1$",
              ha="left", va="center", fontsize=8.2, color="#8a1500",
              style="italic")
    ax_a.set_xlim(0.35, 3.3)
    ax_a.set_ylim(-0.06, 1.30)
    ax_a.set_yticks((0, 0.5, 1.0))
    ax_a.set_xlabel("state pressure $\\rho$ at cap")
    ax_a.set_ylabel("completion\nfraction", fontsize=9)
    ax_a.legend(loc="center right", fontsize=7.8, framealpha=0.95)
    ax_a.set_title("(a)  capped in-memory baseline: completion vs. state pressure",
                   fontsize=9.5, loc="left")

    # ------- panel (b): head-to-head at and beyond the boundary -------
    coords: list[tuple[int, int, float]] = []
    for cap in CAPS:
        ws = set(inm_all[inm_all["cap_mb"] == cap]["workload"].dropna()) | \
             set(ooc_all[ooc_all["cap_mb"] == cap]["workload"].dropna())
        for w in ws:
            rho = rho_mb[int(w)] / cap
            if rho >= RHO_MIN:
                coords.append((cap, int(w), rho))
    coords.sort(key=lambda c: (c[0], c[2]))

    width = 0.38
    x = np.arange(len(coords), dtype=float)
    # small extra gap between cap groups
    gap = 0.55
    for i in range(1, len(coords)):
        if coords[i][0] != coords[i - 1][0]:
            x[i:] += gap

    inm_f, inm_l, ooc_f, ooc_l = [], [], [], []
    for cap, w, _rho in coords:
        f, lab = _frac_lab(inm_all[(inm_all["cap_mb"] == cap)
                                   & (inm_all["workload"] == w)])
        inm_f.append(f)
        inm_l.append(lab)
        o = ooc_all[(ooc_all["cap_mb"] == cap) & (ooc_all["workload"] == w)]
        if o.empty:
            ooc_f.append(np.nan)
            ooc_l.append("")
        else:
            r = o["n_ok"] / o["reps"]
            best = o.loc[r.idxmax()]
            ooc_f.append(best["n_ok"] / best["reps"])
            ooc_l.append(f"{int(best['n_ok'])}/{int(best['reps'])}")

    bi = ax_b.bar(x - width / 2, np.nan_to_num(inm_f), width, color=style.ORANGE)
    bo = ax_b.bar(x + width / 2, np.nan_to_num(ooc_f), width, color=style.BLUE)
    # Pair labels: each above its own bar; when the two heights are close, the
    # out-of-core label is bumped one level up so adjacent labels never collide.
    for rect, lab in zip(bi, inm_l):
        if lab:
            ax_b.annotate(lab, (rect.get_x() + rect.get_width() / 2,
                                rect.get_height() + 0.02),
                          ha="center", va="bottom", fontsize=6.6)
    for rect_i, rect_o, lab in zip(bi, bo, ooc_l):
        if lab:
            h_i, h_o = rect_i.get_height(), rect_o.get_height()
            y = h_i + 0.115 if abs(h_i - h_o) < 0.10 else h_o + 0.02
            ax_b.annotate(lab, (rect_o.get_x() + rect_o.get_width() / 2, y),
                          ha="center", va="bottom", fontsize=6.6,
                          color="#1a3a5c")

    # shade the beyond-memory pairs (rho > 1) per cap group
    beyond = [xi for xi, (_c, _w, rho) in zip(x, coords) if rho > 1]
    if beyond:
        runs: list[list[float]] = [[beyond[0]]]
        for xi in beyond[1:]:
            if xi - runs[-1][-1] <= 1.0 + 1e-6:
                runs[-1].append(xi)
            else:
                runs.append([xi])
        for r_ in runs:
            ax_b.axvspan(min(r_) - 0.5, max(r_) + 0.5,
                         color=style.RED, alpha=0.06, zorder=0)
            ax_b.text((min(r_) + max(r_)) / 2, 1.24, "$\\rho > 1$",
                      ha="center", va="top", fontsize=7.8, color="#8a1500")

    # cap group labels + separators
    for cap in CAPS:
        xs_cap = [xi for xi, (c, _w, _r) in zip(x, coords) if c == cap]
        if xs_cap:
            ax_b.text((min(xs_cap) + max(xs_cap)) / 2, -0.42,
                      f"cap {cap} MB", ha="center", va="top",
                      fontsize=8.6, fontweight="bold", color="#333333")
    for i in range(1, len(coords)):
        if coords[i][0] != coords[i - 1][0]:
            ax_b.axvline((x[i] + x[i - 1]) / 2, color="#bbbbbb", lw=0.8)

    ax_b.set_xticks(x, [f"r{w}\n$\\rho$ {rho:.2f}" for _c, w, rho in coords],
                    fontsize=7.5)
    ax_b.set_ylim(0, 1.32)
    ax_b.set_yticks((0, 0.5, 1.0))
    ax_b.set_ylabel("completion\nfraction", fontsize=9)
    ax_b.set_title(f"(b)  in-memory vs. best out-of-core ($\\rho \\geq {RHO_MIN}$)",
                   fontsize=9.5, loc="left")
    ax_b.legend(handles=[Patch(color=style.ORANGE, label="in-memory, capped"),
                         Patch(color=style.BLUE, label="out-of-core (best config)")],
                loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2,
                fontsize=7.8, framealpha=1.0, borderaxespad=0.15)

    fig.suptitle("Completion at the memory boundary: baseline collapse and the "
                 "out-of-core head-to-head", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    style.caption(fig)
    print(f"panel (b) pairs: {len(coords)}")
    for (cap, w, rho), il, ol in zip(coords, inm_l, ooc_l):
        print(f"  {cap}/r{w} rho={rho:.2f}: inm {il} vs ooc {ol}")
    style.save(fig, "fig_effectiveness")


if __name__ == "__main__":
    main()
