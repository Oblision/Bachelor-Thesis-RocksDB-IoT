# Preliminary Report: RocksDB 512MB Constrained Benchmark

**Date:** 2026-03-28
**Dataset:** `results/constrained_512mb/parsed/summary.csv`
**Context:** Evaluating RocksDB as out-of-core storage for NebulaStream stream operators on memory-constrained edge devices (512MB container, 10M keys ~1.1GB dataset)

---

## 1. Dataset Overview

| Metric | Value |
|--------|-------|
| Total runs | 10,000 (9,665 ok, 335 OOM — 3.4%) |
| Workloads | `fillrandom` (5,000), `readwhilewriting` (5,000) |
| Repetitions | 50 per config, 200 unique configs |
| Parameter sweep | 5 cache sizes x 5 write buffer sizes x 4 compressions |
| Fixed params | memory_limit=512MB, min_level_to_compress=-1, level0_slowdown=20 |
| Compute time | ~298 hours over 23 calendar days (Mar 4–27, 2026) |

**Parameter space:**

| Parameter | Values |
|-----------|--------|
| `cache_size` | 8MB, 32MB, 64MB, 128MB, 256MB |
| `write_buffer_size` | 4MB, 16MB, 32MB, 64MB, 128MB |
| `compression_type` | none, snappy, lz4, zstd |

---

## 2. Key Findings

### 2.1 Compression Is the Dominant Knob for Read-Write Performance

Compression has a dramatic impact on `readwhilewriting` throughput — far exceeding the effect of cache size or write buffer size:

| Compression | readwhilewriting (ops/sec) | Speedup vs none | fillrandom (ops/sec) |
|-------------|---------------------------|-----------------|---------------------|
| none | 8,723 | 1.0x | 453,678 |
| zstd | 22,104 | 2.5x | 378,109 |
| snappy | 33,871 | 3.9x | 453,027 |
| lz4 | 38,072 | **4.4x** | 460,611 |

For pure writes (`fillrandom`), compression has minimal impact except for zstd which shows a ~17% penalty due to higher CPU cost. Under concurrent read-write load, compression reduces the data volume that must be read from disk for compaction and point lookups, yielding a 4x throughput gain with lz4.

**Interaction with write buffer size (readwhilewriting ops/sec):**

| Compression | 4MB wbuf | 16MB | 32MB | 64MB | 128MB |
|-------------|----------|------|------|------|-------|
| lz4 | 14,659 | 31,236 | 45,268 | 49,161 | 53,671 |
| snappy | 13,906 | 30,159 | 40,843 | 41,151 | 46,189 |
| zstd | 14,036 | 11,931 | 21,049 | 26,391 | 41,237 |
| none | 11,139 | 19,261 | 1,587 | 3,312 | 6,227 |

Without compression, `readwhilewriting` performance **collapses** at write buffer sizes >= 32MB (1,587 ops/sec). With lz4, the same config achieves 45,268 ops/sec — a 28.5x difference.

### 2.2 No Performance Cliff from Cache Size (Surprising)

Contrary to the initial hypothesis, cache size has almost no effect on throughput:

**fillrandom (ops/sec by cache_size):**

| 8MB | 32MB | 64MB | 128MB | 256MB |
|-----|------|------|-------|-------|
| 436,424 | 435,844 | 438,737 | 438,029 | 432,748 |

Essentially flat — cache is irrelevant for write-only workloads (expected, but confirms baseline).

**readwhilewriting (ops/sec by cache_size):**

| 8MB | 32MB | 64MB | 128MB | 256MB |
|-----|------|------|-------|-------|
| 25,600 | 25,598 | 28,625 | 27,397 | 21,707 |

Only mild variation. The 64MB cache is marginally best, but 256MB actually performs worst (likely due to memory pressure from cache allocation itself competing with compaction buffers).

**Block cache hit rate does scale with size:**

| Cache Size | Hit Rate |
|-----------|----------|
| 8MB | 5.8% |
| 32MB | 22.8% |
| 64MB | 41.6% |
| 128MB | 59.1% |
| 256MB | 77.1% |

The hit rate increase from 6% to 77% does **not** translate into proportional throughput gains. The write-side overhead (compaction, flushes, stalls) dominates total runtime, masking read-path cache benefits.

### 2.3 Write Buffer Size Has Opposite Effects on the Two Workloads

**fillrandom — sweet spot at 32MB:**

| Write Buffer | ops/sec | Write Amp |
|-------------|---------|-----------|
| 4MB | 395,071 | 2.68x |
| 16MB | 481,382 | 1.41x |
| **32MB** | **500,833** | **0.91x** |
| 64MB | 460,150 | 0.59x |
| 128MB | 344,346 | 0.35x |

Larger buffers reduce write amplification but increase flush latency. At 128MB, each flush writes a 128MB SST file, causing long pauses.

**readwhilewriting — monotonically improves with larger buffers:**

| Write Buffer | ops/sec |
|-------------|---------|
| 4MB | 13,435 |
| 16MB | 23,147 |
| 32MB | 27,187 |
| 64MB | 31,012 |
| 128MB | 39,004 |

Under concurrent load, fewer flushes means less write-path contention with read operations.

### 2.4 Write Amplification Is Driven by Write Buffer Size, Not Cache

Write amplification shows **no dependency on cache size** (~1.19x across all cache sizes for fillrandom).

**By compression (fillrandom):**

| Compression | Write Amp |
|-------------|-----------|
| zstd | 0.73x |
| lz4 | 1.00x |
| snappy | 1.01x |
| none | 2.01x |

**Compaction volume (readwhilewriting):**

| Write Buffer | Compaction Write (mean) |
|-------------|------------------------|
| 4MB | 13.4 GB |
| 16MB | 6.2 GB |
| 32MB | 8.9 GB |
| 64MB | 3.0 GB |
| 128MB | 1.2 GB |

Larger write buffers dramatically reduce total I/O — critical for SD card lifespan on edge devices.

### 2.5 Write Stalls: Compression + Write Buffer Interaction (Corrected)

**Methodology note:** `db_bench` only reports stall statistics when stalls actually occur. Runs with no stall data had zero stalls. The initial analysis (treating missing data as "unknown") severely overestimated the mean stall rate by sampling only slow, stalling runs. The corrected analysis treats missing stall data as 0%.

**Corrected stall % by compression x write_buffer_size (readwhilewriting):**

| Compression | 4MB wbuf | 16MB | 32MB | 64MB | 128MB |
|-------------|----------|------|------|------|-------|
| **none** | 24.7% | 12.6% | 25.5% | 5.9% | 0.0% |
| zstd | 52.9% | 2.6% | 0.9% | 0.0% | 0.0% |
| snappy | 21.0% | 0.3% | 0.2% | 0.0% | 0.0% |
| **lz4** | 21.9% | **0.2%** | **0.1%** | **0.0%** | **0.0%** |

Key observations:
- **Larger buffers DO reduce stalls dramatically:** 4MB -> 30% mean, 16MB -> 3.9%, 64MB -> 1.3%, 128MB -> 0.0%.
- **Without compression, stalls persist at all buffer sizes.** At 32MB wbuf: `none` = 25.5%, `lz4` = 0.1%. Uncompressed SST files are larger, so compaction moves more data and falls behind.
- **32MB anomaly with `none` compression** (25.5% vs 12.6% at 16MB): each flush produces a large L0 file exceeding the target L1 file size, forcing full-overlap compaction rewrite.
- **zstd at 4MB is worst of all** (52.9%): high CPU cost slows flushes/compaction; with tiny buffer, flushes outpace compaction.

**Corrected NebulaStream implication:** With lz4 compression and >= 16MB write buffer, stalls drop below 1% — the combination effectively eliminates the stall problem. The earlier "23% stall rate" was a sampling artifact from missing-data bias.

### 2.6 OOM Kills Concentrate at High Memory Configurations

All 335 OOM kills occurred exclusively in `readwhilewriting` (zero in `fillrandom`).

**OOM count by write_buffer_size x cache_size:**

| | 8MB | 32MB | 64MB | 128MB | 256MB |
|---|-----|------|------|-------|-------|
| **4MB** | 0 | 0 | 0 | 0 | 0 |
| **16MB** | 0 | 0 | 0 | 0 | 0 |
| **32MB** | 0 | 0 | 0 | 0 | 0 |
| **64MB** | 0 | 0 | 0 | 0 | 79 |
| **128MB** | 7 | 8 | 7 | 35 | 199 |

The pattern is clear: when `write_buffer_size + cache_size` approaches the container memory limit, compaction and iterator buffers push total RSS over 512MB. The 128MB wbuf + 256MB cache combination (384MB committed before compaction even starts) OOMs in 199/400 runs (50%).

**Rule of thumb:** `write_buffer_size + cache_size` should not exceed ~50% of container memory.

### 2.7 Latency Profile

**fillrandom** — consistently low regardless of configuration:

| Percentile | Mean Latency |
|-----------|-------------|
| p50 | 0.9 µs |
| p99 | 5.2 µs |
| p99.9 | 25.3 µs |

**readwhilewriting** — high tail latency, cache helps at the tail:

| Cache Size | p50 | p99 | p99.9 |
|-----------|-----|-----|-------|
| 8MB | 19.5 µs | 619 µs | 16,808 µs |
| 32MB | 16.9 µs | 659 µs | 10,046 µs |
| 64MB | 14.2 µs | 667 µs | 6,483 µs |
| 128MB | 14.8 µs | 737 µs | 7,407 µs |
| 256MB | 24.7 µs | 766 µs | 7,720 µs |

While median and p99 show little improvement from cache, p99.9 improves 2.5x going from 8MB to 64MB cache. The 256MB cache regresses at p50 and p99 due to memory pressure effects.

---

## 3. Top Configurations

### Best for fillrandom (pure write ingestion)

| Compression | Write Buffer | Cache | Ops/sec | CV |
|-------------|-------------|-------|---------|-----|
| none | 32MB | 64MB | 527,000 | 8.8% |
| lz4 | 32MB | 64MB | 526,000 | 8.6% |
| snappy | 32MB | 64MB | 525,000 | 8.3% |

### Best for readwhilewriting (concurrent read+write)

| Compression | Write Buffer | Cache | Ops/sec | CV |
|-------------|-------------|-------|---------|-----|
| lz4 | 128MB | 8MB | 60,174 | 47.9% |
| lz4 | 128MB | 64MB | 58,522 | 43.1% |
| lz4 | 32MB | 128MB | 55,203 | 18.2% |

Note: The top readwhilewriting configs have high CV — the 3rd-ranked config (lz4/32MB/128MB) is the most reliable high-performer.

---

## 4. Data Quality Assessment

| Metric | Value |
|--------|-------|
| Total configs | 197 (3 lost to near-total OOM) |
| CV < 10% (stable) | 64 configs (32%) |
| CV 10–20% (moderate) | 67 configs (34%) |
| CV > 20% (noisy) | 64 configs (32%) |
| Mean CV | 0.19 |
| Median CV | 0.12 |

The high CV in some configs is expected — `readwhilewriting` is inherently noisy due to compaction timing variability. With 50 reps, the standard error of the mean is reduced by ~7x, providing reliable mean estimates even for high-variance configs.

---

## 5. Implications for NebulaStream Edge Deployment

1. **Always enable lz4 compression.** It provides a 4.4x throughput improvement for concurrent read-write workloads at negligible CPU cost. This is the single most impactful tuning decision.

2. **Cache size is surprisingly unimportant.** On a 512MB device, allocating only 8–32MB to block cache frees 100–200MB for stream operator state with virtually no throughput penalty. This directly benefits NebulaStream's memory budget.

3. **Write buffer sizing is a throughput-vs-stability tradeoff.** For stream operators doing concurrent reads and writes: 32–64MB write buffers balance throughput, write amplification, and OOM risk. Avoid 128MB buffers unless memory headroom is generous.

4. **Write stalls are solvable with the right configuration.** The corrected analysis shows lz4 + >= 16MB write buffer reduces stalls to < 1%. Without compression, stalls remain severe (up to 25%) even at large buffer sizes. Compression is the key enabler for stall-free operation.

5. **Memory budget rule:** Keep `write_buffer_size + cache_size` under 50% of total device memory. On a 512MB device, that means wbuf + cache <= 256MB. Exceeding this risks OOM kills under concurrent workloads.

---

## 6. Recommended Configuration for 512MB Edge Device

| Parameter | Recommended | Rationale |
|-----------|------------|-----------|
| `compression_type` | lz4 | 4.4x read-write throughput, low CPU overhead |
| `write_buffer_size` | 32MB | Best fillrandom throughput, good readwhilewriting, low OOM risk |
| `cache_size` | 32MB | Minimal throughput loss vs larger caches, frees memory for operators |
| **Total RocksDB memory** | **~64MB** | Leaves ~448MB for NebulaStream operators and OS |

---

## 7. Open Questions

- Does the cache-size insensitivity hold at larger dataset sizes (100M keys)?
- Would rate-limited ingestion (realistic sensor rates) reduce the 23% stall rate?
- Should `deleterandom` workload be tested to simulate window expiration / state cleanup?
- How does `readwhilewriting` behave with read-heavy ratios (e.g., 10:1 read:write)?
