# E0-SMOKE-RESULTS — OOC spill smoke / de-risk (empirical)

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Build/run only in Docker
`nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug` (Debug build — DEBUG
logs present). Date 2026-05-23. ZERO production code changed; only authored
`nes-systests/operator/aggregation/SpillSmoke.test` + ran Docker.

## TL;DR (headline)
- **§3.3.1 in-mem-OOM half: CONFIRMED.** Large single-window aggregation state (~6M keys ≈ 240 MB)
  OOM-kills the in-memory store at a `--memory=512m` ceiling (exit **137**).
- **§3.3 spill-survives half: NOT demonstrated — structural blocker found (a valid, important E0
  outcome per the task brief).** At the SAME state + ceiling, spill-ON **also OOMs (137)** and writes
  **zero** spilled bytes (no SST, empty WAL). Across every workload shape tried, the spill governor
  never evicted a single slice in a real query. Root cause (code-verified, below): the spillable
  store can only evict **COLD** slices (`sliceEnd < build-watermark`, not-yet-emitted), but the
  engine **couples build-watermark-advance to immediate emit** (`getTriggerableWindowSlices` both
  learns the watermark AND returns the now-triggerable slices, which are emitted at once). So a
  monotonic-timestamp tumbling query holds only HOT (actively-built) windows resident; completed
  windows are emitted+freed the instant they go cold. Cold-slice eviction is exercised by the unit
  tests **only because they manually decouple `setLastSeenWatermarkForTest` from
  `getTriggerableWindowSlices`** (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp:380-386`,
  the documented `SPILL_LEAD` emit-horizon lag) — a decoupling the real push pipeline does not create.
- **Engine floor / ceiling: 512 MB is usable** with a small buffer pool. NES embedded-worker floor
  (trivial query) ≈ **46 MB** with a 16 MB pool, but the buffer pool is counted **twice** (the
  systest embeds two nodes: source-node + sink-node), and at the **default** 32768-buffer pool
  (~128 MB) the floor jumps to **256–320 MB**. So spill headroom at 512 MB depends entirely on
  shrinking `number_of_buffers_in_global_buffer_manager`.

---

## Run mode used
Normal mode (not `-b`). **Benchmark mode `-b` is unusable for E0**: it `skip`s the query as "failing"
(SystestRunner.cpp:461) the moment registration/execution errors, masking the real outcome. In normal
mode the query is reported FAILED purely on output-mismatch against the placeholder expected block —
which is fine; we read the **process exit code** (137 = cgroup OOM-kill) and the **`-d` spill DEBUG
log** instead. `--show-query-performance` gives elapsed time; tuples/sec is NaN with Generator
(no input file), as EXPLORE-INFRA §3a predicted.

## Deviations from EXPLORE-INFRA.md (ground-truth corrections)
1. **RocksDB path parent must pre-exist.** `rocksdb_path` is suffixed `/agg-<handlerId>` and RocksDB's
   `create_if_missing` only makes the leaf dir, not parents. With the default `/tmp/nes-spill` the
   first run dies: `spill store failure; ... mkdir if missing: /tmp/nes-spill/agg-2: No such file or
   directory`. **Fix:** `mkdir -p /tmp/nes-spill` before every spill-ON run (added to all commands).
2. **Generator is throttled by default.** `GENERATOR_RATE_CONFIG` default `emit_rate 1000` =
   1000 tuples/s (FixedGeneratorRate.cpp:48; default at GeneratorSource.hpp:148). A 600k-tuple run
   crawled at ~770 tup/s (~13 min) and emitted "No progress for 10 seconds" keepalive warnings that
   *look* like a hang. **Fix:** add `'emit_rate 50000000' AS \`SOURCE\`.GENERATOR_RATE_CONFIG` to the
   `SET(...)` clause. With it, 600k tuples run in ~79 s under `-d`.
3. **Generator SEQUENCE semantics:** fields advance in **lockstep** (one step per tuple), each
   **clamps** at `end` (does NOT wrap), total tuples = max field of (end-start)/step; `STOP='ALL'`
   ends when all reach end. (Verified in GeneratorFields.cpp:235-261, Generator.cpp:113-122.) The
   EXPLORE skeleton implied independent ranges; in practice you size state via clamping behaviour.
4. **CLI worker keys are snake_case** (`--worker.query_engine.number_of_worker_threads`), despite the
   `--help` example showing camelCase — the help string is stale; snake_case (the option names) works.
5. **Default buffer pool is counted per embedded node (×2).** EXPLORE §2a says ~128 MB pool; the
   systest runs source-node AND sink-node in-process, each with its own BufferManager, so the real
   baseline is ~256 MB at default — this is what makes the default-pool floor 256–320 MB.

---

## The .test used (`nes-systests/operator/aggregation/SpillSmoke.test`, key params)
- Source: `TYPE Generator`, `STOP='ALL'`, `emit_rate 50000000`,
  `GENERATOR_SCHEMA = 'SEQUENCE UINT64 0 6000000 1, SEQUENCE UINT64 0 100 1, SEQUENCE UINT64 0 500 1'`
  (key 0..6M step1 = 6M distinct keys; val 0..100; ts 0..500 clamps).
- Query: `SELECT start,end,key,SUM(val) ... GROUP BY key WINDOW TUMBLING(ts, size 100000 ms)`.
  ts max 500 < window 100000 ⇒ ALL keys land in ONE window [0,100000) ⇒ one ~240 MB hash map.
- No valid expected block (placeholder `0,100000,0,0`); E0 reads exit code + log.

## §3.3 demonstration (the headline pair) — raw evidence
Helper: `IMG=nebulastream/nes-development:local-rocksdb`, mount repo at `/workspace`.

**Spill OFF, capped 512 MB (in-memory `DefaultTimeBasedSliceStore`):**
```
docker run --rm --memory=512m --memory-swap=512m --cpus=1 --workdir /workspace -v "$PWD:/workspace" $IMG \
  bash -c "cmake-build-debug/nes-systests/systest/systest -t nes-systests/operator/aggregation/SpillSmoke.test \
     --workingDir /workspace/cmake-build-debug/systest-run --show-query-performance \
     -- --worker.default_query_execution.execution_mode=COMPILER \
        --worker.query_engine.number_of_worker_threads=4 \
        --worker.number_of_buffers_in_global_buffer_manager=8000 \
        --worker.default_query_execution.spill.enabled=false"
# => INNER_EXIT=137   (cgroup OOM-kill)   ✅ in-memory baseline dies
```

**Spill ON, capped 512 MB (same state, hard 192 / soft 128 MB, lz4):**
```
docker run --rm --memory=512m --memory-swap=512m --cpus=1 --workdir /workspace -v "$PWD:/workspace" $IMG \
  bash -c "rm -rf /tmp/nes-spill && mkdir -p /tmp/nes-spill && \
     cmake-build-debug/nes-systests/systest/systest -t nes-systests/operator/aggregation/SpillSmoke.test \
     --workingDir /workspace/cmake-build-debug/systest-run \
     -- --worker.default_query_execution.execution_mode=COMPILER \
        --worker.query_engine.number_of_worker_threads=4 \
        --worker.number_of_buffers_in_global_buffer_manager=8000 \
        --worker.default_query_execution.spill.enabled=true \
        --worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill \
        --worker.default_query_execution.spill.soft_threshold_mb=128 \
        --worker.default_query_execution.spill.hard_threshold_mb=192 \
        --worker.default_query_execution.spill.compression=lz4"
# => INNER_EXIT=137 ; SST=0 ; /tmp/nes-spill/agg-2 = metadata only (WAL 0 bytes)  ❌ spill did NOT save it
```
**Result: spill-ON does NOT survive where in-memory OOMs — because nothing was spilled.** The single
giant window is the HOT slice; it can never be evicted (see Structural blocker).

## Structural blocker (code-verified) — why no real query spills
- A slice is spillable **iff** `sliceEnd < lastSeenWatermark` AND not yet emitted-to-probe
  (`SpillableTimeBasedSliceStore.cpp:478-505`, the cold-pick scan).
- `lastSeenWatermark` (the "build watermark") is advanced ONLY inside `getTriggerableWindowSlices`
  (cpp:269-279) — the **same** call that returns the slices to emit. So the instant a window becomes
  cold, it is also returned for emit and `unspillAndMarkEmitted` marks it emitted (cpp:268, 200-260).
  There is no steady-state set of "cold but not-yet-emitted" slices for the spiller to grab.
- The synchronous hard check (`getSlicesOrCreate`, cpp:169-175) runs `evictUntilUnderHard()` **only on
  genuinely-NEW-slice creation**; with one ever-growing window it fires once (when empty) and never
  again — a 1M-key single-window `-d` run logged **0** `Hard threshold crossed` / `resident=` lines.
- Multi-window monotonic runs DO advance the watermark per buffer, but each completed window is
  emitted+GC'd immediately, so resident **plateaus at the few hot windows** (~16 MB observed) and never
  needs spilling. A 6-window/100k-key `-d` run logged 3× `Hard threshold crossed (resident≈16 MB)`
  each immediately followed by `evictUntilUnderHard ... no COLD slice to evict; proceeding over-budget`
  — i.e. the governor wanted to spill but had no cold candidate.
- The unit tests reach `spillCountForTest() > 0` only by manually seeding the watermark ahead of the
  emit horizon (`setLastSeenWatermarkForTest` decoupled from `getTriggerableWindowSlices`,
  ConcurrencyTest.cpp:380-386 "EMIT HORIZON lags by SPILL_LEAD windows"). The real push pipeline
  provides no such lag.
- **Implication for E1/E2:** to make spilling demonstrable end-to-end, the eval needs EITHER
  (a) a workload/engine path that holds completed windows cold-but-unemitted (e.g. emit/probe
  backpressure, an allowed-lateness / watermark-delay knob — none exists today, grep = 0), OR
  (b) production exposure of `forceSpill`/threshold-driven spill on the **hot** slice (currently
  forbidden: hot slices are never evicted by design). This is a real finding to surface before
  building E1 instrumentation — instrumenting spill latency is moot if no real query spills.

## Memory ceiling + engine floor (trivial-query sweep)
Trivial query = `WindowAggregationDifferentDataTypes.test:1` (8 inline rows). `--memory-swap=--memory`,
`--cpus=1`. exit 137 = OOM-kill, 0 = survived.

| Buffer pool | 512 | 384 | 320 | 256 | 192 | 160 | 128 | 96 | 64 | 48 | 44 | 40 | 32 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| default 32768 (~128 MB ×2 nodes) | — | 0 | 0 | **137** | 137 | 137 | — | — | — | — | — | — | — |
| small 4000 (~16 MB ×2 nodes) | — | — | — | 0 | — | — | — | 0 | 0 | 0 | **137** | 137 | 137 |

- **Default-pool floor: 256–320 MB** (survives 320, OOMs 256). The 2× buffer pool dominates.
- **Small-pool floor: ~46 MB** (survives 48, OOMs 44). This is the irreducible NES embedded-worker +
  Nautilus-JIT + 2×16 MB pool footprint.
- **512 MB ceiling is comfortably usable** for the eval IF the buffer pool is kept small
  (`number_of_buffers_in_global_buffer_manager≈8000` ⇒ 2×32 MB = 64 MB pool ⇒ ~440 MB state headroom).

## State-size → resident map (single giant window, spill OFF, NO cap)
Resident measured as **peak cgroup `memory.current`** (includes default 128 MB×2 pool + page cache +
transient build buffers, so it OVER-counts the pure hash map). Single window ⇒ all keys resident at once.

| distinct keys | peak cgroup RSS | est. state Δ over ~399 MB intercept |
|---|---|---|
| 250 000 | 471 MB | ~72 MB |
| 500 000 | 552 MB | ~153 MB |
| 1 000 000 | 707 MB | ~308 MB |
| 2 000 000 | 1003 MB | ~604 MB |
| 6 000 000 | (OOM-kills at 512 MB cap; ~240 MB pure-map estimate) | — |

- Linear fit: **~318 B/key in real RSS** (intercept ~399 MB = 256 MB default pool + ~140 MB
  engine/JIT/page-cache). The **pure hash-map** footprint (the spill store's own `residentBytes`
  accounting) is `⌈keys/25⌉×1024 B ≈ 41 B/key` — the gap (≈277 B/key) is page cache + chaining +
  build-side transient buffers + RocksDB-irrelevant overhead. Use **~41 B/key** for the store-internal
  spill-threshold math and **~300 B/key** for sizing real RSS against the cgroup ceiling.
- **OOM crossover at 512 MB cap (8000-buffer pool): observed at 6M keys (spill OFF, exit 137);**
  250k keys at default pool already sits at 471 MB cgroup. For E2, pick state points by key count:
  ~1M keys ≈ 0.7 GB cgroup (OOMs a 512 cap), ~250k ≈ near the 512 line — straddle the ceiling there.

## Threshold placement that "worked" (mechanically engaged, even if it couldn't find a cold slice)
For ceiling C = 512 MB with the small (8000) pool: **hard ≈ 192 MB (C·3/8), soft ≈ 128 MB (C/4),
RocksDB lz4, buffer pool 8000 (~64 MB ×... 2 nodes)**. The hard watermark IS crossed and the
synchronous evictor IS invoked (`Hard threshold crossed (resident=…> hard=…)`), so the placement is
correct relative to the ceiling; it simply finds no cold slice to spill (the blocker above). The
general rule confirmed sound: keep `hard < soft-headroom < ceiling` and pool small.

## Files / artifacts
- Test: `nes-systests/operator/aggregation/SpillSmoke.test` (NOT committed — E0 is exploratory).
- Logs: `cmake-build-debug/e0-*.log` (unthrottle/anchor have the threshold-cross + no-cold-slice lines).
- Spill dir during runs: `/tmp/nes-spill/agg-2` (inside container) — never held SST/WAL data in any run.

## Honest verdict
The OOM-survival **gate** is half-proven: the in-memory baseline reliably OOMs at 512 MB on tractable
state, and the ceiling/floor/calibration knobs the later phases need are measured. The **spill-survives**
half is **blocked by an engine-level coupling** between watermark-advance and emit that prevents a real
tumbling query from ever holding spillable (cold, unemitted) state — verified in code and across 4
workload shapes, with zero bytes ever written to RocksDB. This must be resolved (workload path or a
sanctioned hot/eager-spill trigger) BEFORE E1 instrumentation, since spill-latency metrics are
meaningless until a real query spills.
```
