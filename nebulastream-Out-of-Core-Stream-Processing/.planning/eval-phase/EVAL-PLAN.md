# EVAL-PLAN — OOC spill evaluation (MS4 → MS5)

Grounded in `EXPLORE-INFRA.md` (systest + metrics infra) and `EXPLORE-PROPOSAL-MICROBENCH.md`
(proposal RQs/MS/§3.3 + microbench reuse). Branch `feat/ooc-phase1-spill-skeleton` @ HEAD
`5e4fd3cd9f` (Increment B complete, 820/820). Build/run ONLY in Docker
`nebulastream/nes-development:local-rocksdb`. This plan serves **MS4** (wire metrics → design experiment
→ collect data) and sets up **MS5** (analysis). User decisions (2026-05-23): **full staged metrics**;
matrix = **core (state-size × OOC-vs-baseline) + spill-threshold sensitivity + storage-efficiency capture**
(no compression re-confirm — lz4 taken as given from the microbench).

---

## Goal & research questions (verbatim, proposal §2.2)
- **RQ1 (integration):** integrate an OOC backend into NES's push-based model. *(Largely answered by the
  Increment A+B implementation; the eval DEMONSTRATES it via a memory-limited spilling query that completes
  where the in-memory store OOMs.)*
- **RQ2 (performance):** throughput + tail latency (p95/p99) of a research-informed RocksDB config for
  tumbling OOC; latency + CPU-time cost of spilling/restoring when state > memory.
- **RQ3 (efficiency/scaling):** how CPU + disk I/O evolve as state grows past memory; RocksDB storage
  efficiency (write amplification + storage footprint).

## Success criteria (proposal §3.3 — the binary gate + the comparison)
- **§3.3 gate:** process tumbling-window state **> physical memory without OOM**.
- **§3.3.1 effectiveness:** OOC survives a state size that OOM-kills the in-memory baseline.
- **§3.3.2 efficiency:** overhead = OOC (CPU **and** memory capped) throughput/tail-latency **vs the in-memory
  baseline run under identical CPU limits but NO memory cap**. No numeric target — qualitative (no OOM) +
  comparative (quantify overhead). Also capture CPU, disk I/O, write amp, footprint for the RocksDB path.

---

## Phased execution (staged — de-risk before building instrumentation)

### Phase E0 — Smoke / de-risk (ZERO new code) ⟵ do this FIRST
Prove the whole pipeline end-to-end with crude metrics before investing in instrumentation.
1. Author `nes-systests/operator/aggregation/SpillSmoke.test` — a tumbling `GROUP BY` aggregation over a
   `Generator` source whose key range × window span produces resident state that crosses the spill thresholds
   (skeleton in `EXPLORE-INFRA.md` §"Smallest path", lines 300-318).
2. Run spill **ON** under `docker run --memory=512m --memory-swap=512m --cpus=1` (engine buffer pool +
   `soft/hard_threshold_mb` set BELOW the ceiling). Confirm it spills (grep `systest.log -d` for
   `Spilling slice`/`Hard threshold crossed`) and completes (exit ≠ 137).
3. Run spill **OFF** (in-memory `DefaultTimeBasedSliceStore`) at the SAME ceiling + state size → expect
   OOM-kill (exit 137). **This pair is the §3.3 success demonstration in miniature.**
4. **Calibrate state-size knobs:** record resident MB vs (key-range, window-span, window-size) so E2 can pick
   state-size points that straddle the thresholds and the ceiling.
**Exit criteria:** one spilling query that survives a ceiling the in-memory store dies under; a calibration
table mapping generator params → resident state MB. **Deliverable:** `E0-SMOKE-RESULTS.md`.

### Phase E1 — Instrumentation (CODE; run via `/dev-eng-orchestra-cpp`)
The proposal's required metrics are GAPS today (`EXPLORE-INFRA.md` §3). Build the minimum that makes RQ2/RQ3
measurable, in the established TDD+review pipeline. **PRs (stacked, in the NES repo):**
- **E1-PR1 — spill/restore instrumentation (RQ3, RQ2-cost).** Add `chrono` timers + monotonic counters to the
  spill path: `spillCount`, `unspillCount`, `bytesSpilled`, `bytesRestored`, cumulative `spillNs`/`unspillNs`
  (in `SpillableTimeBasedSliceStore` around `spill()`/`unspill()`, and/or in `RocksDBSpillBackend::put/get`).
  Emit them **production-visibly** (survive Release): a structured `NES_INFO` one-liner at query stop AND/OR a
  production accessor the embedded systest backend reads — CSV-parseable. (Promote the existing test-only
  `spillCountForTest` to a real metric.)
- **E1-PR2 — research-informed RocksDB config surface (RQ2).** Extend `SpillConfig` +
  `RocksDBSpillBackend` to set `write_buffer_size` and `block_cache` size (today only `compression` is wired —
  `SpillConfig.hpp:41-54`). Without this, "research-informed config" = lz4 only; with it we apply the full
  microbench recommendation (lz4 / wbuf 32 MB / cache 32 MB). Also expose RocksDB internal statistics
  (`rocksdb::Statistics` / `GetProperty`) for write-amp + footprint so `parse_output.py`-style parsing works.
- **E1-PR3 — tail latency p95/p99 (RQ2).** Implement a `QueryEngineStatisticListener` that records per-task
  (start→complete) durations from the existing event stream (`QueryEngineStatisticListener.hpp:154-161`),
  collects them, and computes p50/p95/p99 at query stop → CSV. Decide + document the latency unit (per-task is
  the cheap, available signal; per-window would need an extra hook — default per-task, note the caveat).
- **E1-PR4 (optional) — Generator throughput.** Either compute throughput externally as
  (known generated tuple count / measured elapsed) in the harness, OR add a processed-tuple counter the
  benchmark JSON can read. Prefer the external calc (no code) unless E0 shows it's unreliable.
**Each PR:** Docker build + ctest gate, cpp-review, regression stays ≥ 820. **Deliverable:** instrumented
binary emitting a CSV row per run with the proposal's metrics.

### Phase E2 — Experiment harness (scripting; new sibling `Bachelor-Thesis/eval-ooc/`)
Reuse the microbench scaffolding (don't rewrite). Build:
- A run-matrix driver adapting `Microbenchmark-BA/scripts/run_benchmarks.py` scaffolding (run-id gen, rep loop,
  `status` capture incl. `oom` via exit 137, CSV append) — but invoking `systest` under
  `docker run --memory --memory-swap --cpus=1`, not `db_bench`.
- The `.test` workloads (E0's query, parameterized by generator state-size knobs + the threshold/variant axes).
- CSV schema = `config.py:CONSTRAINED_CSV_COLUMNS` **extended** with `spill_latency`, `restore_latency`,
  `bytes_spilled`, `bytes_restored`, `state_size_mb`, `cpu_util`, `variant` (ooc|inmem), `memory_capped` (Y/N).
**Deliverable:** `eval-ooc/scripts/` + a documented `run-matrix` command.

### Phase E3 — Run & collect (this IS MS4's "Collected Data")
Execute the matrix (below) → one CSV (`results/ooc-integrated/runs.csv`). Capture per-run: elapsed, throughput,
p50/p95/p99, spill/restore latency + counts + bytes, RocksDB write-amp + footprint, CPU%, disk I/O, status.
**Deliverable:** the raw dataset + a `print_variance_report()`-style data-quality check (CV per cell).

### Phase E4 — Analyze (MS5) & write up
Adapt `analyze.py:generate_report_figures()` + `analyze_comparison()` + the `.tex` template to produce the
RQ1/2/3 figures and the results chapter. **Deliverable:** figures + results section answering all 3 RQs +
the §3.3 OOM-survival figure.

---

## Experiment matrix (proposal: NO config search — fix RocksDB, vary state + a thin threshold axis)

**Fixed RocksDB config (the single "research-informed config", microbench-recommended):**
`compression=lz4`, `write_buffer_size=32 MB`, `block_cache=32 MB` (≈ 64 MB RocksDB at the 512 MB ceiling).
**Fixed environment:** `--memory=512m --memory-swap=512m --cpus=1`; engine buffer pool + spill thresholds set
below the ceiling; `execution_mode=COMPILER`; fixed worker-thread count (record it).

**Axis A — core: state size × variant** (answers §3.3, RQ2 overhead, RQ3 scaling)
- `state_size_mb` ∈ ~{64, 128, 256, 384, 512, 768, 1024} (≈6–7 pts; final values from E0 calibration; must
  span below the spill threshold → above the 512 MB ceiling).
- `variant` ∈ {**OOC** (spill on, memory-capped), **in-mem baseline** (spill off, **no** memory cap, same CPU)}.
  Baseline runs only where it fits; its OOM point vs OOC-survives = the §3.3.1 effectiveness result.
- `workload` ∈ {**W1 uniform keys**, **W2 skewed keys** (Generator `NORMAL_DISTRIBUTION`)} — proposal's
  "skewed … smart-city sensor streams" (§3.1). Bursty timestamps = best-effort/optional (Generator timestamps
  are sequential; note as a limitation).
- reps = 5 (verify sufficiency via CV; bump only noisy cells).

**Axis B — spill-threshold sensitivity** (chosen add-on; characterizes the eviction policy, RQ2)
- At ONE large state size (≥ ceiling), `(soft,hard)` ∈ {tight ~(64,96), medium ~(128,192), loose ~(192,256)} MB,
  × both workloads × 5 reps. Measures how aggressive vs lazy spilling trades throughput vs tail latency vs
  spill volume.

**Storage-efficiency capture** (chosen add-on; RQ3): write amplification, on-disk footprint, CPU%, disk I/O
recorded on EVERY OOC run (no extra runs — extra columns, via E1-PR2's RocksDB statistics + OS counters).

Rough run count: Axis A ≈ 7×2×2×5 = 140 (minus baseline-OOM cells) + Axis B ≈ 3×2×5 = 30 ⇒ **~150 runs**
(vs the microbench's 10k — deliberately small per "no exhaustive search").

---

## Metrics → RQ mapping
| Metric | Source (post-E1) | RQ | Success tie-in |
|---|---|---|---|
| OOM survival (exit ≠137) | harness exit code | RQ1 | §3.3 gate |
| Baseline OOMs where OOC survives | Axis A baseline vs OOC | RQ1 | §3.3.1 |
| Throughput (tuples/s) | elapsed + known generated count | RQ2 | §3.3.2 overhead |
| Tail latency p95/p99 | E1-PR3 listener | RQ2 | §3.3.2 overhead |
| Spill / restore latency + counts + bytes | E1-PR1 timers/counters | RQ2 (cost), RQ3 | — |
| CPU utilization | OS counters (`/proc`, `docker stats`) | RQ3 | §3.3.2 |
| Disk I/O | OS counters / RocksDB stats | RQ3 | §3.3.2 |
| Write amplification | E1-PR2 RocksDB statistics (`parse_write_amp` formula) | RQ3 | §3.3.2 |
| Storage footprint | RocksDB dir size on disk | RQ3 | §3.3.2 |

## Microbench reuse map (don't rewrite — see EXPLORE-PROPOSAL-MICROBENCH §5)
`analyze.py:generate_report_figures()` (fig pipeline incl. P99 fig9) · `analyze_comparison()` (baseline-vs-OOC,
swap groupby→state_size) · `_prep_ok()` (NaN→0 correction — REQUIRED, the documented stall-NaN bug) ·
`_collapse_reps()` + `print_variance_report()` (CI + CV data-quality) · `parse_output.py` (write-amp,
percentile, cache-hit parsers — port the formulas if NES stats string differs) · `config.py:CONSTRAINED_CSV_COLUMNS`
(schema, extend as above) · `report_preliminary.tex` (report template) · `plot_oom_matrix` (the §3.3 survival fig).
Memory mechanism: `run_benchmarks.py:build_docker_cmd()` `--memory/--memory-swap/--cpus=1` — reuse verbatim.

## Risks & notes
- **Instrumentation is real production code** (E1) on the freshly-landed concurrency store — keep regression ≥820,
  cpp-review each PR; spill/restore timers must not perturb the eviction-mutex hold time (time OUTSIDE the lock,
  consistent with the no-I/O-under-lock invariant).
- **Per-task vs per-window latency:** the engine only exposes per-task events; true per-window tail latency would
  need an extra hook. Default to per-task p95/p99, document the unit honestly (ties to the no-TSan-style honesty
  the thesis already commits to).
- **Generator throughput NaN** → external calc; validate the generated-tuple count in E0.
- **State-size calibration** is empirical (E0) — resident MB depends on key cardinality × concurrent slices ×
  entry size; budget time for a calibration pass.
- **Bursty input** is hard with the sequential-timestamp Generator — likely a documented limitation, not a blocker
  (skew via `NORMAL_DISTRIBUTION` keys is achievable and covers the headline "skewed" case).
- **No numeric target** in the proposal — frame results as overhead-vs-baseline + the OOM-survival gate, anchored
  to the microbench's quantitative findings (lz4 4.4×, etc.).

## Timeline fit (SC2 ~28.05–10.06; draft was due 22.05)
E0 (hours) → E1 instrumentation (the bulk; ~days, orchestra) → E2 harness (~1 day, mostly reuse) → E3 runs
(~150 runs, hours-to-1-day) → E4 analysis (reuse, ~1–2 days). Sequenced so a partial result (E0 + Axis A core)
already answers the §3.3 gate + RQ1 if time runs short; threshold sensitivity + full storage-efficiency are the
first things to trim under deadline pressure.
