# RocksDB Microbenchmark Suite

Standalone microbenchmark suite for evaluating RocksDB LSM-tree tuning parameters. It is part of the bachelor's thesis
[*Out-of-Core Stream Processing in NebulaStream*](../README.md): its results chose the RocksDB configuration
(compression, write-buffer and block-cache sizes) used by the out-of-core state backend.

Uses RocksDB's built-in `db_bench` tool with a Python orchestrator that sweeps the parameter space (based on Jin et al.) and collects structured results.

## Prerequisites

**macOS (Homebrew):**
```bash
brew install cmake gflags snappy lz4 zstd zlib python3
pip3 install pandas matplotlib  # for analysis
```

**Ubuntu/Debian:**
```bash
sudo apt-get install -y cmake libgflags-dev libsnappy-dev liblz4-dev libzstd-dev zlib1g-dev python3 python3-pip
pip3 install pandas matplotlib
```

## Setup

```bash
# Clone with submodule
git clone --recurse-submodules https://github.com/Oblision/Bachelor-Thesis-RocksDB-IoT.git
cd Bachelor-Thesis-RocksDB-IoT/Microbenchmark-BA

# Or if already cloned:
git submodule update --init --recursive

# Build db_bench
bash scripts/build_rocksdb.sh
```

## Usage

### Quick validation run (~10 min)
```bash
python3 scripts/run_benchmarks.py --quick
```

Runs 16 benchmark configurations (2 compression types x 2 levels x 1 trigger x 2 buffer sizes x 2 workloads).

### Full parameter sweep
```bash
# All workloads (1,792 runs)
python3 scripts/run_benchmarks.py

# Single workload at a time
python3 scripts/run_benchmarks.py --workloads fillrandom

# Resume after interruption
python3 scripts/run_benchmarks.py --resume

# Preview without running
python3 scripts/run_benchmarks.py --dry-run
```

### Analyze results
```bash
python3 scripts/analyze.py
```

## Parameter Space

Based on Jin et al. LSM-tree tuning knobs:

| Parameter | Values |
|---|---|
| `compression_type` | none, snappy, lz4, zstd |
| `min_level_to_compress` | -1, 0, 2, 4 |
| `level0_slowdown_writes_trigger` | 10, 20, 40, 80 |
| `write_buffer_size` | 4MB, 16MB, 64MB, 256MB |

Full sweep: 4 x 4 x 4 x 4 = 256 parameter combinations across 7 workloads = 1,792 runs.

### Workloads

| Workload | Description |
|---|---|
| `fillrandom` | Random key insertion (write-heavy) |
| `fillseq` | Sequential key insertion |
| `readrandom` | Random key lookups (pre-populated DB) |
| `readwhilewriting` | Concurrent reads + writes |
| `overwrite` | Overwrite existing keys |
| `deleterandom` | Random key deletion |
| `mixgraph` | Mixed read/write from distribution |

### Fixed parameters
- 1,000,000 keys per run
- 100-byte values, 16-byte keys
- Single thread
- Histogram and statistics enabled

## Memory-Constrained IoT Mode (Docker)

Simulates a $10 IoT device (Raspberry Pi Zero W class: 512MB total, ~256MB available) using Docker memory limits. This mode uses a larger dataset (10M keys, ~1.1GB) that exceeds available memory, adds `cache_size` as a sweep parameter, and enables Direct I/O to bypass the OS page cache.

### Docker Setup

```bash
# Install Docker Desktop (macOS)
brew install --cask docker
# Launch Docker Desktop, ensure >= 4GB VM memory in Settings > Resources

# Build the benchmark image (~15-30 min)
bash scripts/build_docker.sh
```

### Constrained Usage

```bash
# Quick validation (16 runs, ~30-60 min)
python3 scripts/run_benchmarks.py --constrained --quick

# Full constrained sweep (864 runs)
python3 scripts/run_benchmarks.py --constrained

# Single workload
python3 scripts/run_benchmarks.py --constrained --workloads fillrandom

# Resume after interruption
python3 scripts/run_benchmarks.py --constrained --resume

# Analyze constrained results
python3 scripts/analyze.py --constrained

# Compare constrained vs unconstrained
python3 scripts/analyze.py --compare
```

### Constrained Parameter Space

| Parameter | Values |
|---|---|
| `compression_type` | none, snappy, lz4, zstd |
| `min_level_to_compress` | -1, 0, 2 |
| `level0_slowdown_writes_trigger` | 10, 20, 40 |
| `write_buffer_size` | 4MB, 16MB, 64MB |
| `cache_size` | 2MB, 8MB, 32MB, 128MB |

4 x 3 x 3 x 3 x 4 = 432 combos x 2 workloads = **864 runs**.

Docker constraints: `--memory=256m --memory-swap=256m --cpus=1`

### Constrained Fixed Parameters
- 10,000,000 keys (~1.1GB on disk)
- Direct I/O enabled (bypasses OS page cache)
- 2 max write buffers, no secondary/row cache
- Single thread, single CPU core

## Output

### Unconstrained
Results are written to `results/parsed/summary.csv` with columns:

```
run_id, workload, compression_type, min_level_to_compress,
level0_slowdown_writes_trigger, write_buffer_size, ops_per_sec,
micros_per_op, mb_per_sec, p50, p75, p99, p99_9, p99_99,
write_amp, compaction_write_gb, stall_percent, stall_micros, timestamp
```

Raw `db_bench` logs are saved to `results/raw/{run_id}.log`.

### Constrained
Results are written to `results/constrained/parsed/summary.csv` with additional columns: `cache_size`, `memory_limit_mb`.

Raw logs are saved to `results/constrained/raw/{run_id}.log`.

## Project Structure

```
Microbenchmark-BA/
├── rocksdb/                    # Git submodule (facebook/rocksdb v10.10.1)
├── scripts/
│   ├── build_rocksdb.sh        # Build db_bench from source (native)
│   ├── build_docker.sh         # Build Docker image for constrained mode
│   ├── config.py               # Parameter space, paths, constants
│   ├── parse_output.py         # Regex parser for db_bench output
│   ├── run_benchmarks.py       # Main orchestrator (native + constrained)
│   └── analyze.py              # Result analysis and plotting
├── results/
│   ├── raw/                    # Raw db_bench logs (unconstrained)
│   ├── parsed/                 # summary.csv (unconstrained)
│   └── constrained/
│       ├── raw/                # Raw db_bench logs (constrained)
│       └── parsed/             # summary.csv (constrained)
├── Dockerfile                  # Two-stage build for db_bench container
├── .dockerignore
├── .gitignore
└── README.md
```
