"""
Configuration for RocksDB microbenchmark parameter sweeps.

Parameter space based on Jin et al. LSM-tree tuning knobs.
"""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_BENCH_BIN = PROJECT_ROOT / "rocksdb" / "build" / "db_bench"
RESULTS_RAW_DIR = PROJECT_ROOT / "results" / "raw"
RESULTS_PARSED_DIR = PROJECT_ROOT / "results" / "parsed"
SUMMARY_CSV = RESULTS_PARSED_DIR / "summary.csv"
DB_TEMP_DIR = Path("/tmp/rocksdb_bench_db")

# ── Fixed parameters (constant across all runs) ───────────────────────────────
FIXED_PARAMS = {
    "num": 1_000_000,
    "value_size": 100,
    "key_size": 16,
    "threads": 1,
    "histogram": 1,
    "statistics": "",  # flag-only, no value
}

# ── Parameter space (Jin et al. knobs) ─────────────────────────────────────────
PARAM_SPACE = {
    "compression_type": ["none", "snappy", "lz4", "zstd"],
    "min_level_to_compress": [-1, 0, 2, 4],
    "level0_slowdown_writes_trigger": [10, 20, 40, 80],
    "write_buffer_size": [
        4 * 1024 * 1024,       # 4 MB
        16 * 1024 * 1024,      # 16 MB
        64 * 1024 * 1024,      # 64 MB
        256 * 1024 * 1024,     # 256 MB
    ],
}

# ── Quick mode (reduced space for validation) ──────────────────────────────────
QUICK_PARAM_SPACE = {
    "compression_type": ["none", "snappy"],
    "min_level_to_compress": [-1, 2],
    "level0_slowdown_writes_trigger": [20],
    "write_buffer_size": [
        4 * 1024 * 1024,       # 4 MB
        64 * 1024 * 1024,      # 64 MB
    ],
}

# ── Workloads ──────────────────────────────────────────────────────────────────
ALL_WORKLOADS = [
    "fillrandom",
    "fillseq",
    "readrandom",
    "readwhilewriting",
    "overwrite",
    "deleterandom",
    "mixgraph",
]

QUICK_WORKLOADS = ["fillrandom", "readrandom"]

# Workloads that need a pre-populated DB (read/mixed loads)
NEEDS_PREPOPULATE = {"readrandom", "readwhilewriting", "deleterandom", "mixgraph"}

# ── Timing ─────────────────────────────────────────────────────────────────────
RUN_TIMEOUT_SECONDS = 3600  # 1 hour max per run

# ── CSV columns ────────────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "run_id",
    "workload",
    "compression_type",
    "min_level_to_compress",
    "level0_slowdown_writes_trigger",
    "write_buffer_size",
    "ops_per_sec",
    "micros_per_op",
    "mb_per_sec",
    "p50",
    "p75",
    "p99",
    "p99_9",
    "p99_99",
    "write_amp",
    "compaction_write_gb",
    "stall_percent",
    "stall_micros",
    "timestamp",
]

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTRAINED MODE — Memory-limited IoT simulation via Docker
# ═══════════════════════════════════════════════════════════════════════════════

# ── Docker / Constrained paths ────────────────────────────────────────────────
CONSTRAINED_RESULTS_RAW_DIR = PROJECT_ROOT / "results" / "constrained" / "raw"
CONSTRAINED_RESULTS_PARSED_DIR = PROJECT_ROOT / "results" / "constrained" / "parsed"
CONSTRAINED_SUMMARY_CSV = CONSTRAINED_RESULTS_PARSED_DIR / "summary.csv"
DOCKER_IMAGE_NAME = "rocksdb-bench-iot"
DOCKER_DB_PATH = Path("/data/rocksdb")
MEMORY_LIMIT_MB = 256

# ── Constrained fixed params (memory-optimized for IoT) ──────────────────────
CONSTRAINED_FIXED_PARAMS = {
    "num": 10_000_000,                              # 10M keys (~1.1GB on disk)
    "value_size": 100,
    "key_size": 16,
    "threads": 1,
    "histogram": 1,
    "statistics": "",                               # flag-only, no value
    "max_write_buffer_number": 2,                   # Limit memtable count
    "db_write_buffer_size": 0,                      # No global buffer limit
    "max_write_buffer_size_to_maintain": 0,         # Free flushed memtables immediately
    "compressed_secondary_cache_size": 0,           # Disable secondary cache
    "row_cache_size": 0,                            # Disable row cache
    # Direct I/O disabled — O_DIRECT fails or stalls on Docker Desktop virtiofs.
    # Docker --memory=256m enforces the memory limit via cgroups instead.
}

# ── Constrained parameter space (adds cache_size as new sweep dimension) ─────
# 4 x 3 x 3 x 5 x 5 = 900 combos x 1 workload = 900 runs
CONSTRAINED_PARAM_SPACE = {
    "compression_type": ["none", "snappy", "lz4", "zstd"],         # 4
    "min_level_to_compress": [-1, 0, 2],                           # 3
    "level0_slowdown_writes_trigger": [10, 20, 40],                # 3
    "write_buffer_size": [
        4 * 1024 * 1024,     # 4 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        128 * 1024 * 1024,   # 128 MB  (likely OOM with large cache)
    ],                                                              # 5
    "cache_size": [
        8 * 1024 * 1024,     # 8 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        128 * 1024 * 1024,   # 128 MB
    ],                                                              # 5
}

# ── Quick constrained mode (validation subset) ───────────────────────────────
# 2 x 1 x 1 x 2 x 2 = 8 combos x 1 workload = 8 runs
CONSTRAINED_QUICK_PARAM_SPACE = {
    "compression_type": ["none", "snappy"],
    "min_level_to_compress": [-1],
    "level0_slowdown_writes_trigger": [20],
    "write_buffer_size": [
        4 * 1024 * 1024,     # 4 MB
        64 * 1024 * 1024,    # 64 MB
    ],
    "cache_size": [
        8 * 1024 * 1024,     # 8 MB
        64 * 1024 * 1024,    # 64 MB
    ],
}

CONSTRAINED_QUICK_FIXED_OVERRIDE = {
    "num": 2_000_000,  # Reduced key count for quick validation
}

# ── Overnight constrained mode (thesis-critical dimensions, ~8h with 3 workers)
# Fix secondary params, sweep cache x wbuf x compression
# 5 x 5 x 4 = 100 combos x 1 workload = 100 runs, 5M keys (~580MB dataset)
CONSTRAINED_OVERNIGHT_PARAM_SPACE = {
    "compression_type": ["none", "snappy", "lz4", "zstd"],         # 4
    "min_level_to_compress": [-1],                                  # fixed
    "level0_slowdown_writes_trigger": [20],                         # fixed
    "write_buffer_size": [
        4 * 1024 * 1024,     # 4 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        128 * 1024 * 1024,   # 128 MB
    ],                                                              # 5
    "cache_size": [
        8 * 1024 * 1024,     # 8 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        128 * 1024 * 1024,   # 128 MB
    ],                                                              # 5
}

CONSTRAINED_OVERNIGHT_FIXED_OVERRIDE = {
    "num": 2_000_000,  # 2M keys (proven from pilot, clear differentiation)
}

# ── Extended constrained mode (low-variance, ~24h with 3 workers) ────────────
# Only safe configs (0 OOM in overnight run), 5 repetitions, 2 workloads.
# 13 safe combos x 2 workloads x 5 reps = 130 runs
CONSTRAINED_EXTENDED_PARAM_SPACE = {
    "compression_type": ["none", "snappy", "lz4", "zstd"],         # 4
    "min_level_to_compress": [-1],                                  # fixed
    "level0_slowdown_writes_trigger": [20],                         # fixed
    "write_buffer_size": [
        4 * 1024 * 1024,     # 4 MB  — all cache sizes safe
        16 * 1024 * 1024,    # 16 MB — cache ≤ 64MB safe
        32 * 1024 * 1024,    # 32 MB — cache ≤ 64MB safe
    ],                                                              # 3
    "cache_size": [
        8 * 1024 * 1024,     # 8 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        # 128 MB omitted: OOM with wbuf ≥ 16MB
    ],                                                              # 4
}
# Extra safe combos only possible with wbuf=4MB
CONSTRAINED_EXTENDED_EXTRA_COMBOS = [
    # wbuf=4MB + cache=128MB was safe in overnight run
    {"compression_type": "none",   "write_buffer_size": 4*1024*1024, "cache_size": 128*1024*1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
    {"compression_type": "snappy", "write_buffer_size": 4*1024*1024, "cache_size": 128*1024*1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
    {"compression_type": "lz4",    "write_buffer_size": 4*1024*1024, "cache_size": 128*1024*1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
    {"compression_type": "zstd",   "write_buffer_size": 4*1024*1024, "cache_size": 128*1024*1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
]

CONSTRAINED_EXTENDED_FIXED_OVERRIDE = {
    "num": 2_000_000,  # 2M keys (same as overnight for comparability)
}

CONSTRAINED_EXTENDED_REPS = 3  # 3 reps: sufficient for CI; ~20h with parallel=4
CONSTRAINED_EXTENDED_WORKLOADS = ["fillrandom", "readwhilewriting"]

# ── 512MB full sweep (state-of-the-art edge devices, e.g. RPi 4) ─────────────
# Complete sweep of ALL wbuf×cache combinations at 512MB, including OOM-prone
# configs to capture the memory boundary in the dataset.
# Memory model: usage ≈ 2 × wbuf + cache + ~100MB overhead
# At 512MB: 2×wbuf + cache ≤ 412MB → safe. Above → OOM (recorded as status=oom).
#
# 5 × 5 × 4 = 100 combos × 2 workloads × 5 reps = 1000 runs (~21h with parallel=4)
CONSTRAINED_512_PARAM_SPACE = {
    "compression_type": ["none", "snappy", "lz4", "zstd"],         # 4
    "min_level_to_compress": [-1],                                  # fixed
    "level0_slowdown_writes_trigger": [20],                         # fixed
    "write_buffer_size": [
        4 * 1024 * 1024,     # 4 MB
        16 * 1024 * 1024,    # 16 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB  — new at 512MB
        128 * 1024 * 1024,   # 128 MB — new at 512MB
    ],                                                              # 5
    "cache_size": [
        8 * 1024 * 1024,     # 8 MB
        32 * 1024 * 1024,    # 32 MB
        64 * 1024 * 1024,    # 64 MB
        128 * 1024 * 1024,   # 128 MB
        256 * 1024 * 1024,   # 256 MB — OOM with wbuf=128MB
    ],                                                              # 5
}

CONSTRAINED_512_FIXED_OVERRIDE = {
    "num": 2_000_000,  # 2M keys (same as overnight for comparability)
}

CONSTRAINED_512_REPS = 50
CONSTRAINED_512_WORKLOADS = ["fillrandom", "readwhilewriting"]

# ── Quick 512MB validation (3 configs × 5 reps × 1 workload = 15 runs) ──────
CONSTRAINED_512_QUICK_PARAM_SPACE = {
    "compression_type": ["lz4"],
    "min_level_to_compress": [-1],
    "level0_slowdown_writes_trigger": [20],
    "write_buffer_size": [4 * 1024 * 1024],      # 4 MB — small/fast
    "cache_size": [32 * 1024 * 1024],             # 32 MB
}
CONSTRAINED_512_QUICK_EXTRA_COMBOS = [
    # Large new-at-512MB config
    {"compression_type": "zstd", "write_buffer_size": 64 * 1024 * 1024,
     "cache_size": 128 * 1024 * 1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
    # OOM-prone config to validate boundary recording
    {"compression_type": "none", "write_buffer_size": 128 * 1024 * 1024,
     "cache_size": 256 * 1024 * 1024,
     "min_level_to_compress": -1, "level0_slowdown_writes_trigger": 20},
]

# ── Constrained workloads ─────────────────────────────────────────────────────
CONSTRAINED_WORKLOADS = ["readwhilewriting"]

# ── Constrained timing ───────────────────────────────────────────────────────
CONSTRAINED_RUN_TIMEOUT_SECONDS = 7200  # 2 hours max per run

# ── Constrained CSV columns ──────────────────────────────────────────────────
CONSTRAINED_CSV_COLUMNS = [
    "run_id",
    "workload",
    "rep",
    "status",
    "run_duration_secs",
    "compression_type",
    "min_level_to_compress",
    "level0_slowdown_writes_trigger",
    "write_buffer_size",
    "cache_size",
    "memory_limit_mb",
    "ops_per_sec",
    "micros_per_op",
    "mb_per_sec",
    "p50",
    "p75",
    "p99",
    "p99_9",
    "p99_99",
    "block_cache_hit_rate",
    "write_amp",
    "compaction_write_gb",
    "compact_read_gb",
    "flush_write_gb",
    "bytes_written",
    "bytes_read",
    "stall_percent",
    "stall_micros",
    "timestamp",
]
