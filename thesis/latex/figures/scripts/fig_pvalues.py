"""fig:pvalues — forest plot of the headline Fisher's-exact contrasts.

One row per decisive completion-proportion contrast in the evaluation. Each row
shows the point estimate of the completion-proportion DIFFERENCE (arm A minus arm
B), its 95% confidence interval (Newcombe's method, built from Wilson score
intervals via scipy), and the reported one-sided Fisher's-exact p-value. A dashed
reference marks p = 0.05 in the p-value column; every contrast clears it by orders
of magnitude.

The completions and p-values are the ones quoted in the prose (verbatim); the CIs
are computed here from those completion counts.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import norm

import style

# (label, ok_A, n_A, ok_B, n_B, p_value) — completions and p verbatim from prose.
CONTRASTS = [
    ("flagship\n(default vs corrected)", 14, 24, 0, 18, 4.1e-5),
    ("attribution ladder\n(levers on vs off)", 7, 8, 0, 8, 7.0e-4),
    ("pooled lever arms\n(pooled vs unpooled)", 12, 16, 0, 16, 8.1e-6),
    ("allocation-type win @512\n(pooled vs heap)", 8, 8, 0, 8, 2.3e-5),
    ("intrinsic backpressure\n(bounded vs heap)", 23, 24, 3, 10, 1.6e-4),
]


def _wilson(k: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for a single proportion."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def _newcombe(ok_a: int, n_a: int, ok_b: int, n_b: int, conf: float = 0.95):
    """Newcombe method for the CI of the difference p_A - p_B."""
    z = float(norm.ppf(1 - (1 - conf) / 2))
    diff = ok_a / n_a - ok_b / n_b
    l1, u1 = _wilson(ok_a, n_a, z)
    l2, u2 = _wilson(ok_b, n_b, z)
    lower = diff - np.sqrt((ok_a / n_a - l1) ** 2 + (u2 - ok_b / n_b) ** 2)
    upper = diff + np.sqrt((u1 - ok_a / n_a) ** 2 + (ok_b / n_b - l2) ** 2)
    return diff, lower, upper


def main() -> None:
    style.use_style()
    fig, (axf, axp) = plt.subplots(
        1, 2, figsize=(9.2, 4.4), width_ratios=[3.0, 1.0], sharey=True)

    ys = np.arange(len(CONTRASTS))[::-1]  # first row at the top
    for y, (label, ok_a, n_a, ok_b, n_b, _) in zip(ys, CONTRASTS):
        diff, lo, hi = _newcombe(ok_a, n_a, ok_b, n_b)
        axf.plot([lo, hi], [y, y], color=style.BLUE, lw=2.0, zorder=2,
                 solid_capstyle="butt")
        axf.plot([lo, lo], [y - 0.12, y + 0.12], color=style.BLUE, lw=1.4)
        axf.plot([hi, hi], [y - 0.12, y + 0.12], color=style.BLUE, lw=1.4)
        axf.scatter([diff], [y], s=70, color=style.BLUE, zorder=3,
                    edgecolors="black", linewidths=0.5)
        axf.annotate(f"{ok_a}/{n_a} vs {ok_b}/{n_b}", xy=(hi, y), xytext=(6, 6),
                     textcoords="offset points", ha="left", va="bottom",
                     fontsize=7.2, color="#444444")

    axf.axvline(0.0, color="#666666", lw=0.9, ls="--", zorder=1)
    axf.set_xlim(-0.15, 1.15)
    axf.set_yticks(ys, [c[0] for c in CONTRASTS], fontsize=8.2)
    axf.set_ylim(-0.6, len(CONTRASTS) - 0.4)
    axf.set_xlabel("completion-proportion difference (arm A $-$ arm B), 95% CI")
    axf.set_title("(a) effect size", fontsize=9.5)
    axf.grid(True, axis="x", alpha=0.25)

    # p-value column (log scale, small p to the left = stronger).
    for y, (_, _, _, _, _, p) in zip(ys, CONTRASTS):
        axp.scatter([p], [y], s=60, color=style.RED, zorder=3,
                    edgecolors="black", linewidths=0.5)
        axp.annotate(_fmt_p(p), xy=(p, y), xytext=(0, 7), textcoords="offset points",
                     ha="center", va="bottom", fontsize=7.4, color="#8a1500")
    axp.axvline(0.05, color=style.GREY, lw=1.1, ls="--", zorder=1)
    axp.text(0.05, len(CONTRASTS) - 0.45, "p = 0.05", rotation=90, fontsize=7.2,
             ha="right", va="top", color="#555555")
    axp.set_xscale("log")
    axp.set_xlim(1e-6, 2e-1)
    axp.set_xlabel("Fisher's exact test $p$ (two-sided)")
    axp.set_title("(b) significance", fontsize=9.5)
    axp.grid(True, axis="x", alpha=0.25)

    fig.suptitle("Headline completion contrasts: effect size and significance",
                 fontsize=11, fontweight="bold")
    style.caption(fig, extra="(a) Newcombe 95% CI for the completion-proportion difference; "
                             "(b) reported two-sided Fisher's-exact p, dashed line at p = 0.05")
    style.save(fig, "fig_pvalues")


def _fmt_p(p: float) -> str:
    exp = int(np.floor(np.log10(p)))
    mant = p / 10 ** exp
    return f"$p={mant:.1f}\\times10^{{{exp}}}$"


if __name__ == "__main__":
    main()
