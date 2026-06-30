"""Loader for the lag-validation result TSVs (34 columns, no header).

Column schema per run-cell.sh printf (1-indexed in the shell, 0-indexed here).
Every figure script imports from this module so the schema lives in one place.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3] / "nebulastream-Out-of-Core-Stream-Processing"
LAGVAL = REPO / ".planning/eval-phase/lag-validation"
RESULTS = LAGVAL / "results"
TRACES = LAGVAL / "traces"
IMG_OUT = HERE.parents[1] / "img"

COLUMNS = [
    "label", "tuples", "window", "emit_rate", "emit_lag", "n_w", "cap", "spill",
    "rc", "status", "elapsed_s", "peak_mb", "spill_dir_mb", "store_peak_b", "slices",
    "cpu_usec", "io_r_b", "io_w_b", "lat_p50_us", "lat_p95_us", "lat_p99_us", "tps",
    "peak_anon_mb", "steady_anon_mb", "flush_anon_mb", "peak_frac_pct",
    "peak_file_mb", "peak_shmem_mb", "peak_rssanon_mb", "pool_mb", "rocksdb_rss_mb",
    "pool_used_peak_mb", "pool_used_final_mb", "unpooled_peak_mb",
]

NUMERIC = [c for c in COLUMNS if c not in ("label", "cap", "spill", "rc", "status")]

_POOL_RE = re.compile(r"_p(\d+)(k?)(?=_|$)")
_WORKLOAD_RE = re.compile(r"_r(\d{2,4})(?=_r\d+$|$)")
_REP_RE = re.compile(r"_r(\d+)$")


def _parse_pool(cell: str) -> float:
    """Pool size in buffers from a label fragment like p16k / p16000."""
    m = _POOL_RE.search(cell)
    if not m:
        return float("nan")
    value = int(m.group(1))
    return float(value * 1000) if m.group(2) == "k" else float(value)


def load_runs() -> pd.DataFrame:
    """All result rows with parsed label fields added."""
    rows = []
    for path in sorted(RESULTS.glob("*.tsv")):
        fields = path.read_text().rstrip("\n").split("\t")
        if len(fields) != len(COLUMNS):  # only full-schema rows; older variants misalign
            continue
        rows.append(dict(zip(COLUMNS, fields + [""] * (len(COLUMNS) - len(fields)))))
    df = pd.DataFrame(rows)
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["cap_mb"] = pd.to_numeric(df["cap"], errors="coerce")  # NaN for "none"
    df["ok"] = df["status"] == "ok"
    df["campaign"] = df["label"].str.split("_").str[0]
    df["cell"] = df["label"].str.replace(_REP_RE, "", regex=True)
    df["workload"] = df["cell"].apply(
        lambda s: (m := _WORKLOAD_RE.search(s)) and int(m.group(1)) or None
    )
    df["pool_buffers"] = df["cell"].apply(_parse_pool)
    df["arm"] = df["cell"].apply(_classify_arm)
    # NOTE: the 32/32 backend-size runs (label *_b32_*) and the drift control
    # (D16_/D32_) are intentionally retained. The cap x pool x workload
    # feasibility / operating-window figures pool them with the 16/8 baseline as
    # additional repetitions (the gap-fill that completes those grids); the 32/32
    # configuration is also analysed on its own in Section~ref{sec:rb32} /
    # Appendix E. Do not filter them out here without revisiting that decision.
    return df


def _classify_arm(cell: str) -> str:
    """Coarse architecture arm of a cell label."""
    if "_inm" in cell or cell.startswith(("inm", "I8")):
        return "inmem"
    if "_unc" in cell or cell.startswith("unc"):
        return "uncapped"
    if cell.startswith(("W_", "N", "U8")):
        return "bounded"      # pooled architecture (operator buffer 8192)
    if cell.startswith("U_"):
        return "heap8128"     # matched-granularity heap control
    if cell.startswith(("L_", "b_", "bt_", "p8k", "S_", "V", "B3", "F_", "P_", "X")):
        return "heap"         # engine default 4096 buffers, unbounded allocation
    return "other"


def rho_table(df: pd.DataFrame) -> dict[int, float]:
    """Uncapped steady anon footprint (MB) per workload, for rho = footprint / cap.

    Priority per workload (mirrors the threats-to-validity statement that rho for the
    largest workloads is derived from in-memory footprints at 2048 MB):
      1. L_unc uncapped runs, 2. L in-memory at 2048 MB, 3. flagship b_/bt_ uncapped.
    """
    measured = df[df["workload"].notna() & (df["steady_anon_mb"] > 50)]
    ok = measured[measured["ok"]]
    sources = [  # highest priority first; ok runs before err fallbacks
        ok[ok["campaign"] == "unc"],  # dedicated footprint-calibration runs
        measured[(measured["campaign"] == "L") & (measured["arm"] == "uncapped") & measured["ok"]],
        ok[(ok["campaign"] == "L") & (ok["arm"] == "inmem") & (ok["cap_mb"] == 2048)],
        ok[ok["campaign"].isin(["b", "bt"]) & (ok["arm"] == "uncapped")],
        # uncapped arms cannot OOM; a timeout-"err" run's steady-state (20-80% of
        # runtime) footprint is still a valid sample — last-resort fallback only
        measured[measured["campaign"].isin(["b", "bt"]) & (measured["arm"] == "uncapped")],
    ]
    table: dict[int, float] = {}
    for source in sources:
        for workload, value in source.groupby("workload")["steady_anon_mb"].median().items():
            table.setdefault(int(workload), float(value))
    return table


def per_cell(df: pd.DataFrame) -> pd.DataFrame:
    """One row per cell: reps, completions, medians over ok reps."""
    ok_rows = df[df["ok"]]
    agg = df.groupby("cell").agg(
        reps=("ok", "size"), n_ok=("ok", "sum"),
        campaign=("campaign", "first"), arm=("arm", "first"),
        cap_mb=("cap_mb", "first"), workload=("workload", "first"),
        pool_buffers=("pool_buffers", "first"),
    )
    med = ok_rows.groupby("cell")[
        ["tps", "lat_p50_us", "lat_p95_us", "lat_p99_us", "elapsed_s",
         "cpu_usec", "io_r_b", "io_w_b", "peak_anon_mb", "steady_anon_mb",
         "spill_dir_mb", "slices"]
    ].median().add_suffix("_med")
    return agg.join(med).reset_index()


def fisher_one_sided(ok_a: int, n_a: int, ok_b: int, n_b: int) -> float:
    """One-sided Fisher's exact test that arm A completes more often than arm B."""
    successes, total, draws = ok_a + ok_b, n_a + n_b, n_a
    p = 0.0
    for k in range(ok_a, min(draws, successes) + 1):
        p += (math.comb(successes, k) * math.comb(total - successes, draws - k)
              / math.comb(total, draws))
    return p
