# Full Evaluation Plan — OOC Stream Processing in NebulaStream

Purpose: a single, reviewable plan that (1) maps each **RQ** to the runs that answer it, (2) maps each of the
**5 hypotheses (H1–H5)** to how it is tested — or why not applicable, (3) lists **every configuration setting**, and
(4) fixes the **repetitions per configuration** with a variance rationale. Executed by the parallel/resumable harness
in `.planning/eval-phase/e3big/` on a dedicated machine.

Per-run cost ≈ 2 min (full Docker systest). Variance so far (75+30 runs): throughput CV **0.005–0.079** at 3 reps.

---

## 0. Fixed setup (held constant unless it is the swept axis)
- Engine: `nes-development:local-rocksdb`, COMPILER mode, 4 worker threads, buffer pool 16000 (≈131 MB).
- Workload (Void sink, no result-check confounder): tumbling `GROUP BY key SUM(val)`, 100k-key windows.
  State size = retained completed-window band, set by `emit_lag` (ts step 1 ⇒ emit_lag ms ≈ retained keys).
- **Baseline "research-informed" config** (microbench-recommended): `compression=lz4`, `wbuf=64 MB`,
  `cache=32 MB`, `P=8`, `cap=512 MB`, `soft/hard=4/8 MB`, uniform keys.
- Metrics per run (all production NES_INFO lines, grep-parsed): exit/status (oom=137), throughput (tuples/s,
  external), process peak RSS (cgroup), store working-set peak, slices spilled, bytes restored, spill-dir size,
  spill latency p50/p95/p99 (H5), RocksDB write-amp + SST footprint + **write-stall µs (H2)**, per-task p50/p95/p99.

---

## 1. Research questions → how they are answered

| RQ | Question | Answered by | Primary metrics | Verdict criterion |
|----|----------|-------------|-----------------|-------------------|
| **RQ1** | Can an OOC backend be integrated into the push-based model so state > RAM completes? | **Sweep A** (survival) + big-key in-mem reference | exit status (oom vs ok) across P × state × cap × workload | §3.3 gate met if OOC completes where the comparison (naive P=1 / in-mem) is OOM-killed |
| **RQ2** | Throughput + tail latency of the research-informed config; cost of spill/restore | **Sweeps B,C,D** (compression/wbuf/cache) + latency everywhere + spill/restore latency | throughput vs in-mem ref (overhead %), per-task p95/p99, spill/restore latency | quantify overhead; tails not dominated by spill |
| **RQ3** | How CPU/disk/storage scale as state grows past memory | all OOC cells, scaled over state + config | write-amp, SST footprint, spill/restore bytes, write-stall µs | characterize scaling; identify dominant knob |

---

## 2. Hypotheses (H1–H5) → applicable? how tested? verdict criterion

| H | Statement (predicted) | Applicable? | Tested by | Verdict criterion | Notes |
|---|-----------------------|-------------|-----------|-------------------|-------|
| **H1** | read throughput ≥ 30k ops/s @ lz4/wbuf64/cache32, 512 MB | **Yes, reframed** | Sweep C (incl. wbuf=64) + baseline | report integrated throughput at the proposed config + overhead vs in-mem | our metric is *end-to-end tuples/s* (incl. agg+spill+emit), not db_bench read-ops/s — comparison is qualitative; the integrated overhead is the real RQ2 number |
| **H2** | write stalls < 5 % of execution time @ ≥64 MB buf + lz4 | **Yes** (needs the new stall metric) | Sweep E = write_stall_µs / elapsed across wbuf, P, state | stall fraction < 5 % at the proposed config | requires the H2 stall-metric code (in progress, TDD+commit) |
| **H3** | compression mandatory (none ⇒ 4× read penalty, 2.5 ms p99) | **Yes, direct** | Sweep B: compression ∈ {none, lz4, zstd} | none vs lz4 throughput / latency / footprint gap | zstd added as a bonus 3rd point |
| **H4** | OOM ceiling = 2·wbuf + cache + ~100 MB; 70 %/90 % watermarks prevent OOM | **Partial / partly superseded** | Sweep F: watermark sweep at large state | do 70/90 % watermarks prevent OOM while holding throughput? | our architectural finding: pre-partitioning the *terminal-flush burst* (not the watermark) was the binding constraint; partition-at-spill is what removed the OOM. We test the watermark model but report it in that revised context |
| **H5** | spilling a 100 MB window → ~2 s | **Yes, from data** | spill-latency metric × spilled-bytes (all OOC cells) | observed s-per-100 MB vs the ~2 s prediction | already captured per run; no extra runs, just analysis |

---

## 3. Configuration matrix — every setting

Design = **one-axis-at-a-time around the baseline + the survival cross** (interpretable, maps 1:1 to RQ/H; avoids a
combinatorial blow-up of meaningless cells). Each sub-sweep below lists the varied axis; all other knobs = baseline (§0).

### Sweep A — Survival & residency (RQ1, P-sweep, H4 context)
- `P` ∈ {1, 2, 4, 8, 16}
- `state` (emit_lag) ∈ {400k, 800k, 1.2M, 1.6M, 2M, **5M**} (5M forces even in-mem to OOM)
- `cap` ∈ {320, 512} MB
- `workload` ∈ {uniform, skewed}
- ⇒ 5 × 6 × 2 × 2 = **120 configs**

### Sweep B — Compression (RQ2, RQ3, **H3**)
- `compression` ∈ {none, lz4, zstd}; `state` ∈ {400k…2M} (5); cap=512, P=8
- ⇒ 3 × 5 = **15 configs**

### Sweep C — Write buffer (RQ2, RQ3, **H1**, H4)
- `wbuf` ∈ {16, 32, 64, 128} MB; `state` ∈ {400k…2M} (5); cache=32, lz4, P=8, cap=512
- ⇒ 4 × 5 = **20 configs**

### Sweep D — Block cache (RQ2; microbench "cache ≈ irrelevant for writes")
- `cache` ∈ {0, 32, 64} MB; `state` ∈ {400k…2M} (5); wbuf=64, lz4, P=8, cap=512
- ⇒ 3 × 5 = **15 configs**

### Sweep E — Stall / backpressure (**H2**)
- No new cells — derived from A + C using the new write-stall metric (stall_µs / elapsed). (0 extra)

### Sweep F — Watermark validation (**H4**)
- `(soft,hard)` ∈ {tight (32,48), proposed-70/90 (derived), loose (96,128)}; `state` ∈ {1.6M, 2M, 5M}; P=8, cap=512
- ⇒ 3 × 3 = **9 configs**

### Reference set — in-memory baselines (RQ1 §3.3.1, RQ2 overhead denominator)
- in-mem (spill off) capped: `state` ∈ {400k…2M, 5M} (6) × `cap` {320,512} = 12
- in-mem uncapped (throughput reference): `state` (6) = 6
- ⇒ **18 configs**

**Total distinct configurations = 120 + 15 + 20 + 15 + 9 + 18 = 197 configs.**

---

## 4. Repetitions — keeping variance small

Observed throughput **CV ≤ 0.079**; worst expected is `none`-compression (microbench flagged high variance there).
95 % CI half-width of the mean ≈ 1.96 · CV / √R:

| R (reps) | CI half-width @ CV=0.08 | CI half-width @ CV=0.15 (none) |
|---|---|---|
| 3 | ±9.0 % | ±17.0 % |
| 5 | ±7.0 % | ±13.1 % |
| **10** | **±5.0 %** | ±9.3 % |
| 20 | ±3.5 % | ±6.6 % |
| 50 | ±2.2 % | ±4.2 % |

**Recommendation:**
- **R = 10** default for all configs → ±5 % CI on the well-behaved cells.
- **R = 20** for the `none`-compression and tight-cap cells (the historically noisy ones).
- (Optional **R = 50** "microbench-grade" if you want to spend the full two weeks.)

Variance is also reported per cell (CV) so any cell exceeding a CV threshold (e.g. 0.15) is auto-flagged for more reps.

---

## 5. Run counts & wall-clock (per-run ≈ 2 min; OOM cells faster)

| Plan | Reps | Total runs | Serial | 4-wide | 8-wide |
|---|---|---|---|---|---|
| Default | 10 (20 for ~30 noisy) | ≈ **2,200** | ~73 h | ~18 h | ~9 h |
| Dense | 20 | ≈ **3,940** | ~131 h | ~33 h | ~16 h |
| Microbench-grade | 50 | ≈ **9,850** | ~328 h | ~82 h | ~41 h |

All three fit your 1–2 week budget; **Default (R=10) is the recommendation** — it already gives ±5 % CIs.
"~50k runs" is only reached by pushing R≈250, which buys essentially nothing statistically (CI ∝ 1/√R).

---

## 6. Outputs / deliverables of the run
- `e3big/results/*.tsv` (one file per cell, resumable) → `collect.sh` → `runs.tsv`.
- `analyze_ooc.py` (extended) → figures per RQ + per-hypothesis, incl. the **P-knee** (residency vs overhead),
  compression (H3), wbuf/throughput (H1), stall% (H2), spill-latency-per-100MB (H5), survival with in-mem OOM.
- A **per-hypothesis verdict table** (H1–H5: confirmed / refuted / reframed) computed from the data.
- The HTML/PDF summary gains a **Hypotheses page** + updated figures.

## 7. Prerequisites before launch
1. **H2 stall-metric code** (in progress: TDD + cpp-review + commit) — needed for Sweep E.
2. Harness finish: `gen-matrix.sh` (emits jobs), `launch.sh <parallelism>` (xargs -P, resumable), `collect.sh`.
3. Skewed + 5M workloads generated (`NORMAL_DISTRIBUTION UINT64 t p` for keys; SEQUENCE 0..5M for big).
4. Local validation of ~30 cells (parallel + resume + all metrics parse) before handing off the full matrix.
