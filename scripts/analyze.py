#!/usr/bin/env python3
"""
Analysis script for RocksDB benchmark results.

Supports both unconstrained (native) and constrained (Docker IoT) modes.

Usage:
    python3 scripts/analyze.py                  # Analyze unconstrained results
    python3 scripts/analyze.py --constrained    # Analyze constrained results
    python3 scripts/analyze.py --compare        # Side-by-side comparison
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add scripts dir to path for config import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONSTRAINED_SUMMARY_CSV, PROJECT_ROOT, SUMMARY_CSV


# ── Data loading ──────────────────────────────────────────────────────────────

NUMERIC_COLS = [
    "ops_per_sec", "micros_per_op", "mb_per_sec",
    "p50", "p75", "p99", "p99_9", "p99_99",
    "write_amp", "compaction_write_gb", "stall_percent", "stall_micros",
    "cache_size", "memory_limit_mb",
]


def load_results(csv_path: Path) -> pd.DataFrame:
    """Load and clean a benchmark results CSV."""
    if not csv_path.exists():
        print(f"ERROR: No results found at {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}")

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ── Shared helpers ────────────────────────────────────────────────────────────

def _human_bytes(n: float) -> str:
    """Format byte count as human-readable string."""
    if n >= 1024**3:
        return f"{n / 1024**3:.0f}GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.0f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n:.0f}B"


def _try_import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        print("matplotlib not installed, skipping plots.")
        print("Install with: pip install matplotlib")
        return None


# ── Unconstrained analysis (original) ────────────────────────────────────────

def print_summary(df: pd.DataFrame) -> None:
    """Print a summary table of results grouped by workload."""
    print("\n=== Results Summary ===\n")

    for workload in df["workload"].unique():
        subset = df[df["workload"] == workload]
        print(f"--- {workload} ({len(subset)} runs) ---")
        if "ops_per_sec" in subset.columns:
            stats = subset["ops_per_sec"].describe()
            print(f"  ops/sec: mean={stats['mean']:.0f}, "
                  f"min={stats['min']:.0f}, max={stats['max']:.0f}, "
                  f"std={stats['std']:.0f}")
        print()


def plot_ops_by_compression(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot ops/sec grouped by compression type."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    for workload in df["workload"].unique():
        subset = df[df["workload"] == workload]
        if subset["ops_per_sec"].isna().all():
            continue

        fig, ax = plt.subplots(figsize=(8, 5))
        subset.boxplot(column="ops_per_sec", by="compression_type", ax=ax)
        ax.set_title(f"{workload}: ops/sec by compression type")
        ax.set_xlabel("Compression Type")
        ax.set_ylabel("ops/sec")
        plt.suptitle("")
        plt.tight_layout()
        out_path = out_dir / f"plot_{workload}_compression.png"
        fig.savefig(out_path, dpi=150)
        print(f"Saved: {out_path}")
        plt.close(fig)


def plot_stalls_vs_triggers(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot stall percentage vs L0 slowdown write trigger."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    write_workloads = df[df["workload"].isin(["fillrandom", "overwrite"])]
    if write_workloads.empty or write_workloads["stall_percent"].isna().all():
        print("No stall data available for write workloads, skipping plot.")
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    for workload in write_workloads["workload"].unique():
        subset = write_workloads[write_workloads["workload"] == workload]
        grouped = subset.groupby("level0_slowdown_writes_trigger")["stall_percent"].mean()
        ax.plot(grouped.index, grouped.values, marker="o", label=workload)

    ax.set_xlabel("level0_slowdown_writes_trigger")
    ax.set_ylabel("Stall %")
    ax.set_title("Stall % vs L0 Slowdown Write Trigger")
    ax.legend()
    plt.tight_layout()
    out_path = out_dir / "plot_stalls_vs_triggers.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


def analyze_unconstrained() -> None:
    """Run the original unconstrained analysis."""
    df = load_results(SUMMARY_CSV)
    out_dir = SUMMARY_CSV.parent
    print_summary(df)
    plot_ops_by_compression(df, out_dir)
    plot_stalls_vs_triggers(df, out_dir)
    print("\nDone.")


# ── Constrained analysis ─────────────────────────────────────────────────────

def print_constrained_summary(df: pd.DataFrame) -> None:
    """Print summary for constrained results, highlighting cache_size impact."""
    print("\n=== Constrained Results Summary ===\n")
    print(f"Memory limit: {df['memory_limit_mb'].iloc[0]}MB")
    print()

    for workload in df["workload"].unique():
        subset = df[df["workload"] == workload]
        print(f"--- {workload} ({len(subset)} runs) ---")

        if "ops_per_sec" in subset.columns and not subset["ops_per_sec"].isna().all():
            stats = subset["ops_per_sec"].describe()
            print(f"  ops/sec: mean={stats['mean']:.0f}, "
                  f"min={stats['min']:.0f}, max={stats['max']:.0f}")

        # Per cache_size breakdown
        if "cache_size" in subset.columns:
            print("  By cache_size:")
            for cs in sorted(subset["cache_size"].dropna().unique()):
                cs_subset = subset[subset["cache_size"] == cs]
                ops_mean = cs_subset["ops_per_sec"].mean()
                stall_mean = cs_subset["stall_percent"].mean()
                wamp_mean = cs_subset["write_amp"].mean()
                print(f"    {_human_bytes(cs):>6s}: "
                      f"ops/sec={ops_mean:,.0f}, "
                      f"stall%={stall_mean:.2f}, "
                      f"write_amp={wamp_mean:.1f}")
        print()


def plot_ops_vs_cache_size(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot ops/sec vs cache_size per compression type — the key thesis plot."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    if "cache_size" not in df.columns:
        return

    for workload in df["workload"].unique():
        subset = df[df["workload"] == workload]
        if subset["ops_per_sec"].isna().all():
            continue

        fig, ax = plt.subplots(figsize=(10, 6))

        for comp in sorted(subset["compression_type"].unique()):
            comp_data = subset[subset["compression_type"] == comp]
            grouped = comp_data.groupby("cache_size")["ops_per_sec"].agg(["mean", "std"])
            x_labels = [_human_bytes(x) for x in grouped.index]
            ax.errorbar(
                x_labels, grouped["mean"], yerr=grouped["std"],
                marker="o", capsize=4, label=comp,
            )

        ax.set_xlabel("cache_size")
        ax.set_ylabel("ops/sec")
        ax.set_title(f"{workload}: ops/sec vs cache_size (256MB memory limit)")
        ax.legend(title="compression")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out_path = out_dir / f"plot_{workload}_ops_vs_cache_size.png"
        fig.savefig(out_path, dpi=150)
        print(f"Saved: {out_path}")
        plt.close(fig)


def plot_stalls_vs_cache_size(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot stall% vs cache_size — demonstrates write stalls under memory pressure."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    if "cache_size" not in df.columns:
        return

    # Use any write-producing workload (fillrandom, readwhilewriting, etc.)
    write_wl_names = {"fillrandom", "readwhilewriting", "overwrite"}
    write_workloads = df[df["workload"].isin(write_wl_names)]
    if write_workloads.empty or write_workloads["stall_percent"].isna().all():
        print("No stall data for write workloads, skipping stall plot.")
        return

    workload_name = write_workloads["workload"].iloc[0]
    fig, ax = plt.subplots(figsize=(10, 6))

    for wbs in sorted(write_workloads["write_buffer_size"].unique()):
        wbs_data = write_workloads[write_workloads["write_buffer_size"] == wbs]
        grouped = wbs_data.groupby("cache_size")["stall_percent"].mean()
        x_labels = [_human_bytes(x) for x in grouped.index]
        ax.plot(x_labels, grouped.values, marker="s", label=f"wbuf={_human_bytes(wbs)}")

    ax.set_xlabel("cache_size")
    ax.set_ylabel("Stall %")
    ax.set_title(f"{workload_name}: Stall % vs cache_size (256MB memory limit)")
    ax.legend(title="write_buffer_size")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out_path = out_dir / f"plot_{workload_name}_stalls_vs_cache_size.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


def plot_write_amp_heatmap(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot write amplification heatmap (write_buffer_size x cache_size)."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    if "cache_size" not in df.columns:
        return

    # Use any write-producing workload
    write_wl_names = {"fillrandom", "readwhilewriting", "overwrite"}
    write_data = df[df["workload"].isin(write_wl_names)]
    if write_data.empty or write_data["write_amp"].isna().all():
        print("No write_amp data for write workloads, skipping heatmap.")
        return

    workload_name = write_data["workload"].iloc[0]
    pivot = write_data.pivot_table(
        values="write_amp",
        index="write_buffer_size",
        columns="cache_size",
        aggfunc="mean",
    )

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(pivot.values, cmap="YlOrRd", aspect="auto")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([_human_bytes(c) for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([_human_bytes(i) for i in pivot.index])
    ax.set_xlabel("cache_size")
    ax.set_ylabel("write_buffer_size")
    ax.set_title(f"{workload_name}: Write Amplification (mean)")

    # Annotate cells
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        color="white" if val > pivot.values[~np.isnan(pivot.values)].mean() else "black",
                        fontsize=10)

    fig.colorbar(im, ax=ax, label="Write Amplification")
    plt.tight_layout()
    out_path = out_dir / f"plot_{workload_name}_write_amp_heatmap.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


def analyze_constrained() -> None:
    """Run constrained mode analysis."""
    df = load_results(CONSTRAINED_SUMMARY_CSV)
    out_dir = CONSTRAINED_SUMMARY_CSV.parent

    print_constrained_summary(df)
    plot_ops_vs_cache_size(df, out_dir)
    plot_stalls_vs_cache_size(df, out_dir)
    plot_write_amp_heatmap(df, out_dir)
    print("\nDone.")


# ── Rep-aware analysis (512MB and any multi-rep dataset) ─────────────────────

CONFIG_COLS = ["write_buffer_size", "cache_size", "compression_type",
               "min_level_to_compress", "level0_slowdown_writes_trigger"]


def _collapse_reps(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Collapse repetitions into per-config mean/std/count.

    Groups by CONFIG_COLS + workload, aggregates the given metric.
    Returns a DataFrame with columns: {metric}_mean, {metric}_std, n_reps, {metric}_ci95.
    """
    group_cols = [c for c in CONFIG_COLS + ["workload"] if c in df.columns]
    stats = df.groupby(group_cols)[metric].agg(["mean", "std", "count"]).reset_index()
    stats.columns = group_cols + [f"{metric}_mean", f"{metric}_std", "n_reps"]
    stats[f"{metric}_ci95"] = 1.96 * stats[f"{metric}_std"] / np.sqrt(stats["n_reps"])
    return stats


def print_variance_report(df: pd.DataFrame) -> None:
    """Print coefficient of variation (CV) per config to assess data quality."""
    ok = df[df["status"] == "ok"].copy()
    if ok.empty:
        print("No successful runs to analyze.")
        return

    stats = _collapse_reps(ok, "ops_per_sec")
    stats["cv"] = stats["ops_per_sec_std"] / stats["ops_per_sec_mean"]

    print("\n=== Variance Report (Coefficient of Variation) ===\n")
    print(f"Total configs: {len(stats)}")
    print(f"Mean reps per config: {stats['n_reps'].mean():.1f}")
    print(f"CV statistics: mean={stats['cv'].mean():.3f}, "
          f"median={stats['cv'].median():.3f}, "
          f"max={stats['cv'].max():.3f}")

    good = (stats["cv"] < 0.10).sum()
    ok_cv = ((stats["cv"] >= 0.10) & (stats["cv"] < 0.20)).sum()
    high = (stats["cv"] >= 0.20).sum()
    print(f"  CV < 10% (good):   {good}")
    print(f"  CV 10-20% (ok):    {ok_cv}")
    print(f"  CV > 20% (noisy):  {high}")

    if high > 0:
        print("\nNoisiest configs (CV > 20%):")
        noisy = stats[stats["cv"] >= 0.20].sort_values("cv", ascending=False)
        for _, row in noisy.head(10).iterrows():
            wbuf = _human_bytes(row["write_buffer_size"])
            cache = _human_bytes(row["cache_size"])
            comp = row["compression_type"]
            print(f"  {comp:6s} wbuf={wbuf:>5s} cache={cache:>5s}  "
                  f"CV={row['cv']:.3f}  mean={row['ops_per_sec_mean']:.0f}  "
                  f"std={row['ops_per_sec_std']:.0f}  n={row['n_reps']:.0f}")
    print()


def plot_ops_vs_cache_rep_aware(df: pd.DataFrame, out_dir: Path) -> None:
    """Plot ops/sec vs cache_size with proper per-config CI from repetitions."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    ok = df[df["status"] == "ok"].copy()
    if ok.empty or "cache_size" not in ok.columns:
        return

    for workload in ok["workload"].unique():
        wl_data = ok[ok["workload"] == workload]
        stats = _collapse_reps(wl_data, "ops_per_sec")

        fig, ax = plt.subplots(figsize=(10, 6))
        mem_limit = df["memory_limit_mb"].iloc[0] if "memory_limit_mb" in df.columns else "?"

        for comp in sorted(stats["compression_type"].unique()):
            comp_stats = stats[stats["compression_type"] == comp]
            # Average across write_buffer_sizes for each cache level
            cache_agg = comp_stats.groupby("cache_size").agg(
                ops_mean=("ops_per_sec_mean", "mean"),
                ops_ci=("ops_per_sec_ci95", lambda x: np.sqrt((x**2).sum()) / len(x)),
            ).reset_index()
            x_labels = [_human_bytes(x) for x in cache_agg["cache_size"]]
            ax.errorbar(
                x_labels, cache_agg["ops_mean"], yerr=cache_agg["ops_ci"],
                marker="o", capsize=4, label=comp,
            )

        ax.set_xlabel("cache_size")
        ax.set_ylabel("ops/sec (mean of per-config means)")
        ax.set_title(f"{workload}: ops/sec vs cache_size ({mem_limit}MB, 95% CI from reps)")
        ax.legend(title="compression")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out_path = out_dir / f"plot_{workload}_ops_vs_cache_size.png"
        fig.savefig(out_path, dpi=150)
        print(f"Saved: {out_path}")
        plt.close(fig)


def plot_ops_heatmap_rep_aware(df: pd.DataFrame, out_dir: Path) -> None:
    """Heatmap of ops/sec (wbuf × cache), averaged across compressions, with CI from reps."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    ok = df[df["status"] == "ok"].copy()
    if ok.empty:
        return

    for workload in ok["workload"].unique():
        wl_data = ok[ok["workload"] == workload]
        stats = _collapse_reps(wl_data, "ops_per_sec")
        mem_limit = df["memory_limit_mb"].iloc[0] if "memory_limit_mb" in df.columns else "?"

        # Average across compression types
        pivot = stats.pivot_table(
            values="ops_per_sec_mean",
            index="write_buffer_size",
            columns="cache_size",
            aggfunc="mean",
        )

        fig, ax = plt.subplots(figsize=(9, 6))
        im = ax.imshow(pivot.values, cmap="YlGn", aspect="auto")

        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([_human_bytes(c) for c in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([_human_bytes(i) for i in pivot.index])
        ax.set_xlabel("cache_size")
        ax.set_ylabel("write_buffer_size")
        ax.set_title(f"{workload}: ops/sec heatmap ({mem_limit}MB, mean across compressions)")

        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                if not np.isnan(val):
                    ax.text(j, i, f"{val:,.0f}", ha="center", va="center",
                            color="white" if val < pivot.values[~np.isnan(pivot.values)].mean()
                            else "black", fontsize=9)

        fig.colorbar(im, ax=ax, label="ops/sec")
        plt.tight_layout()
        out_path = out_dir / f"plot_{workload}_ops_heatmap.png"
        fig.savefig(out_path, dpi=150)
        print(f"Saved: {out_path}")
        plt.close(fig)


def plot_oom_matrix(df: pd.DataFrame, out_dir: Path) -> None:
    """Heatmap showing OOM rate per wbuf × cache combination."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    if "status" not in df.columns:
        return

    # Count OOM vs total for each wbuf × cache pair
    grouped = df.groupby(["write_buffer_size", "cache_size"]).agg(
        total=("status", "count"),
        oom=("status", lambda x: (x == "oom").sum()),
    ).reset_index()
    grouped["oom_rate"] = grouped["oom"] / grouped["total"]

    pivot = grouped.pivot_table(
        values="oom_rate", index="write_buffer_size", columns="cache_size",
    )
    mem_limit = df["memory_limit_mb"].iloc[0] if "memory_limit_mb" in df.columns else "?"

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(pivot.values, cmap="RdYlGn_r", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([_human_bytes(c) for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([_human_bytes(i) for i in pivot.index])
    ax.set_xlabel("cache_size")
    ax.set_ylabel("write_buffer_size")
    ax.set_title(f"OOM Rate by wbuf × cache ({mem_limit}MB)")

    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not np.isnan(val):
                label = f"{val:.0%}"
                ax.text(j, i, label, ha="center", va="center",
                        color="white" if val > 0.5 else "black", fontsize=11)

    fig.colorbar(im, ax=ax, label="OOM Rate")
    plt.tight_layout()
    out_path = out_dir / f"plot_oom_matrix_{mem_limit}mb.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


def analyze_512(csv_path: Path | None = None) -> None:
    """Run rep-aware analysis for 512MB (or any multi-rep constrained dataset)."""
    if csv_path is None:
        csv_path = PROJECT_ROOT / "results" / "constrained_512mb" / "parsed" / "summary.csv"

    df = load_results(csv_path)
    out_dir = csv_path.parent

    # Status summary
    if "status" in df.columns:
        status_counts = df["status"].value_counts()
        print(f"\nStatus: {dict(status_counts)}")
        print(f"Total runs: {len(df)}")

    # Variance report (data quality check)
    print_variance_report(df)

    # Constrained summary (reuse existing)
    ok = df[df["status"] == "ok"] if "status" in df.columns else df
    if not ok.empty:
        print_constrained_summary(ok)

    # Rep-aware plots
    plot_ops_vs_cache_rep_aware(df, out_dir)
    plot_ops_heatmap_rep_aware(df, out_dir)
    plot_stalls_vs_cache_size(ok, out_dir)
    plot_write_amp_heatmap(ok, out_dir)
    plot_oom_matrix(df, out_dir)

    print("\nDone.")


# ── Comparison analysis ──────────────────────────────────────────────────────

def analyze_comparison() -> None:
    """Side-by-side comparison of constrained vs unconstrained results."""
    plt = _try_import_matplotlib()
    if plt is None:
        return

    df_unc = load_results(SUMMARY_CSV)
    df_con = load_results(CONSTRAINED_SUMMARY_CSV)

    out_dir = CONSTRAINED_SUMMARY_CSV.parent

    # Find overlapping parameter space for fair comparison
    shared_workloads = set(df_unc["workload"].unique()) & set(df_con["workload"].unique())
    if not shared_workloads:
        print("No shared workloads between constrained and unconstrained results.")
        return

    print(f"\n=== Constrained vs Unconstrained Comparison ===")
    print(f"Shared workloads: {sorted(shared_workloads)}\n")

    for workload in sorted(shared_workloads):
        unc = df_unc[df_unc["workload"] == workload]
        con = df_con[df_con["workload"] == workload]

        print(f"--- {workload} ---")
        print(f"  Unconstrained: {len(unc)} runs, "
              f"mean ops/sec={unc['ops_per_sec'].mean():,.0f}")
        print(f"  Constrained:   {len(con)} runs, "
              f"mean ops/sec={con['ops_per_sec'].mean():,.0f}")

        # Compare by compression type
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Left: ops/sec boxplot comparison
        comp_types = sorted(
            set(unc["compression_type"].unique()) & set(con["compression_type"].unique())
        )
        if comp_types:
            unc_means = [unc[unc["compression_type"] == c]["ops_per_sec"].mean()
                         for c in comp_types]
            con_means = [con[con["compression_type"] == c]["ops_per_sec"].mean()
                         for c in comp_types]

            x = range(len(comp_types))
            width = 0.35
            axes[0].bar([i - width/2 for i in x], unc_means, width, label="Unconstrained")
            axes[0].bar([i + width/2 for i in x], con_means, width, label="Constrained (256MB)")
            axes[0].set_xticks(list(x))
            axes[0].set_xticklabels(comp_types)
            axes[0].set_ylabel("ops/sec (mean)")
            axes[0].set_title(f"{workload}: ops/sec by compression")
            axes[0].legend()
            axes[0].grid(True, alpha=0.3, axis="y")

        # Right: latency comparison
        for label, data, style in [("Unconstrained", unc, "o-"),
                                    ("Constrained", con, "s--")]:
            if not data["p99"].isna().all() and comp_types:
                p99_means = [data[data["compression_type"] == c]["p99"].mean()
                             for c in comp_types]
                axes[1].plot(comp_types, p99_means, style, label=label)

        axes[1].set_ylabel("P99 latency (us)")
        axes[1].set_title(f"{workload}: P99 latency by compression")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        out_path = out_dir / f"plot_{workload}_comparison.png"
        fig.savefig(out_path, dpi=150)
        print(f"  Saved: {out_path}")
        plt.close(fig)

    print("\nDone.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RocksDB benchmark analysis")
    parser.add_argument(
        "--constrained", action="store_true",
        help="Analyze constrained (Docker IoT) results",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Side-by-side comparison of constrained vs unconstrained",
    )
    parser.add_argument(
        "--constrained-512", action="store_true",
        help="Analyze 512MB constrained results (rep-aware variance analysis)",
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Override CSV path for analysis",
    )
    args = parser.parse_args()

    if args.compare:
        analyze_comparison()
    elif getattr(args, "constrained_512"):
        csv_path = Path(args.csv) if args.csv else None
        analyze_512(csv_path)
    elif args.constrained:
        analyze_constrained()
    else:
        analyze_unconstrained()


if __name__ == "__main__":
    main()
