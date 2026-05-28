# E0-REDO-RESULTS — OOC spill smoke with the emit-decouple (Increment C)

Branch `feat/ooc-phase1-spill-skeleton` @ `1f30a75822` (Increment C head). Build/run only in Docker
`nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug` (Debug). Date 2026-05-23.
Re-run of E0 after the emit-decouple landed, to prove a REAL tumbling query now spills (the original E0's
structural blocker), and to characterise survival/completion under a cgroup memory cap.
Workload = `nes-systests/operator/aggregation/SpillSmoke.test` (BREADTH case: 2,000,001 tuples, key & ts both
`SEQUENCE 0..2,000,000 step 1`, val `SEQUENCE 0..100`; `WINDOW TUMBLING(ts, 100000 ms)` ⇒ 20 completed windows
of 100,000 distinct keys each; `emit_lag` retains a band of completed-but-unemitted windows ⇒ spillable).
Run harness: `/tmp/e0run.sh <label> <pool> <soft> <hard> <emit_lag> <threads> [cap]` (auto-discriminates death point).

## TL;DR
- **✅ The E0 blocker is FIXED. A real query spills.** `emit_lag>0` → the background spiller evicts COMPLETED
  (cold-but-unemitted) windows: **19 windows spilled, 45.6 MB to RocksDB** (original E0: 0 bytes).
- **✅ Completes with CORRECT results.** Full output verified on disk: **2,000,001 rows, all 20 windows**
  (end = 100000…2000000), 100,000 distinct keys each, correct per-key `SUM(val)`.
- **✅ Crash-safe.** Across ALL runs: **0 late-write-guard firings, 0 `INVARIANT`, 0 terminate.** The in-order
  Generator never delivers a late record into a retained band slice ⇒ the guard is dead-but-harmless for
  in-order/watermark-respecting sources (an out-of-order source is still needed to exercise it — see Open items).
- **✅ Spilling relieves STEADY-STATE resident.** With spill ON the store holds ~30–56 MB resident (`hard=8`);
  the spilling query **survives full ingestion down to a 256 MB cap**. No-spill must hold the whole band resident.
- **🔑 The binding bottleneck is the TERMINAL FLUSH, not steady state — and it is NOT relieved by spill config.**
  The query emits continuously during ingestion (fine, spill or not); at EOF the retained band must re-materialise
  into global-buffer-pool buffers to emit. That burst is the consistent failure point: **OOM (137)** with a large
  pool, **buffer-starvation (141)** with a small pool, **harness-result-check OOM (137)** if it gets past the flush.
  Spill and no-spill fail **identically** at tight caps because the band must materialise regardless of where it
  was stored. A clean "spill completes where no-spill OOMs" headline is therefore **NOT achievable by config
  tuning** — it needs code/harness work (below), which is the actionable E1/E2 outcome.
- **📏 Per-key REAL footprint revised to ~150–170 B/key** (was estimated ~300). Total workload state ≈ 340 MB,
  which FITS in a 512 MB cap → at ≥320 MB even no-spill completes the query; the workload is not large enough to
  force a clean no-spill ingestion OOM at generous caps.

## Run matrix (cap via `--memory=<cap> --memory-swap=<cap> --cpus=1`; pool = `number_of_buffers_in_global_buffer_manager`, 4 KB/buffer)
| Run | cap | spill | emit_lag (ms) | pool | hard/soft (MB) | outer exit | died where | output | reading |
|---|---|---|---|---|---|---|---|---|---|
| G | 512 | on | 200,000 (2-win band) | 16000 | 8/4 | 137 | **post-query harness result-check** | **full 2M, 20 win** | QUERY COMPLETES; 137 = `loadQueryResult` diffing 2M rows |
| Ghi | 768 | on | 200,000 | 16000 | 8/4 | 137 | post-query harness result-check | full 2M | even 768 MB can't fit the 2M-row harness diff |
| H | 512 | on | 1,000,000 (10-win band) | 16000 | 8/4 | 137 | **terminal flush** (filesink not closed) | full 2M written | big-band unspill burst (~150 ms, 10 win) spikes over cap |
| Bns512 | 512 | OFF (thr 999999) | 1,000,000 | 16000 | — | 137 | post-query harness result-check | full 2M | band FITS resident at 512 → completes; only harness OOMs |
| Bns400 | 400 | OFF | 1,000,000 | 16000 | — | 137 | terminal teardown (after backpressure drained) | full 2M written | band still fits ingestion; dies at very end |
| Bns320 | 320 | OFF | 1,000,000 | 8000 | — | 137 | post-query harness result-check | full 2M | band (~170 MB) still fits 320 → query completes |
| Bns256 | 256 | OFF | 1,000,000 | 4000 | — | **141** | terminal flush | **partial 900k (9 win)** | small pool + resident band → pool exhausted at flush |
| H256 | 256 | on | 1,000,000 | 4000 | 8/4 | **141** | terminal flush (survived ingestion) | **partial 900k (9 win)** | **identical to Bns256** — flush must re-materialise band into the same exhausted pool |

## The mechanism (what every run agrees on)
1. **Resident window state draws from the global buffer pool** (4 KB buffers). Resident working set ≈ pooled buffers
   held by live hashmaps. So pool size, not just `--memory`, bounds how much state can be resident.
2. **Steady-state ingestion is fine** with continuous emit: only the `emit_lag` band is retained, and it flows
   (oldest window emitted as the watermark advances). Spill keeps this band on disk (low resident); no-spill keeps
   it in pool buffers. Both survive ingestion at every cap ≥ ~256 MB; at 256 MB the no-spill band exhausts a small pool.
3. **The terminal flush is the killer.** At EOF the retained band re-materialises into pool buffers in a tight burst
   to emit (observed: 10 windows unspilled in ~150 ms in run H). Peak demand ≈ band size, regardless of spill:
   - large pool ⇒ burst blows the cgroup cap ⇒ **137** (H).
   - small pool ⇒ burst exhausts the pool ⇒ **141** (Bns256, H256 — identical at 900k rows).
   - bounded band that fits ⇒ **flush succeeds, query completes** (G: emit_lag=200000).
4. **Async spiller can't bound memory under flood + 1 CPU.** `emit_rate` is unthrottled and `--cpus=1`; the background
   spiller competes for the one core and cannot drain fast enough to make `hard_threshold` a hard RSS bound during a
   burst. The threshold is a soft signal, not a ceiling.

## Confounders (systest harness, NOT the system under test)
- **Result correctness check loads the whole result** (`SystestResultCheck::loadQueryResult`) into the SAME capped
  process → diffing 2,000,001 rows needs >768 MB → 137 *after* the query already wrote correct output (G, Ghi,
  Bns320, Bns512). `-b` benchmark mode still calls `checkResult` → no help.
- **Distributed topology**: systest runs source-node → `NetworkSink` → sink-node → file. The terminal burst
  backpressures the network sink (1000 buffered) and adds a teardown hop. Not representative of a single-node OOC deploy.

## Verdict
The core de-risk is **positive and conclusive**: the emit-decouple makes a real tumbling query spill (45.6 MB,
19 completed windows), complete with correct results (2M rows, 20 windows), and run crash-safe; spilling demonstrably
relieves **steady-state** resident memory (spill survives full ingestion to 256 MB). The §3.3 "survives the cap AND
finishes" headline is **gated by the terminal flush**, which re-materialises the retained band into the buffer pool
and fails identically with or without spill at tight caps — i.e. it is an **architectural/harness limitation, not a
spill-config calibration gap, and not a flaw in the emit-decouple itself.** Config tuning was exhausted (9 runs);
the remaining work is code/harness (below).

## What E1/E2 must do (now precisely scoped by E0)
1. **Incremental, backpressured terminal flush (highest leverage).** Emit + free the retained band one slice at a
   time with flow control, so terminal peak ≈ a few slices instead of the whole band. This is the single change that
   unlocks "complete under a cap below the band size." Currently `getTriggerableWindowSlices` at quiescence unspills
   the whole band in a burst.
2. **Wire RocksDB `write_buffer_size` + `block_cache` through SpillConfig → RocksDBSpillBackend.** Removes the
   ~64–80 MB default memtable/cache tax that dominates at tight caps (microbench suggests wbuf 32 MB / cache 32 MB).
3. **Standalone, single-node eval driver + RSS instrumentation.** Direct file sink (no NetworkSink hop), skip or
   stream the result-correctness check under the cap (verify correctness out-of-band on disk), and **measure peak
   RSS** (cgroup `memory.peak`) rather than relying on the OOM-kill signal — this is the E1 instrumentation surface.
4. **Out-of-order source for the guard.** To actually exercise the Increment-C late-write pin, E2 needs a source that
   emits records behind the watermark into a retained band window. The in-order Generator cannot; the guard remains
   verified only by unit test T8 until then.

## What works for a clean run TODAY
**Config G** — `emit_lag=200000` (bounded 2-window band), `pool=16000`, `hard=8`/`soft=4`, `compression=lz4` at a
512 MB cap: the spilling query completes with full correct output (2M rows, 20 windows). The only non-zero exit is
the post-query systest result-diff (137), which is a harness artifact, not the query.

## Artifacts
- Workload: `nes-systests/operator/aggregation/SpillSmoke.test` (preserved at `.planning/eval-phase/SpillSmoke.e0-redo.test`).
- Run harness: `/tmp/e0run.sh` (label, pool, soft, hard, emit_lag, threads, cap → auto death-point discrimination).
- Per-run output: `/tmp/e0_<label>.out`; per-run debug log: `cmake-build-debug/nes-systests/SystemTest_*.log`
  (greppable: `Spilled cold slice`, `unspillAndMarkEmitted`, `buffer allocation failure`, `loadQueryResult`,
  `Closing file sink`, `on late write into the retained band`, `INVARIANT`).
- Verified output sample: `cmake-build-debug/systest-run/results/SpillSmoke_1.csv` (2,000,001 rows when query completes).
