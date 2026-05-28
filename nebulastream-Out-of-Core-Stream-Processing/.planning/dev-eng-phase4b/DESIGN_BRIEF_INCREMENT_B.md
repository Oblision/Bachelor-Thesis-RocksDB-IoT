# Phase 4b — Increment B design brief (full run: auto-eviction + multi-thread hardening + varsized)

Locked via design discussion 2026-05-23. HEAD `6a6ec7301b` (Increment A complete, suite 805/805). Branch
`feat/ooc-phase1-spill-skeleton`, clean. Build/test ONLY in Docker `nebulastream/nes-development:local-rocksdb`,
`cmake-build-debug`, `ninja` + `ctest -LE 'Systest|DockerCompose'`. This brief is authoritative context for the
dev-eng-orchestra-cpp run. **B has more genuine unknowns than A** — most big decisions are OPEN for the architects.

## Goal
Turn the Increment-A spike (forced, single-threaded spill) into a real out-of-core feature: (1) automatic
memory-budget-driven eviction of cold slices, (2) correct under the engine's real concurrency, (3) variable-sized
keys/values. End state: a windowed-aggregation query under a memory budget spills cold slices automatically and
completes correctly where it would otherwise OOM — safe with N worker threads.

## Inherited LOCKED decisions from Increment A (do NOT re-litigate)
- **Route-B seam:** consumers dispatch via `HashMapSlice&` (getHashMapPtr/OrCreate/getNumberOfTuples virtual).
- **`SpillableTimeBasedSliceStore`** subclasses un-final'd `DefaultTimeBasedSliceStore`, owns the `SpillBackend`,
  reuses `createSliceStoreRef`/`SliceCache`/ref unchanged. Build hot path only touches resident WINDOW_FILLING slices.
- **Spill target = COLD slices** (`sliceEnd < watermark`, stopped filling, NOT emitted). `EMITTED_TO_PROBE` slices
  must NEVER be spilled — `triggerSlices` snapshots their raw `HashMap*` for the ASYNC probe (UAF otherwise). This is
  the **async-probe-drain invariant** and it is absolute.
- **Unspill-on-read** happens in the store's trigger feeders (`getTriggerableWindowSlices`, `getAllNonTriggeredSlices`).
- **Budget = per-store resident page math** (Option 1): `Σ_workers ⌈tuples_w/entriesPerPage⌉ × pageSize`, 0 when spilled.
  Pool occupancy rejected (aggregation memory is unpooled).
- `SliceSerializer` MAGIC/VERSION format + the existing `forceSpill`/`isSliceResident`/`residentBytesOf` API stay
  (forceSpill remains useful for tests; B adds the *automatic* path on top).

## SCOPE CORRECTION (2026-05-23) — locked against the signed thesis proposal (DIMA V2.4)
The proposal is authoritative and RE-SCOPES this increment. Out of scope per §2.3.2: **sliding windows, general-purpose
(variable-sized) serialization, and any Nautilus JIT / code-generation modification.** Goal §2.1: **tumbling windows only.**
Eviction model §3.1 (verbatim intent): soft threshold → **background-thread preemptive spill of COLD window state**
(cold = `end-timestamp < watermark`), workers keep processing; hard threshold → **synchronous mandatory spill** to avoid OOM.
Success §3.3 + validation: process tumbling state > memory without OOM, measured by throughput / tail latency / spill+restore
latency via **systest experiments (MS3/MS4)** — NOT ThreadSanitizer.

Consequences (these SUPERSEDE the original B scope below):
- **DROP B-varsized entirely** (general-purpose serialization is out of scope). Keep SliceSerializer fixed-size.
- **No core base-class change** (no protected helpers on DefaultTimeBasedSliceStore). Tumbling ⇒ one slice == one window ⇒
  `cold = sliceEnd < watermark` is determinable by **store self-tracking** (override getSlicesOrCreate to record created slices;
  learn the watermark from the trigger feeders). O-B1 resolved as option (a).
- **No JIT/SliceCache modification.** Respect the existing watermark-monotonicity contract (a cold tumbling slice's window is
  already past trigger, so the build cache won't re-look-it-up). The cache-UAF concern is a contract to RESPECT, not code to change.
- **Concurrency is bounded:** a background spiller thread spilling cold slices + the existing worker/trigger interleaving. Carry
  A's async-probe-drain invariant + emittedKeys guard + idempotent spill/unspill; add locking so the background spiller's
  pick-cold→confirm-not-emitted→spill is atomic vs a trigger marking emitted (closes A's M1 TOCTOU). Validate via a
  multi-threaded stress test (no TSan — unavailable here) + the thesis systest under enforced Docker memory limits.
- Timeline: SC2 finalize-system phase → aim for complete+correct+defensible, do NOT gold-plate.

## DECISION (2026-05-23, user) — soft-spill strategy = BACKGROUND SPILLER THREAD (§3.1 literal)
After analyzing background-thread vs synchronous against the 3 RQs + the no-TSan/SC2 constraints, the user chose the
§3.1-literal design:
- **SOFT threshold → a dedicated background spiller thread** preemptively spills COLD slices (`sliceEnd < watermark`,
  not emitted) so worker threads keep processing (best tail-latency story; matches the proposal verbatim).
- **HARD threshold → synchronous mandatory spill** on the allocating/worker path to prevent OOM.
- This is the higher-concurrency path. It CANNOT be ThreadSanitizer-validated in this environment (TSan needs a
  multi-hour dep-image rebuild — see exploration/BUILD.md). Validation = a multi-threaded stress test (no sanitizer,
  template: nes-input-formatters/.../ConcurrentSynchronizationTest.cpp) + the thesis systest under enforced Docker
  memory limits (MS3/MS4). Honest validation ceiling to be documented.
- Concurrency invariants to preserve (carry from Increment A): async-probe-drain (NEVER spill an EMITTED_TO_PROBE
  slice — its raw HashMap* is in flight to the async probe), emittedKeys guard, idempotent spill/unspill. The background
  spiller's pick-cold→confirm-not-emitted→spill must be atomic vs a worker/trigger marking the slice emitted (closes
  A's M1 TOCTOU) — likely a dedicated eviction mutex + the spiller doing I/O outside the lock.
- Still: NO core base-class change (self-tracking enumeration suffices for tumbling), NO JIT/SliceCache change.
- Background-thread lifecycle: start/stop tied to the store (or operator handler) lifetime; clean shutdown (join) on
  teardown; must not outlive the BufferManager/backend it uses.

NEXT (resume after compaction): orchestra Phase 2 SYNTHESIS → Phase 3 ARCHITECTURE for this background-spiller design,
then RED tests (incl. the stress test) → implement → cpp-review → verify (regression stays ≥805) → manual push/PR.

## Increment B scope (ORIGINAL — partially superseded by the SCOPE CORRECTION above; varsized + sliding + core-edit dropped)
### B-evict — automatic budget-driven eviction
- Store-wide `residentBytes()` + identify COLD spill candidates, oldest-first, spill until back under soft threshold.
- Trigger point: when does the budget get checked / eviction run? (after getSlicesOrCreate creates a slice? a periodic
  tick? on the build/trigger path?) — OPEN.
- Hard-threshold semantics (O4): block / aggressive-spill / spill-then-error when even all-cold-spilled is over budget.
### B-concurrency — multi-thread D6 hardening (the iceberg)
- `forceSpill`/auto-evict check+spill must be atomic vs a concurrent trigger marking the slice emitted (the A-noted
  TOCTOU). Establish a single, documented lock discipline (A's rule: never hold two store locks at once — may need revisiting).
- **SliceCache stale-entry invariant:** a spilled cold slice's range must never be re-looked-up by the build hot path
  (it caches raw `HashMap*`). Verify watermark-monotonicity guarantees this, or invalidate the cache entry on spill. Test it.
- Concurrent build (N workers writing resident filling slices) vs trigger (unspill) vs eviction (spill): prove no slice
  is spilled while a worker holds its `HashMap*`, and no two threads double-spill/double-unspill. Idempotency already holds.
### B-varsized — variable-sized keys/values in spill
- `SliceSerializer` is fixed-size only today (the Phase-2 guard). Extend to serialize/deserialize the ChainedHashMap's
  `varSizedSpace` (unpooled var-sized buffers) so var-sized aggregations can spill. Round-trip parity tests.

## OPEN (architects MUST resolve with code visibility) — these are the hard parts
- **O-B1 — Cold-slice enumeration blocker.** The base `slices`/`windows` maps are PRIVATE; the subclass cannot scan
  them to find cold candidates or compute store-wide residentBytes(). Options: (a) store self-tracks created slices +
  their state by overriding `getSlicesOrCreate` and observing the trigger feeders; (b) add a `protected` accessor /
  `protected:` the maps on the base; (c) a protected virtual hook. Pick the lowest-blast-radius option; (b)/(c) touch
  core but may be cleanest. **Decide and justify; this gates B-evict.**
- **O-B2 — Eviction trigger & cadence.** Inline on slice creation (simple, adds latency to the build path) vs a
  dedicated eviction step/thread vs piggyback on the existing GC/trigger cadence. Consider the JIT build hot path —
  eviction must not run on it per-tuple.
- **O-B3 — Locking model under real concurrency.** Whether A's "never hold two store locks" rule survives auto-eviction
  (which must atomically: pick a cold slice, confirm not-emitted, spill, update tracked sets). May need a single
  coarse eviction mutex or a redesign. Define the invariant precisely + how it's tested.
- **O-B4 — Concurrency validation strategy.** What can actually be tested in-gate? `cmake/Sanitizers.cmake` exists —
  determine if a ThreadSanitizer build is feasible and wire a TSan stress test if so. Otherwise: multi-threaded stress
  tests (N threads, many iterations) + an out-of-gate systest query under a tiny budget. Be honest about coverage limits.
- **O-B5 — Eviction policy edge cases.** What if no COLD slice exists but we're over budget (everything is filling or
  emitted)? (Likely: cannot evict → proceed/over-budget, log; do NOT spill filling/emitted.) Define behavior.

## Risks (carry into SYNTHESIS)
- Heisenbugs: concurrency correctness is hard to prove in-gate. If validation isn't tractable, the run should ESCALATE
  with a clear status rather than ship unverified concurrency.
- Touching the base store's encapsulation (O-B1 option b/c) widens blast radius — keep regression green (805 baseline).
- Varsized serialization is error-prone (offsets, the var-sized buffer relocation) — strong round-trip tests required.

## Test strategy (must)
- B-evict: deterministic single-threaded budget tests (fill past soft → assert oldest-cold spilled, under-budget after,
  correctness preserved); hard-threshold behavior.
- B-concurrency: multi-thread stress (build+trigger+evict) asserting no crash/UAF + correct aggregates; TSan if feasible.
- B-varsized: serialize→deserialize parity for var-sized key/value entries (extend SliceSerializerTest).
- Regression: full suite stays green vs the 805 baseline.

## Out of scope
- Join (HJ/NLJ) spilling. The eval phase (memory-ceiling vs throughput/latency benchmarking) — that's the next phase.
