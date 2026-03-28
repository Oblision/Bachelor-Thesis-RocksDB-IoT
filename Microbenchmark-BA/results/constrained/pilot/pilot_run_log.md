# Pilot Run Log — Constrained Benchmarks

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

| run_id | compression | write_buffer | cache_size | ops/sec | cache_hit_rate | write_amp | stall% | status |
|--------|-------------|-------------|------------|---------|---------------|-----------|--------|--------|
| 87026de2b54b | snappy | 4MB | 64MB | 6,393 | 56.3% | 3.87 | 25.5% | ok |
| 930992b385b6 | snappy | 64MB | 64MB | 27,190 | 29.2% | 0.90 | - | ok |
| 9c4bbe97766a | none | 4MB | 8MB | 2,067 | 5.3% | 7.53 | 18.2% | ok |
| ac8974d9b842 | none | 64MB | 8MB | 22,850 | 4.0% | 1.69 | - | ok |
| bf95d076c72f | snappy | 64MB | 8MB | 21,531 | 4.1% | 0.92 | - | ok |
| d378daa7f9e2 | none | 4MB | 64MB | 7,150 | 54.9% | 7.44 | 19.3% | ok |
| e0e70c90e132 | snappy | 4MB | 8MB | 1,962 | 5.7% | 3.95 | 25.5% | ok |
| efb768bde3cc | none | 64MB | 64MB | - | - | - | - | oom |

## OOM Kill

- **Run:** `efb768bde3cc`
- **Params:** compression=none, write_buffer_size=64MB, cache_size=64MB
- **Cause:** 64MB write buffer + 64MB block cache + memtable overhead + compaction buffers > 256MB
- **Duration before kill:** 14.1s
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
