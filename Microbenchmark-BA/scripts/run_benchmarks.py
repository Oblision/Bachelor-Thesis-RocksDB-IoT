#!/usr/bin/env python3
"""
RocksDB microbenchmark orchestrator.

Sweeps the parameter space defined in config.py, runs db_bench for each
combination, parses results, and writes structured CSV output.

Usage:
    python3 scripts/run_benchmarks.py                        # Full native sweep
    python3 scripts/run_benchmarks.py --quick                 # Reduced parameter space
    python3 scripts/run_benchmarks.py --dry-run               # Print planned runs
    python3 scripts/run_benchmarks.py --resume                # Skip completed runs
    python3 scripts/run_benchmarks.py --workloads fillrandom readrandom
    python3 scripts/run_benchmarks.py --constrained --quick   # IoT Docker mode (quick)
    python3 scripts/run_benchmarks.py --constrained           # Full constrained sweep
"""

import argparse
import csv
import hashlib
import itertools
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from config import (
    ALL_WORKLOADS,
    CONSTRAINED_CSV_COLUMNS,
    CONSTRAINED_EXTENDED_EXTRA_COMBOS,
    CONSTRAINED_EXTENDED_FIXED_OVERRIDE,
    CONSTRAINED_EXTENDED_PARAM_SPACE,
    CONSTRAINED_EXTENDED_REPS,
    CONSTRAINED_EXTENDED_WORKLOADS,
    CONSTRAINED_FIXED_PARAMS,
    CONSTRAINED_OVERNIGHT_FIXED_OVERRIDE,
    CONSTRAINED_OVERNIGHT_PARAM_SPACE,
    CONSTRAINED_PARAM_SPACE,
    CONSTRAINED_QUICK_FIXED_OVERRIDE,
    CONSTRAINED_QUICK_PARAM_SPACE,
    CONSTRAINED_RESULTS_RAW_DIR,
    CONSTRAINED_RUN_TIMEOUT_SECONDS,
    CONSTRAINED_SUMMARY_CSV,
    CONSTRAINED_WORKLOADS,
    CONSTRAINED_512_FIXED_OVERRIDE,
    CONSTRAINED_512_PARAM_SPACE,
    CONSTRAINED_512_QUICK_EXTRA_COMBOS,
    CONSTRAINED_512_QUICK_PARAM_SPACE,
    CONSTRAINED_512_REPS,
    CONSTRAINED_512_WORKLOADS,
    CSV_COLUMNS,
    DB_BENCH_BIN,
    DB_TEMP_DIR,
    DOCKER_DB_PATH,
    DOCKER_IMAGE_NAME,
    FIXED_PARAMS,
    MEMORY_LIMIT_MB,
    NEEDS_PREPOPULATE,
    PARAM_SPACE,
    PROJECT_ROOT,
    QUICK_PARAM_SPACE,
    QUICK_WORKLOADS,
    RESULTS_PARSED_DIR,
    RESULTS_RAW_DIR,
    RUN_TIMEOUT_SECONDS,
    SUMMARY_CSV,
)
from parse_output import parse_db_bench_output


def make_run_id(workload: str, params: dict, prefix: str = "") -> str:
    """Generate a deterministic run ID from workload + parameters."""
    key = f"{prefix}{workload}|" + "|".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def build_db_bench_cmd(
    workload: str, params: dict, db_path: Path, prepopulate: bool = False
) -> list[str]:
    """Construct the db_bench command line."""
    cmd = [str(DB_BENCH_BIN)]

    # Fixed parameters
    for key, value in FIXED_PARAMS.items():
        if value == "":  # Flag-only parameter (no value)
            cmd.append(f"--{key}")
        else:
            cmd.append(f"--{key}={value}")

    # Sweep parameters
    for key, value in params.items():
        cmd.append(f"--{key}={value}")

    # DB path
    cmd.append(f"--db={db_path}")

    # Benchmarks to run
    if prepopulate:
        # First populate with fillrandom, then run the target workload
        cmd.append(f"--benchmarks=fillrandom,{workload}")
    else:
        cmd.append(f"--benchmarks={workload}")

    return cmd


def build_docker_cmd(
    workload: str,
    sweep_params: dict,
    fixed_params: dict,
    memory_limit_mb: int,
    prepopulate: bool = False,
) -> list[str]:
    """Construct a Docker command to run db_bench in a memory-constrained container."""
    cmd = [
        "docker", "run", "--rm",
        f"--memory={memory_limit_mb}m",
        f"--memory-swap={memory_limit_mb}m",  # Disable swap
        "--cpus=1",                            # Simulate single-core IoT
        DOCKER_IMAGE_NAME,
    ]

    # Fixed parameters
    for key, value in fixed_params.items():
        if value == "":  # Flag-only parameter
            cmd.append(f"--{key}")
        else:
            cmd.append(f"--{key}={value}")

    # Sweep parameters
    for key, value in sweep_params.items():
        cmd.append(f"--{key}={value}")

    # DB path inside container
    cmd.append(f"--db={DOCKER_DB_PATH}")

    # Benchmarks to run
    if prepopulate:
        cmd.append(f"--benchmarks=fillrandom,{workload}")
    else:
        cmd.append(f"--benchmarks={workload}")

    return cmd


def check_docker_image() -> bool:
    """Check if the Docker image exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE_NAME],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_completed_run_ids(csv_path: Path) -> set[str]:
    """Read existing CSV to find already-completed run IDs."""
    if not csv_path.exists():
        return set()
    completed = set()
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            completed.add(row["run_id"])
    return completed


def ensure_csv_header(csv_path: Path) -> None:
    """Create CSV file with header if it doesn't exist."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)


def append_csv_row(csv_path: Path, row: dict) -> None:
    """Append a single row to the CSV file."""
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writerow(row)


def run_single_benchmark(
    workload: str, params: dict, run_id: str, dry_run: bool = False
) -> bool:
    """Execute a single db_bench run. Returns True on success."""
    db_path = DB_TEMP_DIR / run_id
    needs_prepopulate = workload in NEEDS_PREPOPULATE
    cmd = build_db_bench_cmd(workload, params, db_path, prepopulate=needs_prepopulate)

    param_str = ", ".join(f"{k}={v}" for k, v in params.items())
    print(f"\n{'='*70}")
    print(f"Run: {run_id}")
    print(f"Workload: {workload} {'(with prepopulate)' if needs_prepopulate else ''}")
    print(f"Params: {param_str}")
    print(f"Command: {' '.join(cmd)}")

    if dry_run:
        print("[DRY RUN] Skipping execution.")
        return True

    # Clean up any previous temp DB
    if db_path.exists():
        shutil.rmtree(db_path)
    db_path.mkdir(parents=True, exist_ok=True)

    # Ensure raw output directory exists
    RESULTS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_log_path = RESULTS_RAW_DIR / f"{run_id}.log"

    try:
        start_time = time.monotonic()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - start_time

        # Combine stdout and stderr (db_bench writes stats to both)
        output = result.stdout + "\n" + result.stderr

        # Save raw log
        with open(raw_log_path, "w") as f:
            f.write(output)

        if result.returncode != 0:
            print(f"ERROR: db_bench exited with code {result.returncode}")
            print(f"Raw log saved to: {raw_log_path}")
            return False

        print(f"Completed in {elapsed:.1f}s")

        # Parse output
        parsed = parse_db_bench_output(output)

        # Write CSV row
        row = {
            "run_id": run_id,
            "workload": workload,
            "compression_type": params.get("compression_type", ""),
            "min_level_to_compress": params.get("min_level_to_compress", ""),
            "level0_slowdown_writes_trigger": params.get(
                "level0_slowdown_writes_trigger", ""
            ),
            "write_buffer_size": params.get("write_buffer_size", ""),
            "ops_per_sec": parsed.ops_per_sec or "",
            "micros_per_op": parsed.micros_per_op or "",
            "mb_per_sec": parsed.mb_per_sec or "",
            "p50": parsed.p50 or "",
            "p75": parsed.p75 or "",
            "p99": parsed.p99 or "",
            "p99_9": parsed.p99_9 or "",
            "p99_99": parsed.p99_99 or "",
            "write_amp": parsed.write_amp or "",
            "compaction_write_gb": parsed.compaction_write_gb or "",
            "stall_percent": parsed.stall_percent or "",
            "stall_micros": parsed.stall_micros or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        append_csv_row(SUMMARY_CSV, row)

        print(f"  ops/sec: {parsed.ops_per_sec}, micros/op: {parsed.micros_per_op}")
        print(f"  P50: {parsed.p50}, P99: {parsed.p99}, P99.99: {parsed.p99_99}")
        return True

    except subprocess.TimeoutExpired:
        print(f"ERROR: Timed out after {RUN_TIMEOUT_SECONDS}s")
        with open(raw_log_path, "w") as f:
            f.write(f"TIMEOUT after {RUN_TIMEOUT_SECONDS}s\n")
        return False

    except Exception as e:
        print(f"ERROR: {e}")
        return False

    finally:
        # Clean up temp DB
        if db_path.exists():
            shutil.rmtree(db_path, ignore_errors=True)


def run_single_constrained_benchmark(
    workload: str,
    params: dict,
    run_id: str,
    fixed_params: dict,
    memory_limit_mb: int,
    timeout: int,
    raw_dir: Path,
    csv_path: Path,
    csv_columns: list[str],
    dry_run: bool = False,
    sweep_state: dict | None = None,
    csv_lock: threading.Lock | None = None,
    print_lock: threading.Lock | None = None,
    rep: int = 1,
) -> bool:
    """Execute a single constrained db_bench run via Docker. Returns True on success.

    sweep_state keys (all optional via None):
        sweep_start, secs_per_unit, run_cost, total_cost,
        completed_cost, run_index, total_runs
    """
    parallel = print_lock is not None
    needs_prepopulate = workload in NEEDS_PREPOPULATE
    cmd = build_docker_cmd(
        workload, params, fixed_params, memory_limit_mb,
        prepopulate=needs_prepopulate,
    )

    if not parallel:
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"\n{'='*70}")
        if sweep_state:
            ss = sweep_state
            elapsed_total = time.monotonic() - ss["sweep_start"]
            remaining = ss["secs_per_unit"] * (ss["total_cost"] - ss["completed_cost"])
            est_run = ss["secs_per_unit"] * ss["run_cost"]
            hdr = (f"[{ss['run_index']}/{ss['total_runs']}] elapsed: {_fmt_duration(elapsed_total)}"
                   f" | ETA: ~{_fmt_duration(remaining)} remaining"
                   f" | est. run: ~{_fmt_duration(est_run)}")
            print(hdr)
        print(f"Run: {run_id} [constrained, {memory_limit_mb}MB]")
        print(f"Workload: {workload} {'(with prepopulate)' if needs_prepopulate else ''}")
        print(f"Params: {param_str}")

    if dry_run:
        if not parallel:
            print("[DRY RUN] Skipping execution.")
        return True

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = raw_dir / f"{run_id}.log"

    try:
        start_time = time.monotonic()

        # Capture output (stream progress only in sequential mode)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        output_lines = []
        last_progress = ""
        phase = "prepopulate" if needs_prepopulate else workload
        max_ops = 0
        phase_switched = False
        for line in proc.stdout:
            output_lines.append(line)
            if parallel:
                continue  # just collect output, no live progress
            # Detect phase switch via benchmark result line (e.g. "fillrandom :")
            if needs_prepopulate and not phase_switched:
                if "fillrandom" in line and "micros/op" in line:
                    print()  # newline before phase switch
                    phase = workload
                    phase_switched = True
                    max_ops = 0
                    continue
            # Show db_bench progress lines (... finished N ops)
            if "finished" in line and "ops" in line:
                elapsed_so_far = time.monotonic() - start_time
                ops_text = line.strip().replace("... finished ", "").replace("                              ", "")
                try:
                    current_ops = int(ops_text.split()[0])
                except (ValueError, IndexError):
                    current_ops = 0
                if current_ops >= max_ops:
                    max_ops = current_ops
                    time_part = f"{_fmt_duration(elapsed_so_far)}"
                    if sweep_state and sweep_state["secs_per_unit"] > 0:
                        est_run = sweep_state["secs_per_unit"] * sweep_state["run_cost"]
                        sweep_el = time.monotonic() - sweep_state["sweep_start"]
                        time_part += f"/~{_fmt_duration(est_run)}"
                        time_part += f" | sweep: {_fmt_duration(sweep_el)}"
                    print(f"\r  [{time_part}] [{phase}] {ops_text}", end="", flush=True)
                    last_progress = ops_text
        if last_progress and not parallel:
            print()  # newline after last progress update

        proc.wait(timeout=timeout)
        elapsed = time.monotonic() - start_time
        output = "".join(output_lines)

        # Save raw log
        with open(raw_log_path, "w") as f:
            f.write(output)

        # Base row with params (used for both success and failure)
        base_row = {
            "run_id": run_id,
            "workload": workload,
            "rep": rep,
            "compression_type": params.get("compression_type", ""),
            "min_level_to_compress": params.get("min_level_to_compress", ""),
            "level0_slowdown_writes_trigger": params.get(
                "level0_slowdown_writes_trigger", ""
            ),
            "write_buffer_size": params.get("write_buffer_size", ""),
            "cache_size": params.get("cache_size", ""),
            "memory_limit_mb": memory_limit_mb,
            "run_duration_secs": round(elapsed, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        def _write_row(row):
            if csv_lock:
                csv_lock.acquire()
            try:
                with open(csv_path, "a", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=csv_columns)
                    writer.writerow(row)
            finally:
                if csv_lock:
                    csv_lock.release()

        # OOM kill detection (Docker kills with exit code 137)
        if proc.returncode == 137:
            if not parallel:
                print(f"OOM KILLED (exit 137) after {elapsed:.1f}s")
            with open(raw_log_path, "a") as f:
                f.write(f"\n--- OOM KILLED (exit 137) after {elapsed:.1f}s ---\n")
            _write_row({**base_row, "status": "oom"})
            return False

        if proc.returncode != 0:
            if not parallel:
                print(f"ERROR: db_bench exited with code {proc.returncode}")
                print(f"Raw log saved to: {raw_log_path}")
            _write_row({**base_row, "status": "error"})
            return False

        if not parallel:
            print(f"Completed in {elapsed:.1f}s")

        # Parse output
        parsed = parse_db_bench_output(output)

        # Write CSV row with full metrics
        row = {
            **base_row,
            "status": "ok",
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
        }
        _write_row(row)

        if not parallel:
            print(f"  ops/sec: {parsed.ops_per_sec}, micros/op: {parsed.micros_per_op}")
            print(f"  P50: {parsed.p50}, P99: {parsed.p99}, P99.99: {parsed.p99_99}")
            print(f"  cache_hit_rate: {parsed.block_cache_hit_rate}")
            if parsed.write_amp:
                print(f"  write_amp: {parsed.write_amp}, stall%: {parsed.stall_percent}")
        return True

    except subprocess.TimeoutExpired:
        timeout_elapsed = time.monotonic() - start_time
        if not parallel:
            print(f"ERROR: Timed out after {timeout}s")
        with open(raw_log_path, "w") as f:
            f.write(f"TIMEOUT after {timeout}s\n")
        # Write timeout row to CSV
        timeout_row = {
            "run_id": run_id,
            "workload": workload,
            "rep": rep,
            "status": "timeout",
            "run_duration_secs": round(timeout_elapsed, 1),
            "compression_type": params.get("compression_type", ""),
            "min_level_to_compress": params.get("min_level_to_compress", ""),
            "level0_slowdown_writes_trigger": params.get(
                "level0_slowdown_writes_trigger", ""
            ),
            "write_buffer_size": params.get("write_buffer_size", ""),
            "cache_size": params.get("cache_size", ""),
            "memory_limit_mb": memory_limit_mb,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_row(timeout_row)
        return False

    except Exception as e:
        if not parallel:
            print(f"ERROR: {e}")
        return False


def _fmt_duration(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g., '1h 5m')."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


# Heuristic relative-cost weights per parameter value for ETA estimation.
# Multiplied together to produce a unitless "cost" per run.
_COST_WEIGHTS = {
    "cache_size": {
        8 * 1024 * 1024: 4.0,      # 8MB — heavy disk I/O
        16 * 1024 * 1024: 3.0,     # 16MB
        32 * 1024 * 1024: 2.0,     # 32MB
        64 * 1024 * 1024: 1.5,     # 64MB
        128 * 1024 * 1024: 1.0,    # 128MB — most fits in cache
        256 * 1024 * 1024: 0.8,    # 256MB — nearly all fits in cache
    },
    "write_buffer_size": {
        4 * 1024 * 1024: 1.3,      # 4MB — frequent flushes
        16 * 1024 * 1024: 1.1,     # 16MB
        32 * 1024 * 1024: 1.0,     # 32MB
        64 * 1024 * 1024: 1.0,     # 64MB — fewer flushes
        128 * 1024 * 1024: 0.5,    # 128MB — likely OOM (fast exit)
    },
    "compression_type": {
        "none": 1.0,
        "snappy": 1.1,
        "lz4": 1.1,
        "zstd": 1.3,
    },
}


# Heuristic baseline: ~420 seconds per cost unit for 10M keys in Docker.
# Used for ETA before any runs complete; replaced by calibrated data after run 1.
_BASELINE_SECS_PER_COST_UNIT_10M = 420


def _estimate_run_cost(params: dict, workload: str) -> float:
    """Estimate relative cost of a run based on its parameters."""
    cost = 1.0
    for param, weights in _COST_WEIGHTS.items():
        val = params.get(param)
        if val is not None and val in weights:
            cost *= weights[val]
    if workload in NEEDS_PREPOPULATE:
        cost *= 2.0
    return cost


def generate_runs(
    param_space: dict,
    workloads: list[str],
    prefix: str = "",
    reps: int = 1,
    extra_combos: list[dict] | None = None,
) -> list[tuple[str, dict, str, int]]:
    """Generate all (workload, params, run_id, rep) tuples with optional repetitions."""
    keys = list(param_space.keys())
    values = list(param_space.values())
    runs = []

    # Standard grid combos
    all_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    # Append extra combos (e.g. wbuf=4MB + cache=128MB)
    if extra_combos:
        all_combos.extend(extra_combos)

    for rep in range(1, reps + 1):
        for params in all_combos:
            for workload in workloads:
                rep_prefix = f"{prefix}rep{rep}|" if reps > 1 else prefix
                run_id = make_run_id(workload, params, prefix=rep_prefix)
                runs.append((workload, params, run_id, rep))

    return runs


def run_constrained(args) -> None:
    """Run benchmarks in memory-constrained Docker mode."""
    # Check Docker image
    if not args.dry_run and not check_docker_image():
        print(f"ERROR: Docker image '{DOCKER_IMAGE_NAME}' not found.")
        print("Build it first: bash scripts/build_docker.sh")
        sys.exit(1)

    # Determine memory limit
    memory_limit_mb = args.memory_limit or MEMORY_LIMIT_MB

    # Select parameter space and build fixed params
    if args.quick and memory_limit_mb == 512:
        space = CONSTRAINED_512_QUICK_PARAM_SPACE
        fixed = {**CONSTRAINED_FIXED_PARAMS, **CONSTRAINED_512_FIXED_OVERRIDE}
    elif args.quick:
        space = CONSTRAINED_QUICK_PARAM_SPACE
        fixed = {**CONSTRAINED_FIXED_PARAMS, **CONSTRAINED_QUICK_FIXED_OVERRIDE}
    elif args.extended and memory_limit_mb == 512:
        space = CONSTRAINED_512_PARAM_SPACE
        fixed = {**CONSTRAINED_FIXED_PARAMS, **CONSTRAINED_512_FIXED_OVERRIDE}
    elif args.extended:
        space = CONSTRAINED_EXTENDED_PARAM_SPACE
        fixed = {**CONSTRAINED_FIXED_PARAMS, **CONSTRAINED_EXTENDED_FIXED_OVERRIDE}
    elif args.overnight:
        space = CONSTRAINED_OVERNIGHT_PARAM_SPACE
        fixed = {**CONSTRAINED_FIXED_PARAMS, **CONSTRAINED_OVERNIGHT_FIXED_OVERRIDE}
    else:
        space = CONSTRAINED_PARAM_SPACE
        fixed = CONSTRAINED_FIXED_PARAMS.copy()

    if args.quick and memory_limit_mb == 512:
        reps = args.reps or CONSTRAINED_512_REPS
        extra_combos = CONSTRAINED_512_QUICK_EXTRA_COMBOS
        workloads = args.workloads or ["readwhilewriting"]  # single workload for quick validation
    elif args.extended and memory_limit_mb == 512:
        reps = args.reps or CONSTRAINED_512_REPS
        extra_combos = None  # rectangular grid, no extras needed
        workloads = args.workloads or CONSTRAINED_512_WORKLOADS
    elif args.extended:
        reps = args.reps or CONSTRAINED_EXTENDED_REPS
        extra_combos = CONSTRAINED_EXTENDED_EXTRA_COMBOS
        workloads = args.workloads or CONSTRAINED_EXTENDED_WORKLOADS
    else:
        reps = args.reps or 1
        extra_combos = None
        workloads = args.workloads or CONSTRAINED_WORKLOADS

    # Results go to memory-specific subdirectories when not 256MB
    if memory_limit_mb != MEMORY_LIMIT_MB:
        raw_dir = PROJECT_ROOT / "results" / f"constrained_{memory_limit_mb}mb" / "raw"
        parsed_dir = PROJECT_ROOT / "results" / f"constrained_{memory_limit_mb}mb" / "parsed"
        csv_path = parsed_dir / "summary.csv"
    else:
        csv_path = CONSTRAINED_SUMMARY_CSV
        raw_dir = CONSTRAINED_RESULTS_RAW_DIR

    csv_columns = CONSTRAINED_CSV_COLUMNS
    timeout = CONSTRAINED_RUN_TIMEOUT_SECONDS

    # Generate all runs with memory-specific prefix to avoid ID collisions
    run_prefix = f"constrained{memory_limit_mb}|" if memory_limit_mb != MEMORY_LIMIT_MB else "constrained|"
    all_runs = generate_runs(
        space, workloads, prefix=run_prefix,
        reps=reps, extra_combos=extra_combos,
    )
    mode_label = "EXTENDED" if args.extended else ("OVERNIGHT" if args.overnight else "FULL")
    print(f"CONSTRAINED MODE ({mode_label}) — Docker with {memory_limit_mb}MB memory limit")
    if reps > 1:
        print(f"Repetitions per config: {reps}")
    print(f"Total planned runs: {len(all_runs)}")

    # Filter completed runs if resuming
    if args.resume:
        completed = get_completed_run_ids(csv_path)
        all_runs = [(w, p, rid, r) for w, p, rid, r in all_runs if rid not in completed]
        print(f"Remaining runs after resume filter: {len(all_runs)}")

    if args.max_runs is not None:
        all_runs = all_runs[:args.max_runs]
        print(f"Limited to {len(all_runs)} runs (--max-runs)")

    if not all_runs:
        print("Nothing to run.")
        return

    # Ensure CSV exists with header
    if not args.dry_run:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not csv_path.exists():
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(csv_columns)

    # Execute — adaptive ETA based on per-run cost estimates
    succeeded = 0
    failed = 0
    oom_kills = 0
    run_costs = [_estimate_run_cost(p, w) for w, p, _, _ in all_runs]
    total_cost = sum(run_costs)
    completed_cost = 0.0

    # Seed ETA with heuristic baseline scaled by key count; replaced after run 1
    num_keys = fixed.get("num", 10_000_000)
    secs_per_unit = _BASELINE_SECS_PER_COST_UNIT_10M * (num_keys / 10_000_000)
    parallel = getattr(args, "parallel", 1) or 1
    est_total = secs_per_unit * total_cost / parallel
    print(f"Estimated total time: ~{_fmt_duration(est_total)} "
          f"(heuristic{f', {parallel} workers' if parallel > 1 else ''}; refines after first run)")

    sweep_start = time.monotonic()

    if parallel > 1:
        # ── Parallel execution with live status display ──────────────────
        csv_lock = threading.Lock()
        state_lock = threading.Lock()
        completed_count = 0
        # Active workers: {run_id: (idx, label, start_time, est_secs)}
        active_workers = {}
        # Completed log: list of (message,)
        completed_log = []
        display_stop = threading.Event()

        def _human_bytes(n):
            if n >= 1024**2: return f"{n // 1024**2}MB"
            if n >= 1024: return f"{n // 1024}KB"
            return f"{n}B"

        def _display_loop():
            """Redraws active worker status every second."""
            while not display_stop.is_set():
                with state_lock:
                    # Print any new completed messages
                    while completed_log:
                        print(completed_log.pop(0))
                    # Build status lines for active workers
                    now = time.monotonic()
                    sweep_el = now - sweep_start
                    if completed_cost > 0:
                        remaining = ((sweep_el / completed_cost)
                                     * (total_cost - completed_cost) / parallel)
                    else:
                        remaining = secs_per_unit * total_cost / parallel
                    lines = []
                    for rid, (idx, label, start, est) in sorted(
                        active_workers.items(), key=lambda x: x[1][0]
                    ):
                        run_el = now - start
                        lines.append(
                            f"  [{idx+1}/{len(all_runs)}] {rid} {label} | "
                            f"running: {_fmt_duration(run_el)}/~{_fmt_duration(est)}"
                        )
                    status_block = "\n".join(lines)
                    summary = (f"  total: {_fmt_duration(sweep_el)} | "
                               f"done: {completed_count}/{len(all_runs)} | "
                               f"remaining: ~{_fmt_duration(remaining)}")
                    # Move cursor up and clear to redraw
                    n_lines = len(lines) + 1  # +1 for summary
                if active_workers or completed_count < len(all_runs):
                    # Clear previous status and redraw
                    output = f"\033[{n_lines}F\033[J" if hasattr(_display_loop, '_prev_n') else ""
                    output += status_block + "\n" + summary
                    print(output, flush=True)
                    _display_loop._prev_n = n_lines
                display_stop.wait(1.0)

        def _run_one(idx, workload, params, run_id, rep, cost):
            nonlocal succeeded, failed, oom_kills, completed_cost, completed_count
            est_run_secs = secs_per_unit * cost
            comp = params.get("compression_type", "?")
            wbuf = _human_bytes(params.get("write_buffer_size", 0))
            csz = _human_bytes(params.get("cache_size", 0))
            rep_label = f" rep{rep}" if reps > 1 else ""
            label = f"({comp}, wbuf={wbuf}, cache={csz}{rep_label})"
            run_start = time.monotonic()
            with state_lock:
                active_workers[run_id] = (idx, label, run_start, est_run_secs)
            ok = run_single_constrained_benchmark(
                workload, params, run_id,
                fixed_params=fixed,
                memory_limit_mb=memory_limit_mb,
                timeout=timeout,
                raw_dir=raw_dir,
                csv_path=csv_path,
                csv_columns=csv_columns,
                dry_run=args.dry_run,
                csv_lock=csv_lock,
                print_lock=state_lock,  # suppress per-run output
                rep=rep,
            )
            with state_lock:
                del active_workers[run_id]
                completed_cost += cost
                completed_count += 1
                run_elapsed = time.monotonic() - run_start
                if ok:
                    succeeded += 1
                    status = "ok"
                else:
                    failed += 1
                    log_path = raw_dir / f"{run_id}.log"
                    if log_path.exists() and "OOM KILLED" in log_path.read_text():
                        oom_kills += 1
                        status = "OOM"
                    else:
                        status = "FAIL"
                completed_log.append(
                    f"  done {run_id} {label} {status} ({_fmt_duration(run_elapsed)})")
            return ok

        print(f"Running with {parallel} parallel workers\n")
        # Print blank lines for the display to overwrite
        for _ in range(parallel + 1):
            print()
        _display_loop._prev_n = parallel + 1

        display_thread = threading.Thread(target=_display_loop, daemon=True)
        display_thread.start()

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {}
            for i, (workload, params, run_id, rep) in enumerate(all_runs):
                f = pool.submit(_run_one, i, workload, params, run_id, rep, run_costs[i])
                futures[f] = run_id
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    with state_lock:
                        completed_log.append(f"  ERROR in {futures[f]}: {e}")

        display_stop.set()
        display_thread.join(timeout=2)
        # Final redraw
        print()
    else:
        # ── Sequential execution ─────────────────────────────────────────
        for i, (workload, params, run_id, rep) in enumerate(all_runs, 1):
            if completed_cost > 0:
                secs_per_unit = (time.monotonic() - sweep_start) / completed_cost
            state = {
                "sweep_start": sweep_start,
                "secs_per_unit": secs_per_unit,
                "run_cost": run_costs[i - 1],
                "total_cost": total_cost,
                "completed_cost": completed_cost,
                "run_index": i,
                "total_runs": len(all_runs),
            }
            ok = run_single_constrained_benchmark(
                workload, params, run_id,
                fixed_params=fixed,
                memory_limit_mb=memory_limit_mb,
                timeout=timeout,
                raw_dir=raw_dir,
                csv_path=csv_path,
                csv_columns=csv_columns,
                dry_run=args.dry_run,
                sweep_state=state,
                rep=rep,
            )
            completed_cost += run_costs[i - 1]
            if ok:
                succeeded += 1
            else:
                failed += 1
                # Check raw log for OOM
                log_path = raw_dir / f"{run_id}.log"
                if log_path.exists() and "OOM KILLED" in log_path.read_text():
                    oom_kills += 1

            # Print updated total time remaining after each run
            if not args.dry_run and i < len(all_runs):
                elapsed_total = time.monotonic() - sweep_start
                secs_per_unit = elapsed_total / completed_cost
                remaining_cost = total_cost - completed_cost
                remaining_secs = secs_per_unit * remaining_cost
                print(f">>> {i}/{len(all_runs)} done | "
                      f"elapsed: {_fmt_duration(elapsed_total)} | "
                      f"remaining: ~{_fmt_duration(remaining_secs)}")

    # Summary
    print(f"\n{'='*70}")
    print(f"Finished: {succeeded} succeeded, {failed} failed "
          f"({oom_kills} OOM kills) out of {len(all_runs)}")
    if not args.dry_run:
        print(f"Results: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="RocksDB microbenchmark orchestrator")
    parser.add_argument(
        "--quick", action="store_true", help="Use reduced parameter space"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned runs without executing",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip already-completed runs",
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        help="Override workload list (e.g., --workloads fillrandom readrandom)",
    )
    parser.add_argument(
        "--constrained",
        action="store_true",
        help="Run in memory-constrained Docker mode (IoT simulation)",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Limit to first N runs (useful for validation)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Run N Docker containers in parallel (default: 1)",
    )
    parser.add_argument(
        "--overnight",
        action="store_true",
        help="Overnight mode: thesis-critical params only, 5M keys, ~8h with 3 workers",
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Extended mode: safe configs only, 5 reps, 2 workloads, ~24h with 3 workers",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=None,
        help="Override number of repetitions per config (default depends on mode)",
    )
    parser.add_argument(
        "--memory-limit",
        type=int,
        default=None,
        help="Docker memory limit in MB (default: 256). Use 512 for RPi 4 class devices.",
    )
    args = parser.parse_args()

    # Dispatch to constrained mode
    if args.constrained:
        run_constrained(args)
        return

    # Validate db_bench exists
    if not args.dry_run and not DB_BENCH_BIN.exists():
        print(f"ERROR: db_bench not found at {DB_BENCH_BIN}")
        print("Run: bash scripts/build_rocksdb.sh")
        sys.exit(1)

    # Select parameter space and workloads
    if args.quick:
        space = QUICK_PARAM_SPACE
        workloads = args.workloads or QUICK_WORKLOADS
    else:
        space = PARAM_SPACE
        workloads = args.workloads or ALL_WORKLOADS

    # Validate workloads
    for w in workloads:
        if w not in ALL_WORKLOADS:
            print(f"WARNING: Unknown workload '{w}', proceeding anyway.")

    # Generate all runs
    all_runs = generate_runs(space, workloads)
    print(f"Total planned runs: {len(all_runs)}")

    # Filter completed runs if resuming
    if args.resume:
        completed = get_completed_run_ids(SUMMARY_CSV)
        all_runs = [(w, p, rid, r) for w, p, rid, r in all_runs if rid not in completed]
        print(f"Remaining runs after resume filter: {len(all_runs)}")

    if not all_runs:
        print("Nothing to run.")
        return

    # Ensure CSV exists with header
    if not args.dry_run:
        ensure_csv_header(SUMMARY_CSV)

    # Execute
    succeeded = 0
    failed = 0
    for i, (workload, params, run_id, _rep) in enumerate(all_runs, 1):
        print(f"\n[{i}/{len(all_runs)}]", end="")
        if run_single_benchmark(workload, params, run_id, dry_run=args.dry_run):
            succeeded += 1
        else:
            failed += 1

    # Summary
    print(f"\n{'='*70}")
    print(f"Finished: {succeeded} succeeded, {failed} failed out of {len(all_runs)}")
    if not args.dry_run:
        print(f"Results: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
