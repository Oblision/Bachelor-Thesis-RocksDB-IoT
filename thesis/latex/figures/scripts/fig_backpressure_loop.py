"""fig:pacing-loop — the source-pacing loop behind the completion valley.

A conceptual diagram (no measured data). The recovery in fig:valley is NOT the
engine's "allocation backpressure" lever (Section sec:backpressure, which was
refuted). It is the intrinsic pacing of the bounded admission queue, verified in
the engine (Section sec:rq3): buffers return only as fast as the spiller drains
to the card, so the operator consumes admission tasks slower, the bounded
admission queue fills, and a source that finds the queue full sleeps in
addAdmissionTaskBlocking until a slot frees. Ingest is thereby paced to the
drain rate and the run completes; if ingest never fills the queue the state
outruns the drain and the pool aborts first.

Layout is a tight left-to-right pipeline with the drain below and the pacing
loop closed above, so the canvas carries no dead space.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

import style


def box(ax, cx, cy, w, h, text, fc, ec, fs=9.2):
    ax.add_patch(FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        linewidth=1.4, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(cx, cy, text, ha="center", va="center", fontsize=fs, zorder=4)


def arrow(ax, p0, p1, color="#333333", lw=1.7, rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle="-|>", mutation_scale=14, linewidth=lw, color=color,
        zorder=2, linestyle=ls, connectionstyle=f"arc3,rad={rad}"))


def main() -> None:
    style.use_style()
    fig, ax = plt.subplots(figsize=(7.3, 3.35))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.7)
    ax.axis("off")

    blue = "#EAF2F8"
    y = 3.05
    W, H = 2.5, 0.98
    xs = [1.45, 4.35, 7.25, 10.25]
    box(ax, xs[0], y, W, H, "Sources", blue, style.BLUE)
    box(ax, xs[1], y, W, H, "Admission queue\n(bounded)", blue, style.BLUE)
    box(ax, xs[2], y, W, H, "Window\noperator", blue, style.BLUE)
    box(ax, xs[3], y, 2.6, H, "Buffer pool\n(fixed size)", "#FCF3E7",
        style.ORANGE)

    yd = 1.15
    box(ax, 7.0, yd, W, 0.92, "SD card\n(RocksDB)", "#F0F0F0", style.GREY)
    box(ax, 10.25, yd, 2.6, 0.92, "Spiller", "#E8F6F1", style.GREEN)

    # forward pipeline: labels sit ABOVE the box row, clear of the box borders
    def lbl(x, yy, t, c="#333333", fs=7.9, va="bottom"):
        ax.text(x, yy, t, ha="center", va=va, fontsize=fs, color=c)
    ytop = y + H / 2 + 0.07
    arrow(ax, (xs[0] + W / 2, y), (xs[1] - W / 2, y)); lbl((xs[0]+xs[1])/2, ytop, "ingest")
    arrow(ax, (xs[1] + W / 2, y), (xs[2] - W / 2, y)); lbl((xs[1]+xs[2])/2, ytop, "tasks")
    arrow(ax, (xs[2] + W / 2, y), (xs[3] - 1.3, y)); lbl((xs[2]+xs[3])/2 - 0.05, ytop, "rent buffer")
    # drain path: pool -> spiller -> card; label above the gap, clear of boxes
    arrow(ax, (xs[3], y - H / 2), (10.25, yd + 0.46), color=style.GREEN)
    lbl(10.7, (y - H / 2 + yd) / 2, "spill", c=style.GREEN, va="center")
    arrow(ax, (10.25 - 1.3, yd), (7.0 + W / 2, yd), color=style.GREY)
    lbl(8.6, yd + 0.55, "drain @\ndevice rate", c=style.GREY, fs=7.6)

    # the pacing loop, closed as an arc ABOVE the row: the arc runs Admission
    # queue -> Sources, the last and most direct link in the chain — the queue
    # is what's blocked (full), and that is what stalls the source thread
    # trying to push into it. The earlier links (pool empty -> operator slows
    # -> queue fills) are stated as the explanatory line above instead of drawn,
    # since they are not where the concrete stall happens. A short action label
    # sits on the arc, matching the terse one-to-three-word style of every
    # other edge label ("rent buffer", "spill", "drain @ device rate").
    arrow(ax, (xs[1] - 0.4, y + H / 2), (xs[0] + 0.15, y + H / 2),
          color=style.RED, lw=1.9, rad=0.30)
    ax.text((xs[1] + xs[0]) / 2 + 0.15, 4.05, "queue full,\nblocks & retries",
            ha="center", va="center", fontsize=8.3, color=style.RED,
            fontweight="bold")
    ax.text(6.0, 5.38,
            "buffers free only at the drain rate $\\Rightarrow$ operator slows "
            "$\\Rightarrow$ queue fills $\\Rightarrow$ sources sleep until a slot frees",
            ha="center", va="center", fontsize=8.5, color=style.RED)
    ax.text(xs[0], y - H / 2 - 0.22,
            "addAdmissionTaskBlocking(): the source thread stalls here",
            ha="center", va="top", fontsize=7.2, color=style.RED,
            style="italic", family="monospace")

    ax.text(6.0, 0.12,
            "Sleeping on the full queue paces ingest to the drain rate and the "
            "run completes; if ingest never fills the queue,\nthe state outruns "
            "the drain and the pool aborts first.",
            ha="center", va="bottom", fontsize=8.3, color="#333333")

    style.save(fig, "fig_backpressure_loop")


if __name__ == "__main__":
    main()
