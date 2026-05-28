# EXPLORE: Proposal + Microbenchmark — Inputs for the OOC-Spill Evaluation Plan

Authoritative source: `DIMA_Thesis_Proposal_V2.4_signed.pdf` (signed 26.01.2026), 9 pages.
Reuse source: `Microbenchmark-BA/` (prior RocksDB microbenchmark, 10k runs at 512 MB).

Title: **Out-of-Core Stream Processing in NebulaStream** (Bachelor's, 12 ECTS, 01/2026–06/2026).

---

## 1. The 3 Research Questions (verbatim, proposal §2.2, pp. 2–3)

§2.2 phrases the research problem as a sequence of questions. The three RQs are:

> **RQ1 (integration):** "How can an Out-of-Core storage solution be integrated into NebulaStream's push-based execution model?"

> **RQ2 (performance of a research-informed config):** "How does a research-informed RocksDB configuration perform in terms of throughput and tail latency when used as an Out-of-Core state backend for tumbling windows in NebulaStream? What is the latency and CPU-time cost of spilling and restoring operator state when the state size exceeds available memory in NebulaStream?"

> **RQ3 (resource scaling / efficiency):** "How does CPU utilization and disk I/O activity evolve as operator state grows beyond memory capacity with an Out-of-Core storage backend in NebulaStream? How efficiently does RocksDB utilize storage for Out-of-Core operator state in NebulaStream, as reflected in write amplification and storage footprint?"

Overarching goal (§2.1, p. 2): *"enable storage of a tumbling-window operator state larger than memory using an empirically informed RocksDB configuration as the state backend in NebulaStream."*

Note: the three RQs map cleanly onto the three thesis contributions — (1) interface/integration, (2) throughput + tail latency + spill/restore cost, (3) CPU + disk I/O + write-amp + storage footprint.

---

## 2. Milestones MS3 / MS4, Success Criteria, Timeline

### Milestones (proposal §4 "Implementation Plan and Timeframe", p. 4)

| MS | Dates | Tasks | Deliverables |
|----|-------|-------|--------------|
| **MS3** | 26.02 – 25.03 | 6. Implement the interface · 7. Integrate interface into NebulaStream · 8. Validate interface solution with Docker | Code for interface · Docker-Setup with configuration · "Test with systest" |
| **MS4** | 26.03 – 15.04 | 9. Integrate selected metrics into Docker environment · 10. Design experiment + evaluation metrics · 11. Conduct experiments | Implement metrics/functions for data collection · Design system plan with experiment definition · **Collected Data** |

So: **MS3 = build + integrate + validate** the RocksDB OOC interface (extends `WindowSlicesStoreInterface`, see MS2 task 5) inside NebulaStream, validated via Docker + the NES `systest` harness. **MS4 = the evaluation itself** — wire metric collection into the Docker env, design the experiment + metrics, and run it to produce the collected dataset. MS4 is therefore the milestone this eval plan serves.

Surrounding milestones for context:
- **MS2** (12.02–25.02): design extension interface on existing `WindowSlicesStoreInterface` + set up Docker env; define "research-informed configuration" (also a MS1 task).
- **MS5** (16.04–30.04): analyze results, build visualizations, form supporting argument (this is where the microbench analysis assets get reused).
- **TC** thesis-complete (01.05–27.05): writeup + draft feedback; **submit on/before 22.05.26** for review.
- **SC2** system-complete (28.05–10.06): "Finalize system and make PR available" + implement reviewed recommendations; publish code in open repo + thesis submission.

### Success criteria (proposal §3.3, p. 3)

> **§3.3 (definition of success):** "Success is defined by the ability to process tumbling-window workloads whose operator state exceeds physical memory without out-of-memory failures during processing."

This is the headline **"process tumbling state > memory without OOM"** criterion.

> **§3.3.1 (effectiveness):** "Effectiveness is established through verification against a sufficient memory baseline." (i.e. show the OOC build survives a state size that crashes the in-memory baseline.)

> **§3.3.2 (efficiency, baseline + metrics):** "Efficiency is measured relative to the standard state backend baseline, executed under identical CPU constraints but without enforced memory limits. Throughput and tail latency are used to quantify the overhead introduced by Out-of-Core state management. CPU utilization, disk I/O activity, write amplification, and storage footprint are measured for the RocksDB-based solution to assess resource efficiency."

**Baseline definition (critical):** the *standard in-memory state backend*, run under **identical CPU limits but WITHOUT a memory cap**. The OOC variant runs under both CPU and memory caps. Overhead = OOC throughput/tail-latency vs that baseline. No quantitative numeric target is stated in the proposal — success is qualitative (no OOM) + comparative (quantify overhead). The microbench's own numbers (e.g. lz4 4.4× over none) are the closest quantitative anchors and live in the microbench, not the proposal.

### Methodology committed in the proposal (§3.1, p. 3)

- Environment: **reproducible single-node Docker** enforcing **strict memory + CPU limits**, with a **locally placed RocksDB storage directory** ("locally placed RocksDB storage directory").
- State backend: RocksDB configured from a **research-informed baseline** (RQ2). Soft + hard memory **watermarks**: soft watermark triggers *preemptive spilling of cold window state*; hard watermark *enforces mandatory spilling* to prevent OOM.
- Workloads: **synthetic, tumbling-window state that exceeds available memory under skewed and bursty input** typical of smart-city sensor streams.
- Metrics collected (§3.1): **throughput, tail latency (p95, p99), spill + restore latency, CPU utilization, disk I/O activity, write amplification, storage footprint.** Supported by RocksDB internal statistics + OS performance counters.

### Scope / out-of-scope for evaluation (§2.3, p. 3)

- **In scope (§2.3.1):** RocksDB OOC state backend for **tumbling windows** via a state interface; evaluation over a **parameter space indicated by prior research** (= the microbench); experimental analysis of throughput, latency, CPU, disk I/O, storage efficiency.
- **Out of scope (§2.3.2):** distributed execution, state migration, fault tolerance / crash recovery / WAL-based durability, modifications to NebulaStream's Nautilus JIT or code-gen pipeline, automatic parameter tuning, **exhaustive configuration search**, and **cross-system comparisons to alternative state backends**. Implementation targets **tumbling windows only — sliding windows excluded**, and general-purpose serialization is excluded. OOC extends memory **only for persistent operator state**, not intermediate results or input buffers.

**Implication for the eval plan:** do NOT re-run a full RocksDB grid sweep (out of scope + already done by the microbench). The integrated-system eval adopts ONE research-informed config and measures spill/restore behavior in NebulaStream. Metrics that "matter" per proposal: throughput, **tail latency p95/p99** (note: proposal says p95+p99; microbench reports p99/p99.9/p99.99), **spill latency**, **restore latency**, CPU, disk I/O, write amp, storage footprint. The OOM-survival check is the binary success gate.

---

## 3. Microbenchmark — what it benchmarked + key findings

Files: `Microbenchmark-BA/results/constrained_512mb/report_preliminary.tex`, `scripts/analyze.py`, `EXPERIMENT_PLAN.md`, `CLAUDE.md`.

### What was benchmarked
- Tool: RocksDB `db_bench` (native C++) inside Docker.
- Workloads (constrained 512 MB final run): **`fillrandom`** (pure sensor ingestion) and **`readwhilewriting`** (concurrent state lookups during ingestion). `readrandom`, `overwrite`, `deleterandom`, `mixgraph` exist in code (`config.ALL_WORKLOADS`) but were not in the 10k run.
- Knobs swept (512 MB run, `report_preliminary.tex` p.1 setup table): **4 compression × 5 write_buffer_size × 5 cache_size = 100 configs × 2 workloads × 50 reps = 10,000 runs** (9,665 ok / 335 OOM).
  - `compression_type` ∈ {none, snappy, lz4, zstd}
  - `write_buffer_size` ∈ {4, 16, 32, 64, 128} MB
  - `cache_size` (block cache) ∈ {8, 32, 64, 128, 256} MB
  - **Fixed:** `level0_slowdown_writes_trigger=20`, `min_level_to_compress=-1`, `max_write_buffer_number=2`. Dataset: 2 M keys × (16 B key + 100 B value).

### Key findings (from `report_preliminary.tex`)
1. **Compression is the dominant knob (~4.4×).** lz4 gives 38,072 rww ops/sec vs 8,723 for none = **4.4× speedup**; in worst configs (no compression, wbuf ≥ 32 MB) the gap is **28.5×** (45,268 vs 1,587 ops/sec). lz4 also has write-amp ≈ 1.00× vs 2.01× for none. (§Results-1.)
2. **Cache size is surprisingly unimportant.** Block-cache hit rate scales 5.8 % (8 MB) → 77 % (256 MB) but throughput is flat; **256 MB cache performs *worst* for rww** (competes with compaction buffers). 64 MB is the sweet spot, 8–32 MB nearly free. (§Results-2.)
3. **Write buffer = opposite effects per workload.** fillrandom peaks at 32 MB then degrades at 128 MB (flush latency); rww improves monotonically with larger wbuf. Compaction I/O drops 11× (13.4 → 1.2 GB) from 4→128 MB wbuf — matters for SD-card lifespan. (§Results-3.)
4. **Stall NaN bug (methodology correction).** `db_bench` only emits stall stats when stalls occur; the original analysis treated missing data as "unknown" and over-estimated mean stall rate (the bogus "23 % stall rate"). The corrected analysis treats missing stall data as **0 %** (`_prep_ok()` in analyze.py fills NaN→0). Corrected result: lz4 + wbuf ≥ 16 MB ⇒ < 1 % stalls. (§Results-4.) This is the documented "NaN-in-stall-data bug" and the data-quality checklist item in `CLAUDE.md`.
5. **OOM boundary.** All 335 OOM kills were in `readwhilewriting`; rule of thumb **`write_buffer_size + cache_size ≤ 50 % of container memory`** (worst cell: wbuf=128 MB + cache=256 MB → 50 % OOM). (§Results-5.) Memory model in `config.py`: `usage ≈ 2×wbuf + cache + ~100 MB overhead`.
6. **Tail latency:** uncompressed storage shows 3–5× higher P99. (§Results-6.)

---

## 4. Research-informed RocksDB config to ADOPT (for RQ2)

From `report_preliminary.tex` §"Recommended Configuration for 512 MB Edge Device" (the microbench's explicit recommendation):

| Parameter | Adopt value | Rationale (from microbench) |
|-----------|-------------|------------------------------|
| `compression_type` | **lz4** | 4.4× rww throughput gain, negligible CPU cost, write-amp ≈ 1.0× |
| `write_buffer_size` | **32 MB** | Best fillrandom, good rww, low OOM risk |
| `cache_size` (block cache) | **32 MB** | Minimal throughput loss, frees RAM for stream operators |
| (fixed) `level0_slowdown_writes_trigger` | 20 | unchanged from sweep |
| (fixed) `min_level_to_compress` | -1 | unchanged from sweep |
| (fixed) `max_write_buffer_number` | 2 | unchanged from sweep (flagged as a stall-mitigation lever) |

**Total RocksDB memory ≈ 64 MB**, leaving ~448 MB for NebulaStream + OS at a 512 MB ceiling. This is the config the NebulaStream integration eval should use as its single "research-informed RocksDB config."

Caveats to carry into the plan:
- The recommendation is for a **512 MB** ceiling. If the integrated eval uses a different memory ceiling, re-derive cache/wbuf against the `2×wbuf + cache ≤ 50 % memory` rule.
- The microbench flagged a **follow-up stall sweep** (not run): `max_write_buffer_number ∈ {2,3,4}`, `level0_slowdown_writes_trigger ∈ {20,40,60}`, `level0_stop_writes_trigger ∈ {36,64,100}` with lz4/32 MB/32 MB fixed. Optional input if integrated runs still show stalls.
- The microbench also notes **FIFO compaction** as a candidate for time-windowed (tumbling) state (near-zero write-amp) — directly relevant to tumbling-window OOC and worth considering for the NebulaStream config.

---

## 5. Reusable microbench assets (ranked, file:function)

All paths under `Microbenchmark-BA/`. Ranked by reuse value for analyzing the integrated-system runs.

1. **`scripts/analyze.py:generate_report_figures()`** (lines 607–915) — the entire publication figure pipeline (fig1–fig9 + extras): ops vs cache/wbuf by compression, ops heatmaps, OOM matrix, stall plots, write-amp, cache-hit-rate, **P99 latency vs cache (fig9, lines 859–872)**. Highest-value asset: lift wholesale, swap the CSV source + axis labels (cache_size → state_size / time).
2. **`scripts/analyze.py` plot helpers** — `_heatmap()` (587–604), `_save()` (581–584), `_clipped_yerr()` (624–627, prevents negative error bars), `_human_bytes()` (53–61), and the `COMP_ORDER` / `COMP_COLORS` / `WBUF_MARKERS` style constants (555–557). Reusable as a small plotting toolkit.
3. **`scripts/analyze.py:_prep_ok()`** (560–566) — the **NaN→0 stall/write-amp correction** + `status=="ok"` filter. Encodes the documented stall-NaN bug fix; must be reused so the integrated analysis doesn't repeat the over-estimation error.
4. **`scripts/analyze.py:_collapse_reps()` + `print_variance_report()`** (331–378) — rep-aware aggregation into mean/std/count + **95 % CI** (`1.96·std/√n`) and **coefficient-of-variation** data-quality report (CV<10 % good / 10–20 % ok / >20 % noisy). Directly serves the `CLAUDE.md` data-quality checklist for the integrated runs.
5. **`scripts/parse_output.py`** (whole file) — regex parser for `db_bench` output → `BenchmarkResult`. Reusable functions especially: `parse_percentiles()` (p50/p75/p99/p99.9/p99.99), `parse_write_amp()` (flush+compact / user bytes), `parse_block_cache_hit_rate()`, `parse_stall_percent/micros()`, `parse_compact_read_gb()/flush_write_gb()`. If NebulaStream exposes RocksDB statistics in the same string format, these parse it as-is; otherwise port the metric definitions (esp. the write-amp formula).
6. **`scripts/config.py:CONSTRAINED_CSV_COLUMNS`** (291–321) — the **CSV schema** to adopt for the integrated runs: `run_id, workload, rep, status, run_duration_secs, <config knobs>, memory_limit_mb, ops_per_sec, micros_per_op, mb_per_sec, p50, p75, p99, p99_9, p99_99, block_cache_hit_rate, write_amp, compaction_write_gb, compact_read_gb, flush_write_gb, bytes_written, bytes_read, stall_percent, stall_micros, timestamp`. Extend with **`spill_latency`, `restore_latency`, `state_size`, `cpu_util`** for the proposal's new metrics. `status ∈ {ok, oom}` already supports the OOM-survival success gate.
7. **`results/constrained_512mb/report_preliminary.tex`** (whole file) — the **.tex report structure** (scrartcl, `\keyinsight` highlight box, color-coded result tables, per-finding section layout, footer provenance line). Reuse as the template for the integrated-system results chapter / figures.
8. **`scripts/analyze.py:analyze_comparison()`** (920–993) — side-by-side **baseline-vs-treatment** bar/line comparison (ops + P99 by compression). Directly adaptable to the proposal's required **in-memory-baseline vs OOC** comparison (§3.3.2): swap the two datasets, group by state-size instead of compression.
9. **`scripts/analyze.py:plot_oom_matrix()` / fig5** (473–518, 687–701) — OOM-rate heatmap; reusable to visualize where the OOC build survives vs OOMs across state sizes (the §3.3 success-criterion figure).

Lower priority: `run_benchmarks.py` (orchestrator) — the Docker-invocation pattern is reusable but the integrated eval drives NebulaStream, not `db_bench`, so only the harness scaffolding (run-id generation, rep loop, status capture, CSV append) ports over, not the `build_docker_cmd()` `db_bench` flags.

---

## 6. Memory-constraint mechanism used by the microbench

**Docker container memory limit via cgroups — not a RocksDB option, not a host cgroup.** In `scripts/run_benchmarks.py:build_docker_cmd()` (lines 121–126):

```
docker run --rm --memory={N}m --memory-swap={N}m --cpus=1 <image> ...
```

- `--memory={N}m` sets the hard container memory ceiling (enforced by the kernel cgroup; exceeding it ⇒ OOM kill, captured as `status=oom`, exit 137).
- `--memory-swap={N}m` set equal to `--memory` ⇒ **swap disabled** (so memory pressure can't be hidden by swapping).
- `--cpus=1` ⇒ single-core, simulating IoT/edge hardware.
- The 512 MB run used `--memory=512m --memory-swap=512m --cpus=1` (footer of `report_preliminary.tex`; default `MEMORY_LIMIT_MB=256`, overridable via `--memory-limit`).
- Direct I/O (`O_DIRECT`) was **disabled** — it fails/stalls on Docker Desktop virtiofs (`config.py` `CONSTRAINED_FIXED_PARAMS` comment); Docker `--memory` alone enforces the limit. The proposal independently calls for the same Docker memory+CPU-limited single-node setup (§3.1), so this is the natural mechanism for the integrated eval's memory ceiling.

**Recommended for the integrated eval:** reuse the exact `docker run --memory --memory-swap --cpus=1` pattern so the OOC NebulaStream build runs under the same enforced ceiling, and the in-memory baseline runs with `--cpus=1` but **no `--memory` flag** (per §3.3.2 "identical CPU constraints but without enforced memory limits").
