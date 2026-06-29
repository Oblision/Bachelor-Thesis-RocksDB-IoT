"""Shared matplotlib style for all thesis figures.

Design rule (chapter 5 comment): generous spacing, no overlapping labels or items.
Every figure uses constrained_layout so matplotlib computes non-overlapping
bounding boxes for titles, ticks, and legends.
"""
from __future__ import annotations

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
# The methods/provenance footnote is NO LONGER baked into the figure. It now
# lives in LaTeX (\methodsnote / \nexmarknote in Template.tex), appended under
# each figure's \caption. Two reasons:
#   1. With savefig.bbox="tight", a long text artist stretched the saved canvas
#      sideways and left the actual plot a small fraction of the width.
#   2. Wrapping the text inside matplotlib produced ragged mid-clause breaks.
# LaTeX wraps the note to \textwidth automatically and the plot governs the
# canvas, so figures render large. caption() is kept as a no-op so existing
# call sites stay valid; the per-figure `extra` notes are folded into the LaTeX
# captions where they matter.
def caption(fig, extra: str = "") -> None:  # noqa: ARG001 - kept for compatibility
    """No-op: the methods/provenance note is set in LaTeX, not in the figure."""
    return None


def save(fig, name: str) -> None:
    """Write a PDF into latex/img/ and report the path."""
    from data import IMG_OUT

    out = IMG_OUT / f"{name}.pdf"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")
