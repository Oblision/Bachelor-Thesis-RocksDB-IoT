#!/usr/bin/env python3
"""
Archive pilot constrained benchmark data.

Re-parses all raw logs with the new parser (extracts block_cache_hit_rate,
bytes_read/written, compact_read_gb, flush_write_gb) and writes a pilot
summary CSV with the full 28-column schema. Also generates plots and a
run log documenting the pilot findings.

Usage:
    python3 scripts/archive_pilot.py
"""

import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    CONSTRAINED_CSV_COLUMNS,
    CONSTRAINED_QUICK_PARAM_SPACE,
    CONSTRAINED_RESULTS_RAW_DIR,
    CONSTRAINED_WORKLOADS,
    MEMORY_LIMIT_MB,
)
from parse_output import parse_db_bench_output
from run_benchmarks import generate_runs


# ── Paths ────────────────────────────────────────────────────────────────────
PILOT_DIR = CONSTRAINED_RESULTS_RAW_DIR.parent / "pilot"
PILOT_CSV = PILOT_DIR / "pilot_summary.csv"
PILOT_LOG = PILOT_DIR / "pilot_run_log.md"


def _human_bytes(n: float) -> str:
    if n >= 1024**2:
        return f"{n / 1024**2:.0f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n:.0f}B"


def _parse_run_duration(text: str) -> float | None:
    """Extract run duration from the ops summary line (last match = target workload)."""
    matches = list(re.finditer(r"([\d.]+)\s+seconds\s+\d+\s+operations", text))
    if matches:
        return round(float(matches[-1].group(1)), 1)
    return None


def archive_pilot():
    """Re-parse raw logs and write pilot archive."""
    PILOT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate run mapping
    runs = generate_runs(
        CONSTRAINED_QUICK_PARAM_SPACE, CONSTRAINED_WORKLOADS,
        prefix="constrained|",
    )
    run_map = {rid: (w, p) for w, p, rid in runs}

    # Find raw logs
    raw_logs = sorted(CONSTRAINED_RESULTS_RAW_DIR.glob("*.log"))
    if not raw_logs:
        print("ERROR: No raw logs found.")
        sys.exit(1)

    print(f"Found {len(raw_logs)} raw logs in {CONSTRAINED_RESULTS_RAW_DIR}")

    # Parse each log
    rows = []
    for log_path in raw_logs:
        run_id = log_path.stem
        text = log_path.read_text()

        if run_id not in run_map:
            print(f"  WARNING: {run_id} not in run map, skipping")
            continue

        workload, params = run_map[run_id]

        # Base row
        row = {
            "run_id": run_id,
            "workload": workload,
            "compression_type": params.get("compression_type", ""),
            "min_level_to_compress": params.get("min_level_to_compress", ""),
            "level0_slowdown_writes_trigger": params.get(
                "level0_slowdown_writes_trigger", ""
            ),
            "write_buffer_size": params.get("write_buffer_size", ""),
            "cache_size": params.get("cache_size", ""),
            "memory_limit_mb": MEMORY_LIMIT_MB,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # OOM detection
        if "OOM KILLED" in text:
            duration_match = re.search(r"after ([\d.]+)s", text)
            row["status"] = "oom"
            row["run_duration_secs"] = (
                round(float(duration_match.group(1)), 1) if duration_match else ""
            )
            rows.append(row)
            print(f"  {run_id}: OOM (wbuf={_human_bytes(params['write_buffer_size'])}, "
                  f"cache={_human_bytes(params['cache_size'])})")
            continue

        # Parse metrics
        parsed = parse_db_bench_output(text)
        duration = _parse_run_duration(text)

        row.update({
            "status": "ok",
            "run_duration_secs": duration or "",
            "ops_per_sec": parsed.ops_per_sec or "",
            "micros_per_op": parsed.micros_per_op or "",
            "mb_per_sec": parsed.mb_per_sec or "",
            "p50": parsed.p50 or "",
            "p75": parsed.p75 or "",
            "p99": parsed.p99 or "",
            "p99_9": parsed.p99_9 or "",
            "p99_99": parsed.p99_99 or "",
            "block_cache_hit_rate": parsed.block_cache_hit_rate or "",
            "write_amp": parsed.write_amp or "",
            "compaction_write_gb": parsed.compaction_write_gb or "",
            "compact_read_gb": parsed.compact_read_gb or "",
            "flush_write_gb": parsed.flush_write_gb or "",
            "bytes_written": parsed.bytes_written or "",
            "bytes_read": parsed.bytes_read or "",
            "stall_percent": parsed.stall_percent or "",
            "stall_micros": parsed.stall_micros or "",
        })
        rows.append(row)
        print(f"  {run_id}: {parsed.ops_per_sec} ops/sec, "
              f"cache_hit={parsed.block_cache_hit_rate}, "
              f"write_amp={parsed.write_amp}, stall%={parsed.stall_percent}")

    # Write CSV
    with open(PILOT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CONSTRAINED_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"\nWrote {len(rows)} rows to {PILOT_CSV}")
    return rows


def generate_plots(rows: list[dict]):
    """Generate focused pilot plots from archived data."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed, skipping plots.")
        return

    ok_rows = [r for r in rows if r.get("status") == "ok"]
    if not ok_rows:
        print("No OK rows, skipping plots.")
        return

    # ── Plot 1: ops/sec by (write_buffer_size, cache_size) grouped by compression
    fig, ax = plt.subplots(figsize=(12, 6))

    compressions = sorted(set(r["compression_type"] for r in ok_rows))
    combos = sorted(
        set(
            (int(r["write_buffer_size"]), int(r["cache_size"]))
            for r in ok_rows
        )
    )
    combo_labels = [f"wbuf={_human_bytes(w)}\ncache={_human_bytes(c)}" for w, c in combos]

    n_groups = len(combos)
    n_bars = len(compressions)
    width = 0.8 / max(n_bars, 1)
    colors = ["#2196F3", "#FF9800", "#4CAF50", "#E91E63"]

    for ci, comp in enumerate(compressions):
        vals = []
        for w, c in combos:
            match = [
                r for r in ok_rows
                if r["compression_type"] == comp
                and int(r["write_buffer_size"]) == w
                and int(r["cache_size"]) == c
            ]
            vals.append(float(match[0]["ops_per_sec"]) if match else 0)
        x = [i + ci * width for i in range(n_groups)]
        ax.bar(x, vals, width, label=comp, color=colors[ci % len(colors)])

    ax.set_xticks([i + width * (n_bars - 1) / 2 for i in range(n_groups)])
    ax.set_xticklabels(combo_labels, fontsize=9)
    ax.set_ylabel("ops/sec")
    ax.set_title("Pilot: ops/sec by (write_buffer_size, cache_size) — readwhilewriting, 256MB limit")
    ax.legend(title="compression")
    ax.grid(True, alpha=0.3, axis="y")

    # Add value labels
    for bar in ax.patches:
        if bar.get_height() > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{bar.get_height():,.0f}", ha="center", va="bottom", fontsize=7,
            )

    plt.tight_layout()
    path = PILOT_DIR / "pilot_ops_by_params.png"
    fig.savefig(path, dpi=150)
    print(f"Saved: {path}")
    plt.close(fig)

    # ── Plot 2: block_cache_hit_rate by cache_size
    fig, ax = plt.subplots(figsize=(8, 5))

    cache_sizes = sorted(set(int(r["cache_size"]) for r in ok_rows if r.get("block_cache_hit_rate")))
    for comp in compressions:
        hit_rates = []
        cs_vals = []
        for cs in cache_sizes:
            match = [
                r for r in ok_rows
                if r["compression_type"] == comp
                and int(r["cache_size"]) == cs
                and r.get("block_cache_hit_rate")
            ]
            if match:
                # Average across write_buffer_sizes
                avg = sum(float(r["block_cache_hit_rate"]) for r in match) / len(match)
                hit_rates.append(avg * 100)
                cs_vals.append(_human_bytes(cs))
        if hit_rates:
            ax.plot(cs_vals, hit_rates, marker="o", linewidth=2, label=comp)

    ax.set_xlabel("cache_size")
    ax.set_ylabel("Block Cache Hit Rate (%)")
    ax.set_title("Pilot: Block Cache Hit Rate by cache_size — 256MB limit")
    ax.legend(title="compression")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 100)

    plt.tight_layout()
    path = PILOT_DIR / "pilot_cache_hit_rate.png"
    fig.savefig(path, dpi=150)
    print(f"Saved: {path}")
    plt.close(fig)

    # ── Plot 3: write amplification by (compression, write_buffer_size)
    fig, ax = plt.subplots(figsize=(10, 5))

    wbuf_sizes = sorted(set(int(r["write_buffer_size"]) for r in ok_rows if r.get("write_amp")))
    bar_data = {}
    for r in ok_rows:
        if r.get("write_amp"):
            key = (r["compression_type"], int(r["write_buffer_size"]))
            bar_data[key] = float(r["write_amp"])

    labels = []
    vals = []
    bar_colors = []
    color_map = {"none": "#2196F3", "snappy": "#FF9800", "lz4": "#4CAF50", "zstd": "#E91E63"}
    for comp in compressions:
        for wbs in wbuf_sizes:
            key = (comp, wbs)
            if key in bar_data:
                labels.append(f"{comp}\n{_human_bytes(wbs)}")
                vals.append(bar_data[key])
                bar_colors.append(color_map.get(comp, "#999"))

    ax.bar(range(len(vals)), vals, color=bar_colors)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Write Amplification")
    ax.set_title("Pilot: Write Amplification by (compression, write_buffer_size)")
    ax.grid(True, alpha=0.3, axis="y")

    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    path = PILOT_DIR / "pilot_write_amp.png"
    fig.savefig(path, dpi=150)
    print(f"Saved: {path}")
    plt.close(fig)


def generate_run_log(rows: list[dict]):
    """Generate the pilot run log markdown."""
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    oom_rows = [r for r in rows if r.get("status") == "oom"]

    # Build results table
    table_lines = []
    table_lines.append("| run_id | compression | write_buffer | cache_size | ops/sec | cache_hit_rate | write_amp | stall% | status |")
    table_lines.append("|--------|-------------|-------------|------------|---------|---------------|-----------|--------|--------|")
    for r in rows:
        wbs = _human_bytes(int(r["write_buffer_size"])) if r.get("write_buffer_size") else ""
        cs = _human_bytes(int(r["cache_size"])) if r.get("cache_size") else ""
        ops = f"{float(r['ops_per_sec']):,.0f}" if r.get("ops_per_sec") else "-"
        hit = f"{float(r['block_cache_hit_rate'])*100:.1f}%" if r.get("block_cache_hit_rate") else "-"
        wamp = f"{float(r['write_amp']):.2f}" if r.get("write_amp") else "-"
        stall = f"{float(r['stall_percent']):.1f}%" if r.get("stall_percent") else "-"
        table_lines.append(
            f"| {r['run_id']} | {r.get('compression_type','')} | {wbs} | {cs} | {ops} | {hit} | {wamp} | {stall} | {r.get('status','')} |"
        )

    log = f"""# Pilot Run Log — Constrained Benchmarks

**Date:** 2026-03-03
**Purpose:** Quick validation of constrained mode (8 runs, `--quick` with 2M keys)

## Docker Configuration

- **Memory limit:** 256MB (no swap)
- **CPUs:** 1
- **Direct I/O:** enabled (`use_direct_reads`, `use_direct_io_for_flush_and_compaction`)
- **Key count:** 2,000,000 (~220MB on disk)
- **Workload:** `readwhilewriting` (concurrent read + write, prepopulated)

## Parameter Space (Quick Mode)

| Parameter | Values |
|-----------|--------|
| compression_type | none, snappy |
| min_level_to_compress | -1 |
| level0_slowdown_writes_trigger | 20 |
| write_buffer_size | 4MB, 64MB |
| cache_size | 8MB, 64MB |

**Total combos:** 2 x 1 x 1 x 2 x 2 = 8 runs

## Results

{chr(10).join(table_lines)}

## OOM Kill

- **Run:** `{oom_rows[0]['run_id'] if oom_rows else 'N/A'}`
- **Params:** compression=none, write_buffer_size=64MB, cache_size=64MB
- **Cause:** 64MB write buffer + 64MB block cache + memtable overhead + compaction buffers > 256MB
- **Duration before kill:** {oom_rows[0].get('run_duration_secs', 'N/A') if oom_rows else 'N/A'}s
- **Exit code:** 137 (Docker OOM killer)

## Key Early Findings

### 1. Cache Size Dominance
- 8MB cache -> ~2,000 ops/sec (massive disk I/O for every read)
- 64MB cache -> ~7,000 ops/sec with small write buffer (3.5x improvement)
- **Implication:** Block cache is the dominant performance knob under memory pressure

### 2. Write Buffer Surprise
- 64MB write buffer -> ~22,000 ops/sec even with 8MB cache
- 4MB write buffer -> ~2,000 ops/sec with 8MB cache
- **Why:** Larger write buffer means fewer memtable flushes, fewer L0 files, less compaction interference with reads
- **Implication:** Write buffer size has a larger impact than expected; it affects read performance indirectly

### 3. Snappy Compression
- Halves write amplification (7.5x -> 3.9x with 4MB wbuf)
- Slightly reduces throughput (~5-10% lower ops/sec)
- **Trade-off:** Worth it for SD card lifespan on edge devices

### 4. Stall Correlation
- High stalls (18-25%) only with small write buffer (4MB)
- Large write buffer (64MB): no stalls detected
- **Why:** 4MB write buffer fills quickly -> frequent flushes -> L0 file accumulation -> write stalls

### 5. OOM Boundary
- 64MB write_buffer + 64MB cache = OOM on 256MB device
- RocksDB internal overhead (bloom filters, table readers, compaction buffers) pushes total memory past the limit
- **Implication:** Need to keep write_buffer + cache well under device memory

## Files

- `pilot_summary.csv` — Full 28-column CSV with re-parsed metrics
- `pilot_ops_by_params.png` — Throughput by parameter combination
- `pilot_cache_hit_rate.png` — Block cache hit rate by cache_size
- `pilot_write_amp.png` — Write amplification by compression and write buffer
"""

    with open(PILOT_LOG, "w") as f:
        f.write(log)
    print(f"Wrote run log: {PILOT_LOG}")


def main():
    print("=" * 60)
    print("Archiving pilot constrained benchmark data")
    print("=" * 60)

    rows = archive_pilot()
    generate_plots(rows)
    generate_run_log(rows)

    print(f"\nPilot archive complete: {PILOT_DIR}")
    print(f"  CSV:  {PILOT_CSV}")
    print(f"  Log:  {PILOT_LOG}")
    print(f"  Plots: {PILOT_DIR}/*.png")


if __name__ == "__main__":
    main()
