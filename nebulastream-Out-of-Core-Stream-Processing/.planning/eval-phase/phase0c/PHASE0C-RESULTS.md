# Phase 0c — Clean peak-RSS measurement harness

**Goal:** measure the windowed-aggregation query's peak RSS under a memory cap, *without* the systest confounders
that made E0 unmeasurable, so Phase 1/2 improvements can be quantified against a clean baseline.

## What changed vs. E0 (why this is now measurable)
A fresh, thorough code sweep (Phase 0c exploration) established two things that overturn the E0 premise that a
bespoke C++ driver was required:

1. **No NetworkSink on the single-node path.** `QueryDecomposition` only injects a NetworkSink when the plan is cut
   across two distinct worker nodes (`nes-query-optimizer/src/Placement/QueryDecomposition.cpp:122`). The systest
   embedded worker runs the whole plan on one node ⇒ no NetworkSink burst. That confounder never existed for
   single-node runs.
2. **The 2M-row result-check is skippable with a Void sink.** `checkResult` returns `nullopt` *before* any
   `loadQueryResult` when the sink type is `VOID` (`nes-systests/systest/src/SystestResultCheck.cpp:769-785`). E0's
   137s in runs G/Ghi/Bns512/Bns320 were `loadQueryResult` diffing 2M rows in RAM — a Void sink removes that load
   entirely while still delivering every triggered window to the (discarding) sink, so the **terminal-flush burst we
   actually want to measure is unchanged**.

⇒ Phase 0c is a **light shell + Void-`.test` harness around the existing `systest` binary**, not a new linked C++
executable. Artifacts: `SpillSmokeVoid.test` (Void-sink clone of the E0-REDO breadth workload) + `run-rss.sh`
(memory-capped Docker run, reads cgroup-v2 `memory.peak`, captures the Phase-0b SpillMetrics log line + RocksDB
spill-dir size + exit code) + `results.tsv` (auto-appended).

## Harness contract
- Workload is FIXED (2,000,001 tuples, 100k distinct keys/window, 20 tumbling windows of 100000 ms) so peaks are
  directly comparable to E0-REDO. Only SYSTEM knobs vary.
- `run-rss.sh <label> <cap_mb> <spill on|off> <pool_buffers> <soft_mb> <hard_mb> <emit_lag_ms> <threads> <wbuf_mb> <cache_mb>`
- Peak RSS source: container cgroup `/sys/fs/cgroup/memory.peak` (cgroup v2), read inside the container right after
  `systest` exits. Exit codes: `0` ok · `137` cgroup OOM-kill · `141` buffer-pool starvation.

## Validation run (harness proof) — ✅ PASSED 2026-05-23
The workload E0 could not get a clean number for (run Ghi died 137 at 768 MB *inside* `loadQueryResult`) now
**completes** and yields a clean peak. Branch `feat/ooc-phase1-spill-skeleton`, build `cmake-build-debug` (Debug),
image `nebulastream/nes-development:local-rocksdb`.

| label | cap (MB) | spill | pool | soft/hard | emit_lag (ms) | threads | rc | proc peak (MB) | store peak (MB) | spilled | spill_dir (MB) | runtime |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V_on1024 | 1024 | on | 16000 | 4/8 | 200000 | 1 | **0** | **417** | **19** | 19 | 43 | 116 s |

Phase-0b `logMetrics()` (SpillableTimeBasedSliceStore.cpp:909, NES_INFO) for this run:
> spilled 19 slices / 77,824,000 B, restored 19 slices / 77,824,000 B, peak resident 20,486,144 B;
> spill latency µs p50=40510 p95=95946 p99=95946 max=95946 (n=19);
> restore latency µs p50=38326 p95=41350 p99=41350 max=41350 (n=19)

**Proves:** (1) the result-check confounder is gone — `rc=0` where E0 was `137`; (2) the harness measures a clean peak;
(3) spilling engages (19 slices ≈ 74 MB logical → 43 MB on disk after lz4); (4) Phase-0b instrumentation is correct in
a real query — `bytesRestored == bytesSpilled`, latencies + peak all populated.

### 🔑 The headline gap Phase 0c exists to expose
**Process peak RSS = 417 MB, but the store's internal resident high-water = only ~19.5 MB.** Spilling keeps the
*slice store* tiny, yet the process still peaks at 417 MB. The ~400 MB delta is the buffer pool (16000 × 4 KB = 64 MB)
+ RocksDB memtable/cache + engine overhead + **the terminal-flush re-materialisation** (all 19 retained windows get
unspilled-on-trigger back into pool buffers to be emitted — visible as the 19 "Unspilling slice … on trigger read"
lines). This is precisely the E0 conclusion made *measurable*: config/spill shrinks resident store state but NOT the
process peak, because emission must re-materialise the band. **That delta is the Phase 1/2 target** (paced/partitioned
emit so peak ≈ one window/partition, not the whole band).

## Focused baseline results (2026-05-23) — the Phase-1 BEFORE

Three modes (see `run-rss.sh`): **on** = retain band (`emit_lag`) + spill to RocksDB; **inmem** = retain band but
never spill (the fair no-OOC baseline OOC must beat — `enabled=true`, thresholds ≈∞); **off** = `enabled=false` ⇒
`DefaultTimeBasedSliceStore`, which IGNORES `emit_lag` ⇒ no band retained = a plain continuous-emit query (the engine
floor reference, NOT an OOC baseline). Pinned: pool=16000 (64 MB), 1 thread, RocksDB defaults (wbuf/cache 0). Workload
fixed at 2,000,001 tuples / 20 tumbling windows of 100k keys.

### Full 20-window backlog (`emit_lag=2000000` ⇒ nothing emits until terminate; maximal terminal flush)
| cap (MB) | off (no retain) | inmem (retain, no spill) | on (retain + spill) |
|---|---|---|---|
| 1024 | — | 407 ✓ | **505 ✓** |
| 512 | 306 ✓ | 397 ✓ | **496 ✓** |
| 384 | 307 ✓ | 384 ✓ (at cap) | 384 ✓ (at cap) |
| **320** | **307 ✓** | **320 ✓** | **137 OOM** |
| 256 | — | **137 OOM** | **137 OOM** |

Store internal peak resident (`peakResidentBytes`): **inmem = on = 78 MB** for the full band; **23 MB** for the
2-window band (`W2_inmem512`). i.e. the on-mode store re-materializes the *entire* band at the terminal flush, exactly
matching what inmem holds resident the whole time.

### 2-window band control (`emit_lag=200000`)
| cap | inmem | on |
|---|---|---|
| 512 | 321 ✓ (store 23) | 419 ✓ (store 19) |

### What the baseline establishes (the Phase-1 BEFORE)
1. **The current un-paced spill is strictly *worse* than not spilling.** At every cap the `on` process peak exceeds
   `inmem` by ~100 MB (505 vs 407 @1024; 496 vs 397 @512), and **at 320 MB `inmem` survives while `on` OOMs**. The
   terminal flush re-materializes the whole band into the pool *on top of* RocksDB's resident working memory, so OOC
   today *adds* peak instead of removing it.
2. **`on` store peak scales with band size** (19 MB @2-win → 78 MB @20-win) = whole-band re-materialization. This is
   the single metric Phase 1 must flatten: after paced emit, `on` store peak should be ≈ ONE window, independent of
   band size.
3. **Death-cap ordering:** `off` (floor ~306) < `inmem` (dies 256–320, lives 320) < `on` (dies 320–384, lives 384).
   The clean demonstration window is **cap ∈ [320, 384) MB**: a non-retaining query lives, in-mem lives, but the
   current spill dies.
4. **Engine floor caveat (matters for Phase 2 design):** the no-retention floor (~306 MB, dominated by engine +
   ingestion + 64 MB pool) is large vs this band (78 MB). To show OOC beating *in-mem* (survive where the band itself
   exceeds RAM), a later eval needs a BIGGER band (more keys/windows) so band ≫ floor. The *mechanism* (band-size-
   independent peak) is demonstrable at this size; the dramatic "OOC survives where in-mem OOMs" headline needs the
   bigger workload (E0 noted in-mem OOMs ≈6M keys/512 MB).

### 🎯 Phase-1 target cell (success criterion)
**cap = 320 MB, full backlog (`emit_lag=2000000`), pool=16000, spill on.**
- BEFORE (un-paced terminal flush): `rc=137` (OOM), peak pinned at cap.
- AFTER (Phase 1 paced emit, one window unspilled→emitted→freed at a time): expect `rc=0` with process peak ≈ floor +
  one window (~310–315 MB) — i.e. spill should turn the 320 MB OOM into a completion, and ideally land *below* inmem's
  397 MB. Re-run `./run-rss.sh F20_on320 320 on 16000 4 8 2000000 1 0 0` to measure the after.

Raw data: `results.tsv`; per-run logs: `logs/<label>.log`; NES detail: `cmake-build-debug/nes-systests/SystemTest_*.log`.

## Phase 1 (L1, Option 1 "bound the unspill") AFTER results — committed `05c8520dbb`
Phase 1 makes the terminal flush unspill ONE window at a time (lazy getAllNonTriggeredSlices + per-window
ensureWindowSlicesResident hook in triggerAllWindows). Re-ran the full-backlog "on" cells with the rebuilt binary:

| label | cap | threads | rc | proc peak | store peak | vs BEFORE |
|---|---|---|---|---|---|---|
| F20_on320_p1 | 320 | 1 | **137** | 320 | NA(killed) | unchanged (was 137) |
| F20_on512_p1 | 512 | 1 | 0 | 500 | **78** | unchanged (was 496 / 78) |
| F20_on1024_p1 | 1024 | 1 | 0 | 495 | **78** | unchanged (was 505 / 78) |
| F20_on320_p1_t4 | 320 | 4 | **137** | 320 | NA | still OOM with parallelism |
| F20_on256_p1_t4 | 256 | 4 | **137** | 256 | NA | still OOM |

### 🔴 Conclusion: Option 1 alone does NOT reduce the peak — escalate to Option 2.
`store_peak` is **still 78 MB (the whole band)** in every completing run — identical to pre-Phase-1. Phase 1 changes the
ORDER of unspilling (one window at a time) but not the END STATE: at terminate every window is emitted via the ASYNC
`emitBuffer` and stays resident (emittedKeys-protected, freed only after the probe drains it + the output watermark
advances). The synchronous terminate loop emits all 20 windows before the probe frees any — even at threads=4 — so the
whole band is resident at the peak regardless. The death cap [320,384) is unmoved; spill-on still OOMs where in-mem
survives (320).

Phase 1 is **neutral on peak** (±5 MB, no regression) and is the **correct, necessary substrate** for the fix: you
cannot bound the in-flight emitted set without first unspilling per-window. The binding constraint is the **in-flight
emitted set** (Option 2 / backpressure: free each window before unspilling the next), or the Phase-2 partitioned
streaming emit (peak ≈ one partition). This is the agreed escalation point.

Caveat preserved: a MODERATE emit_lag (retain a few windows, emit continuously) never stresses the terminal flush and
already works today at low peak (see V_on1024, 2-window band, store peak 19 MB). The pathological full-backlog
(emit_lag=2000000, nothing emits until EOS) is the case OOC must win and currently cannot by Option 1.

## SpillMetrics (Phase 0b) capture — note
`logMetrics()` logs at NES_INFO into the per-run `cmake-build-debug/nes-systests/SystemTest_*.log`, **not** stdout.
`run-rss.sh` greps the newest such file after each run and surfaces `peak resident` (→ `store_peak_mb`) and
`spilled N slices` (→ `slices_spilled`) into `results.tsv`. The internal `peakResidentBytes` (store resident
high-water) complements the external cgroup `memory.peak` (whole-process RSS); their gap is the buffer-pool + RocksDB
+ engine + terminal-flush overhead quantified above.
