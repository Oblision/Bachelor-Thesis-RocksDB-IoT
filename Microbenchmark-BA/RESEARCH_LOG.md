# Research Log — RocksDB Microbenchmark for NebulaStream

> **Thesis:** Out-of-Core State Management for NebulaStream using RocksDB
> **Author:** Benjamin Steinbüchel
> **Institution:** TU Berlin, DIMA Group
>
> This log documents every major decision, insight, methodology change, and AI interaction
> throughout the thesis. Entries are reverse-chronological (newest first). Each entry is tagged
> with an AI involvement level for full transparency.

---

## Process: The HCRAID Experiment Cycle

Every experiment follows six phases:

1. **Hypothesize** — Write a falsifiable statement BEFORE running. State dependent/independent/control variables.
2. **Configure** — Define parameter space in `config.py`. Run `--dry-run`. Validate with quick subset (3–5 configs).
3. **Run** — Execute with monitoring. Record environment, start/end times, failure counts.
4. **Analyze** — Complete the data quality checklist before any interpretation:
   - [ ] Row count matches expected (configs × reps)
   - [ ] NaN inspection (db_bench 0→NaN bug — see Entry 2026-03-28b)
   - [ ] OOM runs marked and excluded from means
   - [ ] CV reported per config
   - [ ] No duplicate run_ids
5. **Interpret** — For each finding: state evidence, confidence level, alternative explanations. Tag AI involvement.
6. **Document** — Write entry to this log. Update reports. Define next hypothesis.

### AI Transparency Tags

| Tag | Meaning | Documentation Required |
|-----|---------|----------------------|
| `AI: None` | Student only | — |
| `AI: Consulted` | Asked AI, decided independently | What was asked |
| `AI: Assisted` | AI helped write code/text, student reviewed | Review notes |
| `AI: Generated` | AI produced output | Full verification checklist |
| `AI: Corrected` | Student found and fixed an AI error | The error and the fix |

---

## Timeline

---

### 2026-03-28c — Config Consistency Verification (1k vs 9k Batches)
**Phase:** Analysis
**AI Role:** AI: Assisted

Verified that the first 1,000 runs (completed 2026-03-05) and the subsequent 9,000 runs (started 2026-03-25) use **identical parameter spaces**:
- Same 4 compression types, 5 write buffer sizes, 5 cache sizes, 2 workloads
- All 200 unique config groups have exactly 50 repetitions
- Memory limit consistently 512MB across both batches

There is a 20-day gap between batches (Mar 5 → Mar 25), but no configuration drift occurred.

**Evidence:** `results/constrained_512mb/parsed/summary.csv` — grouped by (compression, wbuf, cache, workload), all groups = 50 reps
**Open Questions:** None — configs are consistent. Data can be treated as a single dataset.

---

### 2026-03-28b — NaN Bug Discovery & Stall Data Correction
**Phase:** Analysis
**AI Role:** AI: Corrected

**Discovery:** `db_bench` does not report stall statistics when zero stalls occur. The parser (`parse_output.py`) correctly returns `None`/`NaN` for missing fields, but this created a **sampling bias** during analysis: only runs with stalls had stall data, so the mean was computed only over stalling runs.

**The error:** AI-generated analysis initially reported a mean stall rate of ~23% for `readwhilewriting`. This was wrong — it excluded all zero-stall runs from the denominator.

**The fix:** Treat all NaN stall values as 0% stalls. This is correct because:
- `db_bench` only omits stall output when stalls = 0
- At large `write_buffer_size` values, fewer flushes mean fewer L0 file pileups, genuinely eliminating stalls

**Corrected finding:** With lz4 compression + ≥16MB write buffer, stalls drop below 1%. The "23% stall rate" was a sampling artifact.

**Critical thinking demonstrated:** Student identified that AI analysis was systematically biased by treating missing data as "unknown" rather than "zero."

**Evidence:** `results/constrained_512mb/PRELIMINARY_REPORT.md` Section 2.5, `scripts/analyze.py` NaN handling
**Open Questions:** Should `parse_output.py` be patched to emit 0 instead of None for missing stall data? (Tradeoff: explicit zeros vs. preserving raw parser behavior)

---

### 2026-03-28a — Preliminary Report Generation
**Phase:** Analysis → Writing
**AI Role:** AI: Generated

AI generated `PRELIMINARY_REPORT.md` from the 10,000-run dataset. Key claims in the report:

| # | Claim | Status |
|---|-------|--------|
| 2.1 | lz4 gives 4.4x throughput for readwhilewriting | **Unverified** |
| 2.2 | No performance cliff from cache size | **Unverified** |
| 2.3 | Write buffer has opposite effects on two workloads | **Unverified** |
| 2.4 | Write amplification driven by buffer size, not cache | **Unverified** |
| 2.5 | Stall correction (NaN→0) | **Verified** (see 2026-03-28b) |
| 2.6 | OOM at high wbuf+cache combos | **Unverified** |
| 2.7 | Cache helps p99.9 latency | **Unverified** |

**ACTION:** Each claim needs cross-checking against `summary.csv` before inclusion in thesis.

**Evidence:** `results/constrained_512mb/PRELIMINARY_REPORT.md`
**Open Questions:** Several AI-written explanations need clarification — report proofreading in progress.

---

### 2026-03-25 → 2026-03-27 — Second 512MB Batch (9,000 runs)
**Phase:** Data Collection
**AI Role:** AI: None

Continued the 512MB sweep with 9,000 additional runs, completing the full 10,000-run target. Execution ran over ~3 calendar days with ~250 CPU hours.

All 200 unique configurations now have 50 repetitions each (verified 2026-03-28c).

**Evidence:** `results/constrained_512mb/parsed/summary.csv` rows 1001–10000, timestamps 2026-03-25 to 2026-03-27
**Open Questions:** None — data collection phase complete.

---

### 2026-03-04 → 2026-03-05 — First 512MB Batch (1,000 runs)
**Phase:** Data Collection
**AI Role:** AI: None

First batch of the 512MB constrained sweep. 1,000 data points collected as initial validation before committing to the full 10,000-run sweep.

Confirmed:
- OOM rate ~3.4% (vs 35% at 256MB) — the larger container makes most configs viable
- All 4 compression types and 5 cache/buffer sizes represented
- Orchestrator resume mechanism works across interrupted sessions

**Evidence:** `results/constrained_512mb/parsed/summary.csv` rows 1–1000, timestamps 2026-03-04 to 2026-03-05
**Open Questions:** Is 50 reps per config sufficient for statistical reliability? (Answered: yes — reduces SE by ~7x)

---

### 2026-03-04 — Pivot to 512MB Market-Fit Target
**Phase:** Design
**AI Role:** AI: Consulted

**Decision:** Shift from 256MB to 512MB container memory to better match the current IoT market. Raspberry Pi 4 (the dominant edge device class) has 1–4GB RAM; a 512MB container simulates a realistic memory budget for one stream processing operator.

**Rationale:**
- 256MB was too constrained — 35% OOM rate made many configs untestable
- 512MB is the realistic floor for NebulaStream deployments
- Allows testing larger cache sizes (up to 256MB) to find the actual performance curve

**New parameter space:**
- 5 cache sizes: 8, 32, 64, 128, 256 MB
- 5 write buffer sizes: 4, 16, 32, 64, 128 MB
- 4 compression types: none, snappy, lz4, zstd
- 2 workloads: fillrandom, readwhilewriting
- 50 repetitions per config
- **Total: 10,000 runs**

**Evidence:** `scripts/config.py` — `CONSTRAINED_512MB_*` parameter definitions
**Open Questions:** Should we also test at 1GB to capture the upper range? (Deferred — 512MB provides the critical data point)

---

### 2026-03-03 — First Constrained Run (256MB, 100 configs)
**Phase:** Data Collection → Interpretation
**AI Role:** AI: Consulted

Ran 100 configurations in 256MB Docker-constrained mode with `readwhilewriting` workload.

**Results:**
- 65 runs completed successfully, 35 OOM kills (35% failure rate)
- OOM kills concentrated at `write_buffer_size + cache_size > ~200MB`
- Single run per config — no variance estimates possible

**Key Insight:** Sample size is too small for feasible statistical analysis. With only 1 run per config:
- Cannot compute standard deviation or confidence intervals
- Cannot distinguish signal from noise in throughput measurements
- `readwhilewriting` is inherently noisy due to compaction timing

This directly motivated the pivot to 512MB mode with 50 repetitions per config.

**Evidence:** `results/constrained/parsed/summary.csv` (101 rows including header)
**Open Questions:** None — this run served its purpose as a pilot.

---

### 2026-03-02 — Unconstrained Baseline Runs
**Phase:** Data Collection
**AI Role:** AI: None

Ran db_bench natively on macOS without memory constraints. 256 parameter combinations × 7 workloads = 1,792 planned runs.

**Finding:** Compression type, L0 slowdown trigger, and min_level_to_compress had **negligible impact** on all metrics. The 1M key dataset (~100MB) fits entirely in memory, so:
- Block cache hit rate was always ~100%
- No write stalls occurred
- Write amplification was minimal

**Lesson:** Unconstrained mode is only useful as a baseline proving that parameter sensitivity emerges under memory pressure. The interesting science happens in constrained mode.

**Evidence:** `results/parsed/summary.csv`
**Open Questions:** None — baseline established.

---

### 2026-03-02 — Project Setup & Benchmark Architecture Design
**Phase:** Design
**AI Role:** AI: Assisted

Designed and implemented the benchmark orchestration framework:

- **Architecture:** Python orchestrator (`run_benchmarks.py`) drives RocksDB's native `db_bench` tool through all parameter combinations
- **Parallel execution:** ThreadPoolExecutor for concurrent runs
- **Constrained simulation:** Docker containers with `--memory` and `--cpus` flags
- **Resilience:** Resume after interruption, graceful OOM handling (exit 137), timeout protection
- **Output:** Structured CSV with 25+ metrics parsed from raw `db_bench` text output

AI assisted with code structure and Docker integration. Student designed the parameter space and experimental methodology.

**Evidence:** `scripts/run_benchmarks.py`, `scripts/config.py`, `scripts/parse_output.py`, `Dockerfile`
**Open Questions:** Whether to use Direct I/O (later disabled due to Docker virtiofs incompatibility)
