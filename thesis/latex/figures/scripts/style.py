"""Shared matplotlib style for all thesis figures.

Design rule (chapter 5 comment): generous spacing, no overlapping labels or items.
Every figure uses constrained_layout so matplotlib computes non-overlapping
bounding boxes for titles, ticks, and legends.
"""
from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Colorblind-safe palette (Wong / seaborn-colorblind).
BLUE = "#0173B2"      # out-of-core / bounded
ORANGE = "#DE8F05"    # in-memory capped
GREEN = "#029E73"     # uncapped reference / success
RED = "#D55E00"       # failure
PURPLE = "#CC78BC"
GREY = "#949494"
YELLOW = "#ECE133"
SKY = "#56B4E9"

OK_CMAP = "RdYlGn"    # completion fraction heatmaps

RC = {
    "figure.constrained_layout.use": True,
    "figure.dpi": 150,
    "font.size": 10,
    "font.family": "sans-serif",
    "axes.titlesize": 10.5,
    "axes.labelsize": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.6,
    "legend.fontsize": 9,
    "legend.framealpha": 0.9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.03,
}


def use_style() -> None:
    plt.rcParams.update(RC)


# --- shared provenance / methods caption -----------------------------------
# Every data figure carries the same two-line footnote so the workload, the
# fixed configuration, the sample size, and the data source are unambiguous and
# identical across the thesis (scientific-figure best practice).
_METHODS = ("Synthetic Zipfian sensor stream (s≈1.0, 1.5 M-key universe, "
            "top-10≈ 20% of traffic)  ·  tumbling window 50k tuples, "
            "emit-lag 4 M (N_w=80 retained windows)  ·  2 pinned cores, hard cgroup cap, "
            "lz4 spill → SD card")
_SOURCE = ("n = 8–10 repetitions per cell unless labelled otherwise  ·  "
           "source: lag-validation campaign (3,133 runs, 457 cells), debug build, single host")


def caption(fig, extra: str = "") -> None:
    """Stamp the standard methods/provenance footnote on a figure.

    The text is wrapped onto several short lines on purpose. With
    savefig.bbox="tight", a single long caption line would stretch the saved
    canvas sideways to fit the text, leaving the actual plot a small fraction of
    the width; scaled to the page, the plot then looks tiny. Wrapping each
    provenance clause to a narrow column keeps the canvas width governed by the
    plot, not the footnote. Uses supxlabel so constrained_layout reserves space.
    Pass `extra` for a figure-specific note (e.g. the statistical test used).
    """
    clauses = _METHODS.split("  ·  ")
    if extra:
        clauses.append(extra)
    clauses += _SOURCE.split("  ·  ")
    lines = []
    for clause in clauses:
        lines.extend(textwrap.wrap(clause, width=80) or [clause])
    fig.supxlabel("\n".join(lines), fontsize=8.5, color="#5a5a5a",
                  linespacing=1.3)


def save(fig, name: str) -> None:
    """Write a PDF into latex/img/ and report the path."""
    from data import IMG_OUT

    out = IMG_OUT / f"{name}.pdf"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")
