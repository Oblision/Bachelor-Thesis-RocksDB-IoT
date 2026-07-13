"""fig:valley — why the completion window is not a pyramid.

At a single cap and pool (512 MB, 16k bounded pool) completion does NOT fall
monotonically as state pressure rho rises. It dips into a valley and then
RECOVERS at the heaviest load that still fits, because that load fills the pool
early, backpressure throttles ingest down to the spiller's drain rate, and the
run finishes slowly instead of failing fast. The lighter middle loads free-run
above the drain ceiling and abort.

Two stacked panels share the workload axis (ordered by rho):
  (a) completion fraction: observed valley+recovery vs the naive monotone
      expectation (the "pyramid" that does not happen).
  (b) ingest rate: realized (throttled) for completing cells, free-running
      demand for failing cells, against the empirical drain ceiling.

All numbers are recomputed from the lag-validation TSVs (16/8 baseline; the
32/32 twins are pooled as extra reps exactly as in the rest of the RQ1 family).
Cap 512 MB, bounded (W_) arm, 16k pool — the middle column of fig:heatmap-512.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import data
import gridcells as gc
import style

CAP = 512
POOL = 16000
CEILING = 2.6          # empirical SD drain ceiling, M tuples/s (see fig:race)


def _column(df, rho_mb):
    """Per-workload completion and ingest for the 512 MB / 16k bounded column."""
    _, _, bnd = gc.arm_frames(gc.grid_df(df), CAP)
    sub = bnd[bnd["pool_buffers"] == POOL]
    # free-running demand per workload from completing reference runs
    ref = df[df["ok"] & df["workload"].notna()
             & df["arm"].isin(["inmem", "uncapped"]) & (df["tps"] > 0)]
    free = {int(w): float(v) / 1e6
            for w, v in ref.groupby("workload")["tps"].median().items()}
    rows = []
    for w in sorted(sub["workload"].dropna().unique(), key=lambda w: rho_mb[int(w)]):
        r = sub[sub["workload"] == w]
        n, nok = len(r), int(r["ok"].sum())
        realized = r[r["ok"]]["tps"].median()
        complete = nok > n / 2
        # completing cells plot their realized (throttled) rate; failing cells
        # plot free-running demand (a dead run has no realized rate of its own),
        # matching the fig:race convention.
        ingest = (realized / 1e6 if complete and not np.isnan(realized)
                  else free.get(int(w), np.nan))
        # dominant failure mode of the failing reps, in the thesis's own
        # categories (POOL abort = throttle-recoverable race; OOM = the resident
        # state exceeds the cap, which throttling ingest cannot cure).
        cats: dict[str, int] = {}
        for rc in r[~r["ok"]]["rc"]:
            c = gc.classify_rc(rc)
            cats[c] = cats.get(c, 0) + 1
        failmode = max(cats, key=cats.get) if cats else None
        rows.append(dict(
            label=data.wlabel(w), rho=rho_mb[int(w)] / CAP,
            frac=nok / n, nok=nok, n=n, complete=complete, ingest=ingest,
            failmode=failmode,
        ))
    return rows


def _fail_color(mode):
    return style.RED if mode == "OOM" else style.ORANGE


def main() -> None:
    style.use_style()
    df = data.load_runs()
    df = df[~df["cell"].str.contains("b32")]
    rho_mb = data.rho_table(df)
    rows = _column(df, rho_mb)

    x = np.arange(len(rows))
    labels = [f"{r['label']}\n$\\rho$ {r['rho']:.2f}" for r in rows]
    fracs = np.array([r["frac"] for r in rows])
    colors = [style.GREEN if r["complete"] else _fail_color(r["failmode"])
              for r in rows]

    fig, (ax0, ax1) = plt.subplots(
        2, 1, figsize=(6.6, 5.6), sharex=True,
        gridspec_kw=dict(height_ratios=[1.0, 1.0]))

    # ---- panel (a): completion, observed valley vs expected pyramid ----
    # naive expectation: completion tapers with size -- a pyramid, full at the
    # light workloads and sloping to zero as the state grows. Drawn as a
    # schematic silhouette (indicative, NOT a fitted / quantitative model).
    xs = np.linspace(x[0], x[-1], 200)
    t = (xs - x[0]) / (x[-1] - x[0])
    # high at the very start, then a sharp roll-off on the left and a long low
    # tail -- a left-leaning pyramid, drawn as a qualitative sketch (not a
    # fitted model). Exponent < 1 pushes the steep descent toward the left.
    expected = 0.5 * (1.0 + np.cos(np.pi * t ** 0.58))
    ax0.fill_between(xs, 0, expected, color=style.GREY, alpha=0.08, zorder=1)
    ax0.plot(xs, expected, ls="--", color=style.GREY, lw=1.6,
             label="naive expectation: a pyramid tapering with size (qualitative)",
             zorder=2)
    ax0.text(1.18, 0.90, "qualitative",
             ha="center", va="center", fontsize=7.8, color=style.GREY,
             style="italic")
    ax0.plot(x, fracs, color=style.BLUE, lw=1.4, zorder=3)
    ax0.scatter(x, fracs, c=colors, s=95, zorder=4, edgecolors="white",
                linewidths=1.1)
    for xi, r in zip(x, rows):
        ax0.annotate(f"{r['nok']}/{r['n']}", (xi, r["frac"]),
                     textcoords="offset points", xytext=(0, 9),
                     ha="center", fontsize=8.5)
    # mark the recovery
    rec = int(np.argmax([r["rho"] for r in rows if r["complete"]] and
                        [r["frac"] if r["complete"] else -1 for r in rows]))
    # annotate the recovery workload (heaviest completing cell)
    comp_idx = [i for i, r in enumerate(rows) if r["complete"]]
    rec = comp_idx[-1]
    # short leader labels placed in clear space, off the completion line
    ax0.annotate("recovery\n(throttled)", (x[rec], fracs[rec]),
                 textcoords="offset points", xytext=(30, -2), ha="left",
                 va="center", fontsize=8.2, color=style.GREEN,
                 arrowprops=dict(arrowstyle="->", color=style.GREEN, lw=1.1))
    ax0.axvspan(x[1] - 0.4, x[2] + 0.4, color=style.ORANGE, alpha=0.07, zorder=0)
    ax0.text((x[1] + x[2]) / 2, 0.60, "valley\n(pool aborts)", ha="center",
             fontsize=8.2, color=style.ORANGE, style="italic")
    # the recovery is bounded above: past the fit edge the state itself exceeds
    # the cap, so the run OOMs regardless of ingest rate.
    oom_i = next((i for i, r in enumerate(rows) if r["failmode"] == "OOM"), None)
    if oom_i is not None:
        # place in the right margin so the descending completion line stays clear
        ax0.annotate("OOM\n(state $>$ cap)", (x[oom_i], fracs[oom_i]),
                     textcoords="offset points", xytext=(16, 20), ha="left",
                     va="bottom", fontsize=8.2, color=style.RED,
                     arrowprops=dict(arrowstyle="->", color=style.RED, lw=1.1))
    ax0.set_ylabel("completion fraction")
    ax0.set_ylim(-0.08, 1.16)
    ax0.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax0.set_title("(a) completion fraction: the valley and its recovery")
    ax0.legend(loc="lower left", fontsize=8.3)

    # ---- panel (b): ingest rate vs drain ceiling ----
    ing = np.array([r["ingest"] for r in rows])
    ax1.axhspan(CEILING - 0.1, CEILING + 0.1, color=style.PURPLE, alpha=0.16,
                zorder=0)
    ax1.axhline(CEILING, color=style.PURPLE, lw=1.3, ls="-", zorder=1)
    ax1.text(1.15, CEILING - 0.07, "storage-drain ceiling $\\approx$ 2.6 M/s",
             ha="left", va="top", fontsize=8.2, color=style.PURPLE)
    for xi, r in zip(x, rows):
        if r["complete"]:
            ax1.scatter(xi, r["ingest"], s=95, facecolors=style.GREEN,
                        edgecolors=style.GREEN, linewidths=1.6, zorder=4)
            tag, tc = (("throttled", style.GREEN) if r["ingest"] < CEILING
                       else ("fits ($\\rho<1$)", style.GREEN))
        elif r["failmode"] == "OOM":
            ax1.scatter(xi, r["ingest"], s=105, marker="X", facecolors=style.RED,
                        edgecolors="white", linewidths=1.0, zorder=4)
            tag, tc = "OOM (state $>$ cap)", style.RED
        else:  # pool abort
            ax1.scatter(xi, r["ingest"], s=95, facecolors="white",
                        edgecolors=style.ORANGE, linewidths=1.6, zorder=4)
            tag, tc = "unthrottled", style.ORANGE
        ax1.annotate(tag, (xi, r["ingest"]), textcoords="offset points",
                     xytext=(0, 11), ha="center", fontsize=7.8, color=tc)
    ax1.set_ylabel("ingest rate (M tuples/s)")
    ax1.set_ylim(0, 4.0)
    ax1.set_xlim(-0.5, len(rows) - 1 + 0.78)
    ax1.set_xticks(x, labels, fontsize=8.5)
    ax1.set_xlabel("workload (ordered by state pressure $\\rho$)")
    ax1.set_title("(b) ingest rate against the drain ceiling")

    handles = [
        Line2D([], [], marker="o", ls="", mfc=style.GREEN, mec=style.GREEN,
               ms=9, label="completes"),
        Line2D([], [], marker="o", ls="", mfc="white", mec=style.ORANGE,
               ms=9, mew=1.6, label="pool abort (throttle-recoverable)"),
        Line2D([], [], marker="X", ls="", mfc=style.RED, mec="white",
               ms=10, label="OOM (state exceeds cap)"),
    ]
    ax1.legend(handles=handles, loc="lower left", fontsize=8.0,
               title="failing cells at unthrottled demand", title_fontsize=7.6)

    fig.suptitle("Completion dips, then recovers, as pacing engages "
                 f"({CAP} MB, {POOL // 1000}k pool)",
                 fontsize=11, fontweight="bold")
    style.save(fig, "fig_valley")


if __name__ == "__main__":
    main()
