"""fig:effectiveness — completion at the memory boundary, two focused panels.

Panel (a): the capped in-memory baseline alone, completion fraction against
state pressure rho, all caps in one axis. Its only message is the boundary:
every baseline cell with rho < 1 completes, no cell with rho > 1 does.

Panel (b): the head-to-head that matters — capped in-memory versus the best
out-of-core operating point (max completion over pool sizes and both
architectures), restricted to the coordinates at and beyond the boundary
(rho >= RHO_MIN). The lighter fits-workloads, where both arms complete
everything, are omitted; the full grid is in fig:heatmap.

Source: L_/W_ matrices + the L_ in-memory baselines. 16/8 RocksDB
configuration only: the 32/32 backend-size twin runs (*_b32_*) are excluded,
matching fig_heatmap / fig_geometry_law / fig_knife; that contrast is
analysed separately in Section~sec:rb32.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import data
import gridcells as gc
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
    df = df[~df["cell"].str.contains("b32")]  # 16/8 baseline only; see docstring
    rho_mb = data.rho_table(df)
    # grid_df drops the chk/ref verification reruns (rc141 teardown status),
    # which would otherwise count as spurious failing cells.
    cells = data.per_cell(gc.grid_df(df))

    inm_all = cells[(cells["campaign"] == "L") & (cells["arm"] == "inmem")
                    & cells["workload"].notna() & cells["cap_mb"].isin(CAPS)]
    ooc_all = cells[cells["arm"].isin(["heap", "bounded"]) & cells["cap_mb"].isin(CAPS)
                    & cells["campaign"].isin(["L", "W"]) & cells["workload"].notna()]

    fig, (ax_a, ax_b) = plt.subplots(
        2, 1, figsize=(7.6, 5.8), height_ratios=[1.0, 1.45])

    # ---------------- panel (a): the baseline collapse ----------------
    # One row per cap, one marker per baseline cell at its rho, each labeled
    # completed/attempted. Fill encodes the outcome (filled green = all reps
    # complete, orange = partial, open red = all fail): there is no fraction
    # axis to decode, and the collapse at the boundary reads directly as
    # "green left of the line, red right of it".
    ROW = {512: 2, 1024: 1, 2048: 0}   # 512 on top, reading order of panel (b)
    from matplotlib.lines import Line2D
    ax_a.axvspan(1.0, 3.3, color=style.RED, alpha=0.05, zorder=0)
    for cap in CAPS:
        sub = inm_all[inm_all["cap_mb"] == cap]
        prev_x, prev_below = None, False
        for w in sorted(sub["workload"].dropna().unique(),
                        key=lambda w_: rho_mb[int(w_)]):
            f, lab = _frac_lab(sub[sub["workload"] == w])
            x_, y_ = rho_mb[int(w)] / cap, ROW[cap]
            if f == 1.0:
                kw = dict(mfc=style.GREEN, mec="black", mew=0.5)
            elif f == 0.0:
                kw = dict(mfc="none", mec=style.RED, mew=1.3)
            else:
                kw = dict(mfc=style.ORANGE, mec="black", mew=0.5)
            # near-coincident rho values: drop the marker slightly below the
            # row line (y is categorical, so the dodge misstates nothing)
            below = prev_x is not None and (x_ - prev_x) < 0.14 and not prev_below
            dodge = prev_x is not None and (x_ - prev_x) < 0.06
            yy = y_ - (0.34 if dodge else 0.0)
            ax_a.plot(x_, yy, marker="o", ms=6.2, ls="", **kw)
            ax_a.annotate(lab, (x_, yy), textcoords="offset points",
                          xytext=(0, -14.0 if (below or dodge) else 7.0),
                          ha="center", fontsize=6.4, color="#333333")
            prev_x, prev_below = x_, below or dodge
    ax_a.axvline(1.0, color="#666666", lw=1.0, ls="--")
    ax_a.text(0.985, -0.45, "$\\rho = 1$", ha="right", va="center",
              fontsize=8.5, color="#555555", rotation=90)
    ax_a.text(1.98, 0.38, "the baseline collapses at the boundary:\n"
              "no completion past $\\rho \\approx 1$",
              ha="center", va="center", fontsize=8.2, color="#8a1500",
              style="italic")
    ax_a.set_xlim(0.35, 3.3)
    ax_a.set_ylim(-0.85, 2.75)
    ax_a.set_yticks((2, 1, 0), ("512 MB", "1024 MB", "2048 MB"), fontsize=8)
    ax_a.set_xlabel("state pressure $\\rho$ at cap")
    ax_a.set_ylabel("cap", fontsize=9)
    handles_a = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec="black",
               label="all reps complete"),
        Line2D([], [], marker="o", ls="", mfc=style.ORANGE, mec="black",
               label="partial (label = fraction)"),
        Line2D([], [], marker="o", ls="", mfc="none", mec=style.RED, mew=1.3,
               label="all reps fail"),
    ]
    ax_a.legend(handles=handles_a, loc="center right", fontsize=7.0,
                framealpha=0.95, borderpad=0.4, labelspacing=0.3)
    ax_a.set_title("(a)  capped in-memory baseline: each workload cell at its "
                   "state pressure, labeled completed/attempted",
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
