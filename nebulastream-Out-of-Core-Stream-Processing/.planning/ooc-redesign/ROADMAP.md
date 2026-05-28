# OOC Emit Redesign — Roadmap (streaming, bounded-memory spill read-back)

Branch base `feat/ooc-phase1-spill-skeleton` @ `1f30a75822`. Build/test ONLY in Docker
`nebulastream/nes-development:local-rocksdb`, dir `cmake-build-debug`, `ninja` + `ctest -LE 'Systest|DockerCompose'`.
Scope authority: signed proposal DIMA V2.4 §2.3.2 — **NO Nautilus/JIT/codegen edits, NO sliding windows, NO
variable-sized serialization.** Manual push policy (human reviews commits). Exploration evidence:
`.planning/ooc-redesign/exploration/{EMIT-PATH,STORE-SPILL,HANDLER-JIT-PARTITION}.md`.

## 1. Problem (what E0 proved)
Spilling relieves *steady-state* resident memory, but the **terminal flush re-materialises the whole retained band
of windows into the buffer pool at once** to emit it → peak ≈ the entire band → OOM/starvation, defeating the spill.
No config knob fixes this (threshold = steady-state only; pool size = OOM-vs-starve pincer; `emit_lag` only "helps"
by shrinking the band until OOC is unnecessary). The fix is architectural: **bounded, incremental, partitioned
read-back** — the external-merge-sort / grace-hash discipline NES's spill path is currently missing.

## 2. The constraint that shapes the design (host/JIT boundary)
- **Emit read-out is JIT.** `AggregationProbePhysicalOperator::open()` is pure `nautilus::val<>` iterating a
  `ChainedHashMapRef` (`AggregationProbePhysicalOperator.cpp:51-170`). Input contract = raw `ChainedHashMap*`.
- **Build insert is JIT.** Cache HIT returns the raw `HashMap*` in-trace with no host call
  (`SliceCacheSecondChance.cpp:84-88`); key→bucket routing `findOrCreateEntry(record, MurMur3HashFunction)` is
  generated (`AggregationBuildPhysicalOperator.cpp:107`). MISS crosses host once via `nautilus::invoke(…cacheMissProxy)`
  (`DefaultTimeBasedSliceStoreRef.cpp:104-111`).
- ⇒ We may freely change: the **store**, the **handler `triggerSlices`/emit pacing**, the **SpillBackend +
  SliceSerializer**, and **spill-time data layout**. We may NOT change how the probe scans a map or how the build
  routes a key. Therefore bounded chunks fed to the probe MUST be **key-disjoint partitions** (each key emitted once).
- ⇒ **"Stream RocksDB → sink" (original L2) is infeasible host-only.** The feasible equivalent is **partitioned
  chunked emit** (L2⊕L3): stream a partition's records from RocksDB into a *small* chunk map, hand that to the JIT
  probe, free it, repeat. Peak = one partition, independent of window size.

## 3. Target architecture (end state)
```
build (JIT, unchanged) ─▶ per-window per-worker ChainedHashMap (one slice per sliceEnd)
        │  cold & unemitted (emit_lag band)
        ▼
   SPILL  ── post-hoc partition by stored hash%P ──▶ RocksDB: (sliceEnd, worker, partition) → fixed-stride records
        │
        ▼  (quiescence / watermark)
   EMIT (host pacing, bounded):
     for each window w, IN BOUNDED IN-FLIGHT WINDOW:
       for each partition p in 0..P:                 ◀── streaming record cursor, NO full-map rebuild
          rent small chunk map ≤ |w|/P pages
          decode partition p records into chunk map
          hand chunk map to JIT probe (one EmittedAggregationWindow, chunk=p/lastChunk)
          free chunk map
   peak resident ≈ ONE partition (+ bounded in-flight emit set)   ← independent of band size AND window size
```

## 4. Phases (each ends green: `ninja` + full `ctest -LE 'Systest|DockerCompose'` ≥ baseline, + new tests)

### Phase 0 — Measurement & enabling (prerequisite; was EVAL-PLAN E1)
Without this we cannot *prove* any phase works (E0 showed the systest harness OOMs on its own 2M-row result-check and
adds a NetworkSink burst — both confound the signal).
- **0a. RocksDB memory wiring.** Thread `write_buffer_size` + `block_cache` from `SpillConfig` →
  `RocksDBSpillBackend` (today only `compression` is wired). Removes the ~64–80 MB default memtable/cache tax.
  Files: `SpillConfig.hpp` (+2 UIntOptions), `RocksDBSpillBackend.{hpp,cpp}` (`rocksdb::Options`), lowering passthrough.
- **0b. Spill/restore instrumentation.** Counters + timers: bytes spilled/restored, slices spilled/unspilled,
  per-op spill & restore latency (for p95/p99), peak resident bytes (store-internal). Expose via the handler/log.
- **0c. Standalone single-node eval driver** (outside systest): direct File sink (no NetworkSink hop), **no
  in-process 2M-row result-check** (verify correctness out-of-band on disk), reads cgroup `memory.peak`. This is the
  instrument that turns "OOM-kill / didn't OOM" into "peak RSS = X MB". Reuse Microbenchmark-BA `analyze.py`.
- **Exit:** can run the breadth workload under a cap and report peak RSS + spill/restore latencies, no harness OOM.

### Phase 1 — L1: Incremental, backpressure-paced emit (host-only; the demonstrated fix)
Make emit process **one window at a time**, freeing each before unspilling the next, and **bound the in-flight
emitted set** (emit is async — `emitBuffer` queues a task; unbounded unspill is the real peak driver).
- **Mechanism:** restructure the terminal/quiescence flush so it does not unspill the whole band. Today:
  `WindowBuildPhysicalOperator` terminate → `triggerAllWindows` → `getAllNonTriggeredSlices` →
  `unspillAndMarkEmitted(ALL)` (`SpillableTimeBasedSliceStore.cpp:210-278, 357-365`). Change to a generator/iterator
  that yields one window's slices, lets the handler emit it, waits until that emit buffer is drained/freed (bounded
  in-flight count K, default small), then yields the next. Steady-state path already emits per-buffer
  (`WindowBuildPhysicalOperator::close:71-84`) — align it to the same bounded discipline.
- **Backpressure hook:** observe sink/buffer pressure where `triggerSlices` already allocates
  (`AggregationOperatorHandler.cpp:120-148`, `getUnpooledBuffer`/`emitBuffer`) and gate the next unspill on it.
- **Files (host-only):** `SpillableTimeBasedSliceStore.{hpp,cpp}` (incremental flush API + bounded unspill),
  `AggregationOperatorHandler.cpp` (paced `triggerSlices`), maybe `WindowBuildPhysicalOperator.cpp` (terminate loop).
  JIT probe UNCHANGED (still one `EmittedAggregationWindow` per window).
- **Tests:** unit — flush of N spilled windows never holds >K resident simultaneously (assert via resident-bytes
  hook / spill-unspill counts); correctness identical to baseline. Driver — breadth workload completes under a cap
  where the bulk flush OOM'd; report peak RSS drop.
- **Exit / thesis value:** **§3.3 demo for the breadth case** — a query whose retained band > RAM completes under
  the cap because peak ≈ one window, not the band. This is the minimum publishable result.
- **Risk:** async emit means a window's pages free only after the probe advances the watermark (GC). The bound K must
  account for in-flight (emitted-not-yet-GC'd) windows; pin/`emittedKeys` already prevents re-spill of in-flight maps.

### Phase 2 — L2⊕L3: Partitioned spill + streaming chunked emit (the headline contribution)
Bound peak to **one partition**, independent of *window size* (handles large-but-buildable windows; breadth too).
- **2a. Multi-chunk window support (GATING — verify first).** Confirm/extend `EmittedAggregationWindow`
  (`AggregationOperatorHandler.hpp:39-55`) + probe combine so ONE window may be emitted as P key-disjoint chunks
  (the struct already carries chunk/lastChunk/seq fields per EMIT-PATH §5 — verify they drive correct combine and
  EOS, no duplicate rows). If unsupported, this phase is blocked at the framework level — decide extend-vs-stop here.
- **2b. Post-hoc partitioned spill (host-only).** In `SpillableAggregationSlice::spill()`
  (`SpillableAggregationSlice.cpp:85-117`) bucket the fixed-stride records by the **already-stored** hash (`hash % P`,
  no rehash — `SliceSerializer` blob is `[hash|key|value]*`) into P blobs. Extend the spill key to
  `(sliceEnd, workerThreadId, partitionId)` (`RocksDBSpillBackend` 12→? byte key; prefix-erase still works).
  Choose P from a memory budget (target partition ≤ a few MB). Update residentBytes accounting per partition.
- **2c. Streaming record cursor (host-only).** Add `SliceSerializer::recordCursor(blob)` (fixed-stride decode, one
  batch at a time, ZERO pool pages) and `SpillBackend::streamSpilledRecords(sliceEnd, worker, partition, batchCb)`
  (+ Null/InMemory/RocksDB impls). RocksDB reads the partition blob (or, optional 2c′, range-scans if we later move
  to per-record keys — see Phase 3).
- **2d. Partitioned chunked emit (host-only).** In the store's emit path: for each window, for each partition,
  decode the partition's records into a *small* chunk `ChainedHashMap` rented from the buffer provider, hand it to the
  existing JIT probe as one `EmittedAggregationWindow` (chunk=p, lastChunk=p==P-1), free it, next partition. Build the
  chunk map with the host `ChainedHashMap` write API (no JIT).
- **Tests:** unit — emitting a window with P partitions yields exactly the baseline rows (set-equal), each key once;
  peak resident during emit ≤ one partition + chunk map; cursor decodes blobs without renting full-window pages.
  Driver — a single large window (built across the stream, spilled) read back under a cap < window size.
- **Exit / thesis value:** **bounded read-back memory independent of window size** — the genuinely novel
  external-memory-operator result. Spill/restore p95/p99 vs partition count P (RQ2/RQ3).
- **Risk:** correctness of multi-chunk combine (2a) is the linchpin; partition skew (hash%P uneven) → size P or
  document; more, smaller RocksDB values → I/O amplification (measure, tune wbuf/block cache from 0a).

### Phase 3 — Demand-driven read-back + frontier/future
- **3a. Pull-based read-back.** Drive partition/chunk unspill from sink readiness (only fetch the next chunk when
  downstream can accept it), fully decoupling read-back rate from a fixed loop. Builds on Phase 1's backpressure hook.
- **3b. (Optional) per-record RocksDB keys** `(sliceEnd|worker|partition|hash|key)→value` for native range-scan
  iteration (no blob), enabling true streaming + future external sort/merge. Rewrites spill format + erase.
- **OUT OF SCOPE (future work, state explicitly in thesis):** bounding a **single window's BUILD** when it exceeds
  RAM during ingestion (the depth case) — requires an **insert-time grace-hash router in generated code**
  (`AggregationBuildPhysicalOperator.cpp:107`) = Nautilus/JIT edit = forbidden by §2.3.2. Phases 1–2 bound spill,
  read-back and emit; they do not bound the build of one oversized window.

## 5. Scope guardrails (from HANDLER-JIT-PARTITION §5)
| Change | Verdict | Why |
|---|---|---|
| emit_lag retention, L1 incremental/backpressured emit | ✅ allowed | store + handler host code |
| L2⊕L3 partitioned spill (post-hoc, by stored hash) + streaming cursor | ✅ allowed | spill/serializer/backend host code; map already built |
| Multi-chunk window emit via existing chunk/lastChunk fields | ✅ allowed (verify 2a) | emit framework host code |
| RocksDB wbuf/block-cache wiring, instrumentation, standalone driver | ✅ allowed | config + host + tooling |
| Insert-time sub-map routing / grace-hash on build | ❌ forbidden | edits generated `findOrCreateEntry` (JIT) |
| Residency/epoch check on the cache-HIT path | ❌ forbidden | edits in-trace HIT (JIT), breaks watermark contract |
| `SliceCacheEntry` layout/atomicity, sliding-window, varsized serialization | ❌ forbidden | §2.3.2 exclusions |

## 6. Sequencing & dependencies
```
Phase 0 (0a wbuf/cache → 0b instrumentation → 0c driver)   [enables measurement of all below]
   └─▶ Phase 1 (L1 incremental emit)        [standalone thesis result: breadth §3.3]
          └─▶ Phase 2 (2a verify → 2b spill → 2c cursor → 2d emit)   [headline: size-independent read-back]
                 └─▶ Phase 3 (pull-based; optional per-record keys; depth=future)
```
Each phase is independently shippable & reviewable (manual push). Recommended first commit unit: **Phase 0a**
(smallest, unblocks tight-cap headroom + needed by every later measurement).

## 7. Open questions to resolve at phase entry
- P1: what is the right in-flight bound K given async emit + GC-after-watermark? (measure; start K=1–2.)
- P2a: do `EmittedAggregationWindow` chunk/lastChunk/seq fields already yield correct multi-chunk combine, or must
  the host combine be extended? (read probe combine + EOS handling; this gates Phase 2.)
- P2b: choose P (fixed vs budget-derived from residentBytes); handle hash%P skew.
- P0c: simplest standalone driver — a tiny gtest/main linking the engine with a Generator source + File sink, or a
  trimmed systest invocation with result-check disabled? (prefer a small dedicated driver for clean RSS.)
- Execution vehicle: run each phase via `/dev-eng-orchestra-cpp` (TDD + cpp-review, Docker gates, regression ≥ baseline).
