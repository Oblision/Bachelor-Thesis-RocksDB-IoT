# Project Context

## Thesis Goal
Bachelor thesis on **out-of-core state management for NebulaStream** — a stream processing engine for IoT/smart city edge deployments. We are evaluating whether **RocksDB** is viable as the embedded storage backend for stateful stream operators (windowed aggregations, joins) on memory-constrained edge devices.

## Smart City Scenario
Sensors (traffic cameras, air quality monitors, smart meters) produce continuous data streams processed on cheap edge hardware (Raspberry Pi Zero W class: 512MB total, ~256MB available). Stream operators accumulate state that exceeds available memory, requiring an out-of-core storage engine that spills to disk gracefully.

## What This Repo Does
A Python-orchestrated microbenchmark suite that sweeps RocksDB `db_bench` across tuning parameter combinations and collects structured CSV results. Two modes:

### Unconstrained Mode (baseline)
- Runs `db_bench` natively on macOS with unlimited memory
- 1M keys (~100MB dataset, fits in memory)
- 256 param combos x 7 workloads = 1,792 runs
- **Finding so far:** compression, L0 triggers, and min_level_to_compress had negligible impact because the dataset fits in memory. No meaningful write amplification or stalls captured.

### Constrained Mode (IoT simulation) — just implemented
- Runs `db_bench` inside Docker with `--memory=256m --memory-swap=256m --cpus=1`
- 10M keys (~1.1GB dataset, exceeds 256MB memory)
- Direct I/O enabled to bypass OS page cache
- `cache_size` added as new sweep parameter (the dominant knob under memory pressure)
- 432 param combos x 2 workloads = 864 runs
- Workloads: `fillrandom` (sensor ingestion), `readrandom` (state lookups)

## Key Benchmark Parameters and Why They Matter

| Parameter | Values (constrained) | Smart City Relevance |
|---|---|---|
| `cache_size` | 2MB, 8MB, 32MB, 128MB | How much device RAM goes to storage vs. stream operators — the central thesis knob |
| `write_buffer_size` | 4MB, 16MB, 64MB | Memtable size before flush; larger = fewer flushes but more memory committed |
| `compression_type` | none, snappy, lz4, zstd | Reduces disk I/O but costs CPU — matters on single-core edge devices |
| `level0_slowdown_writes_trigger` | 10, 20, 40 | When RocksDB throttles writes because compaction can't keep up |
| `min_level_to_compress` | -1, 0, 2 | Which LSM levels get compressed; saves CPU for hot data |

## Expected Key Results
1. **Performance cliff** — at what `cache_size` does read throughput collapse?
2. **Write stalls** — does RocksDB stall writes under memory pressure? (back-pressures entire stream pipeline)
3. **Write amplification** — extra disk I/O from compaction (affects SD card lifespan on edge devices)

## Project Structure
```
Microbenchmark-BA/
├── rocksdb/                          # Git submodule (facebook/rocksdb)
├── scripts/
│   ├── build_rocksdb.sh              # Build db_bench natively
│   ├── build_docker.sh               # Build Docker image for constrained mode
│   ├── config.py                     # All parameters, paths, constants
│   ├── parse_output.py               # Regex parser for db_bench output
│   ├── run_benchmarks.py             # Orchestrator (native + Docker modes)
│   └── analyze.py                    # Analysis + plotting (unconstrained/constrained/compare)
├── results/
│   ├── raw/ + parsed/                # Unconstrained results
│   └── constrained/raw/ + parsed/    # Constrained results
├── Dockerfile                        # Two-stage build for db_bench container
├── .dockerignore
├── .gitignore
└── README.md
```

## Current State / Next Steps
- All code for constrained mode is implemented and dry-run verified
- Docker Desktop needs to be installed: `brew install --cask docker`
- Docker image needs to be built: `bash scripts/build_docker.sh` (~15-30 min)
- Quick validation: `python3 scripts/run_benchmarks.py --constrained --quick` (16 runs)
- Full sweep: 864 runs, estimated 43-144 hours

## Known Pitfalls
- **Direct I/O on Docker Desktop macOS**: `O_DIRECT` may fail on `virtiofs`. If so, remove the `use_direct_reads` and `use_direct_io_for_flush_and_compaction` flags from `CONSTRAINED_FIXED_PARAMS` in `config.py` (rely on Docker `--memory` alone).
- **OOM kills (exit 137)**: Most likely with `write_buffer_size=64MB + cache_size=128MB`. The orchestrator logs and skips these gracefully.
- **Docker VM memory**: Docker Desktop needs >= 4GB VM memory (Settings > Resources). The 256MB container limit is separate.

## Open Questions
- Whether to add `readwhilewriting` to constrained workloads (concurrent read+write, more realistic for stream operators)
- Whether to add `deleterandom` (simulates window expiration/state cleanup)
- Whether key/value sizes should be adjusted to match actual NebulaStream state record sizes

## Research Log Protocol

**RESEARCH_LOG.md** is the living timeline of this thesis. Claude MUST:

1. **Auto-log findings:** After any analysis, insight, or decision — automatically create/update an entry in `RESEARCH_LOG.md`. Do not wait for the user to ask.

2. **Respect user corrections:** When Benjamin reviews and adjusts any finding, immediately update the `RESEARCH_LOG.md` entry to reflect HIS determination. The log always represents what the student has verified and concluded. Tag corrected entries with `AI: Corrected` and note what changed.

3. **Follow HCRAID:** Every new experiment follows the cycle: Hypothesize → Configure → Run → Analyze → Interpret → Document. See the Process section in `RESEARCH_LOG.md`.

4. **AI Transparency Tags:** Every entry must have one of: `AI: None` | `AI: Consulted` | `AI: Assisted` | `AI: Generated` | `AI: Corrected`

5. **Data Quality Checklist** (mandatory before any interpretation):
   - [ ] Row count matches expected (configs × reps)
   - [ ] NaN inspection (db_bench 0→NaN bug — stall fields)
   - [ ] OOM runs excluded from means
   - [ ] CV reported per config
   - [ ] No duplicate run_ids

## Preferences
- Keep code simple and focused — this is a benchmark tool, not production software
- Existing unconstrained mode must remain untouched (separate paths, separate CSV, separate run IDs)
- Results from constrained mode go to `results/constrained/`
