# 10,044-cell Synthetic-Workload Evaluation of the NES OOC Backend

> Self-contained deliverable for the **synthetic-data characterisation phase** of
> the bachelor thesis *"Out-of-Core State Management for NebulaStream using
> RocksDB"*. The dataset and figures in this folder come from a fully-completed
> 54-rep sweep (3.4 days of compute, Thu 2026-05-28 19:23 → Mon 2026-06-01 04:23
> CEST) on a single Linux host (i7-10700K, 32 GB RAM, native Docker). A
> later phase refined these results into the final thesis evaluation (Chapter 5,
> including Nexmark); see the [top-level README](../README.md).

## What's in this folder

```
runs.tsv                 — 10,044 rows × 31 columns. One row per cell.
figs/
  fig_H1_wbuf.png        — Sweep C: throughput vs write_buffer_size
  fig_H2_stall.png       — write-stall fraction vs wbuf (H2 verdict)
  fig_H3_compression.png — Sweep B: lz4 / none / zstd comparison
  fig_P_knee.png         — Sweep A: P-knee on (P × state × cap)
  fig_survival_P.png     — Sweep A: survival rate per (P, state, cap)
  H1-H5-verdicts.md      — data-derived verdict table for the proposal's H1–H5
EXPERIMENT-PLAN.md       — the experimental design (matrix, hypotheses, sweeps)
```

The harness that produced this data, the workload `.test` files, and the
analyzer that generated the figures live alongside the rest of the project at
`../nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/e3big/`
(directly reproducible via `bash gen-matrix.sh && bash launch.sh 4 && bash
collect.sh && python3 analyze.py runs.tsv figs`).

## Matrix at a glance

| Sweep | Question | Cells |
|-------|----------|-------|
| **A** — Survival & residency | RQ1, H4 — does OOC survive state > memory; where is the P-knee? | 5,940 |
| **B** — Compression | RQ2, H3 — is compression mandatory? | 810 |
| **C** — Write buffer | RQ2, H1 — peak throughput vs wbuf | 1,080 |
| **D** — Block cache | RQ2 | 810 |
| **F** — Watermark | H4 — do 70/90 % watermarks prevent OOM at large state? | 486 |
| Refs — in-mem baselines | RQ1 §3.3.1, RQ2 overhead denominator | 918 (648 capped + 270 uncapped) |
| **Total** | | **10,044** |

All cells run in a Docker container with `--cpus=4 --memory=cap_mb`, single-node
embedded systest binary, tumbling-window aggregate query, Void sink to keep the
result-check OOM out of the measurement.

## Status mix observed

| Status | Count | Note |
|--------|-------|------|
| `ok`   | 9,089 (90.49 %) | Cell completed; throughput / latency / metric numbers in TSV |
| `oom`  | 789 (7.86 %) | Cell SIGKILL'd by docker memory cap (rc=137). Expected at high-state low-cap boundary cells |
| `err`  | 166 (1.65 %) | rc=141 — NES global buffer pool exhaustion. **This is a documented failure mode**, not a harness bug: appears at scale=5M / cap=512 / P∈{2,4,8} where the cap is wide enough that the OS doesn't OOM-kill the worker, but the in-mem pipeline outruns spill release before the pool can refill |

The two-mode failure surface (OS-OOM vs NES-pool starvation) is part of the
research finding, not noise — see `figs/H1-H5-verdicts.md` and §11 of
`EXPERIMENT-PLAN.md` for the interpretation.

## Data-derived verdicts on H1–H5 (proposal §2.2)

Full text in `figs/H1-H5-verdicts.md`; summary:

| Hypothesis | Verdict |
|------------|---------|
| **H1** "throughput peaks at lz4 / wbuf=64 / cache=32; ≥30 k tup/s" | **Reframed** — median 19,930 tup/s end-to-end; the ≥30k was a db_bench number, not an end-to-end NES throughput. Report integrated overhead vs in-mem. |
| **H2** "write stalls < 5 % at wbuf ≥ 64" | **CONFIRMED** — median 0.00 % stall fraction |
| **H3** "compression is mandatory (none ⇒ ≥4× read penalty)" | **Partly refuted** for throughput (lz4 vs none ≈ 19,944 vs 20,004 t/s), but footprint differs. Compression's value at this workload is storage, not speed — runs were too short to exercise compaction. |
| **H4** "OOM ceiling = 2·wbuf+cache+~100 MB; 70/90 % watermarks safe" | **Partly superseded** — the binding constraint at large state turned out to be the **terminal-flush burst**, not the watermark. Partition-at-spill (P>1) is what removed the OOM; the watermark model is reported in that revised context. |
| **H5** "spilling 100 MB ≈ 2 s" | Needs sec-per-100MB calc against `figs/` data — per-spill p50/p99 µs medians: 41,181 / 81,401; median spill_dir 43 MB. |

## Schema reference (`runs.tsv`)

31 tab-separated columns; first row is the header.

| Col | Field | Meaning |
|----:|-------|---------|
| 1 | `label` | unique cell identifier — encodes sweep + axis values + rep |
| 2 | `variant` | `ooc`, `ooc_skew`, `inmem`, `inmem_uncapped` |
| 3 | `compression` | `lz4`, `zstd`, `none` |
| 4 | `state_tuples` | source-side tuple count (Generator) |
| 5 | `cap_mb` | docker `--memory` per-cell cap; `none` for uncapped refs |
| 6 | `spill` | `on` / `off` |
| 7 | `pool` | NES global buffer pool size (count of 4 KB buffers) |
| 8 | `soft_mb` | soft spill threshold |
| 9 | `hard_mb` | hard spill threshold |
| 10 | `emit_lag_ms` | retained band of completed-but-unemitted windows |
| 11 | `threads` | worker thread count |
| 12 | `wbuf_mb` | RocksDB write_buffer_size |
| 13 | `cache_mb` | RocksDB block_cache_size |
| 14 | `num_partitions` | P — keyed-state hash-partition count |
| 15 | `rc` | systest return code (0 / 137 / 141 / …) |
| 16 | `status` | `ok` / `oom` / `err` |
| 17 | `elapsed_s` | wall-clock of the systest invocation |
| 18 | `throughput_tps` | tuples/sec (`state_tuples / elapsed_s`) for ok cells |
| 19 | `peak_mb` | cgroup `memory.peak` |
| 20 | `store_peak_mb` | spill backend resident peak (1/P-bounded) |
| 21 | `slices_spilled` | count of slices spilled |
| 22 | `bytes_restored` | bytes read back from spill |
| 23 | `spill_dir_mb` | `du -sb /tmp/nes-spill-<label>` at end of run |
| 24 | `write_amp` | RocksDB `bytes_compacted / bytes_flushed` |
| 25 | `sst_footprint_b` | sum of L1+ SST file sizes |
| 26 | `write_stall_micros` | total µs spent in write stall |
| 27–29 | `p50_us`, `p95_us`, `p99_us` | per-task latency from NES_INFO `Task latency us:` |
| 30, 31 | `spill_p50_us`, `spill_p99_us` | per-slice spill latency |

`NA` indicates either a crashed cell or a metric that doesn't apply to that
variant (e.g., RocksDB stats for `inmem*` variants). The 10k phase used a
simpler NA-vs-zero policy than the upcoming Nexmark phase; analyses that
group/aggregate should filter `variant.startswith("ooc") AND status=="ok"`
before computing means over the RocksDB-specific columns.

## How to reproduce

```bash
# In ../nebulastream-Out-of-Core-Stream-Processing/, after building the systest binary:
cd .planning/eval-phase/e3big
REPS=54 bash gen-matrix.sh                                # writes jobs.txt (10044 lines)
nohup bash launch.sh 4 > launch.out 2>&1 & echo $! > launch.pid; disown
# … wait ~3.5 days, then:
bash collect.sh
python3 analyze.py runs.tsv figs
```

The `runs.tsv` and `figs/` in this folder are the outputs of that pipeline,
copied here verbatim so reviewers don't need to navigate into `.planning/`.

## Caveats

- The compression sweep (Sweep B) didn't exercise compaction strongly because
  per-cell event-time is short relative to RocksDB's compaction trigger. The
  Nexmark phase uses scales up to 40 M tuples and gets meaningful compaction.
- The Sweep B/C/D sweeps were run only at states ≤ 2 M tuples; the H1 chart's
  s=5M data point is from Sweep A (single wbuf=64 baseline), not a separate
  Sweep C s=5M sweep. Don't read it as a wbuf-curve point.
- `write_amp` is 0 for many ok cells because runs were too short to trigger
  RocksDB compaction; a 0 here means "no compaction observed in this run
  window", not "no amplification capability of the workload."
- `NA` for inmem variants on RocksDB-specific columns is correct semantics
  (RocksDB was not active). The Nexmark-phase TSV will be more careful about
  distinguishing "measured zero" from "not applicable."
