# 2d Concurrency / Lifetime Audit — spill-vs-emit UAF & data race

**Scope:** uncommitted Phase 2 step 2d change. Read-only audit of the partitioned TERMINAL emit
running while the BACKGROUND SPILLER thread is LIVE. Highest-risk dimension of the change.

**Files audited (uncommitted diff):**
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp`
- `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp`
- `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp`
- `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp`
- `nes-physical-operators/src/WindowBuildPhysicalOperator.cpp` (terminate path)

---

## VERDICT: **SAFE**

No use-after-free and no data race found on the spill-vs-emit dimension. The load-bearing
claim-before-mutex-release invariant is preserved through the extraction, the spiller is provably
fenced off every claimed slice (both `spill()` mutator call sites reject `emittedKeys` slices under
`evictionMutex`), the lock-free streaming reads operate only on claimed slices whose residency is
stable, and the P>1 ownership hand-off is leak-total by construction (registry-owned, raw-pointer
emit buffer). Only NON-blocking observations (Low severity, no fix required) are listed at the end.

---

## Q1 — Claim correctness (can the spiller still pick/spill a claimed slice?)

**NO.** After `claimWindowForPartitionedEmit` returns, the spiller can neither pick, spill, nor free
any slice of the window.

- `claimSliceForEmit` (`SpillableTimeBasedSliceStore.cpp:245-266`) runs ENTIRELY under
  `evictionMutex` (`:251`). It first `spillerCv.wait`s out any in-flight spill
  (`!spillInProgress.contains(sliceEnd)`, `:252-256`), then `emittedKeys.wlock()->insert(sliceEnd)`
  + `pinnedKeys.erase(sliceEnd)` (`:259,262`) **before the unique_lock leaves scope** — the
  claim-before-release invariant. The function does NOT touch `spillInProgress` (`:263-264`), exactly
  as specified (that fence belongs only to the unspill path).
- The ONLY two `spillable->spill()` mutator call sites are `forceSpill` (`:687`) and
  `spillOneColdSlice` (`:818`). Both are gated on the emitted check:
  - `pickAndReserveColdSlice` (`:696-770`) holds `evictionMutex` across the whole snapshot+predicate
    scan and rejects emitted slices at `:754-758` (`emittedKeys.rlock()->contains(end) → continue`)
    AND pinned at `:759-764`, BEFORE `spillInProgress.insert(end)` at `:765`.
  - `forceSpill` throws `SpillStoreFailure` on an emitted slice at `:656-660` (under `evictionMutex`,
    before reserving).
- Because the claim inserts into `emittedKeys` under the same `evictionMutex` that
  `pickAndReserveColdSlice` holds across check+reserve, there is no TOCTOU window: a spiller that has
  not yet reserved the slice will see it emitted and `continue`; a spiller that already reserved it
  (mid-spill) is waited out by `claimSliceForEmit` before the claim is recorded.

**Invariant preserved post-extraction:** YES. The extracted `claimSliceForEmit` body is line-for-line
identical to the old inline claim half (compare the removed block in the diff to `:251-262`): same
`evictionMutex` scope, same `spillerCv.wait` predicate, same insert/erase order, same
`!isResident()` return.

## Q2 — No double-fence / no leak (claim does not insert `spillInProgress`)

**SAFE.** `claimSliceForEmit` deliberately omits the `spillInProgress` reservation (`:263-264`); the
unspill branch of `ensureWindowSlicesResident` sets it itself (`:297-300`). No code reads
`spillInProgress` expecting the partitioned path to have set it:

- `residentBytes()` (`:875-915`) uses `spillInProgress` only to SKIP in-flight slices (`:896-899`).
  A claimed-but-not-reserved slice is therefore COUNTED by `residentBytes()` while
  `streamWindowPartition` reads it. This is correct and race-free: `streamWindowPartition` does NOT
  mutate the slice's maps (resident path filters into FRESH maps `materializeResidentPartition`
  `SpillableAggregationSlice.cpp:230-291`, source maps untouched; spilled path reads disk and
  deserializes into fresh maps, slice stays spilled). `residentBytes()` reads the resident slice's
  stable `hashMaps[]` footprint while holding `evictionMutex`; since the slice is emitted, no
  concurrent `spill()`/`unspill()` can begin to mutate those maps (Q1), so the footprint read is
  race-free even though `streamWindowPartition` is concurrently reading the same maps (two concurrent
  READERS, no writer). The omission of the `spillInProgress` fence is exactly correct: the
  partitioned path never mutates slice maps, so it needs no footprint-reader fence.
- No leak: the claim only inserts into `emittedKeys`; those entries are cleaned by GC
  (`garbageCollectSlicesAndWindows` `:574 emittedKeys.wlock()->erase`) and `deleteState`
  (`:619 emittedKeys.wlock()->clear`). No `spillInProgress` entry is created, so none can leak.

## Q3 — Resident-read race (`materializeResidentPartition` outside `evictionMutex`)

**SAFE.** After claim, the spiller skips emitted slices (Q1), so it cannot `spill()` (→
`hashMaps[pos].reset()`, `SpillableAggregationSlice.cpp:180`) a resident claimed slice mid-read.

- `materializeResidentPartition` is `const` and read-only: it iterates `hashMaps[pos]` chains
  (`:255-275`) and builds FRESH `dst` maps; the source slice is never mutated and `resident` stays
  true.
- The unlocked `bool resident` read in `streamWindowPartition` (`SpillableTimeBasedSliceStore.cpp:364`
  → `SpillableAggregationSlice.cpp:109-112 isResident()`) is benign: post-claim, `resident` has no
  concurrent writer (the only writers are `spill()`/`unspill()`, both excluded by the emitted check),
  so the read sees a stable value with no torn-read / no race.
- Erasing the pin (`pinnedKeys.erase` in the claim, `:262`) does not matter for safety here — the
  `emittedKeys` membership alone fences the spiller; the pin erase is just bookkeeping hygiene
  (Increment C). `pickAndReserveColdSlice` rejects on EITHER emitted OR pinned, so dropping the pin
  after marking emitted never re-opens a spill window.
- Edge case handled: if a spill was in flight at claim time, `claimSliceForEmit` waits it out; the
  slice then ends non-resident (`spill()` set `resident=false` at `:184` before clearing
  `spillInProgress` via `finalizeSpill`), so `streamWindowPartition` takes the SPILLED branch
  (`streamEmitByPartition`), never dereferencing freed maps.

## Q4 — `ensureWindowSlicesResident` regression (P=1 / steady path identical?)

**No regression.** The extraction left the steady/unspill path semantically identical:

- Claim half now delegates to `claimSliceForEmit` (`:290`); body byte-identical to the old inline
  block (Q1).
- Unspill half (`:291-323`) keeps the SAME `spillInProgress.insert` under a fresh `evictionMutex`
  scope (`:297-300`), the SAME `provider` INVARIANT (`:301`), the SAME `ReservationGuard`
  (`:309`), the SAME order (`unspill` → `recordUnspillSample` → `spilledKeys.erase` →
  `spillInProgress.erase` under lock → `notify_all` → `committed=true`, `:309-322`), matching the
  random-access path at `:527-539` and the late-write path at `:437-449`.
- The P=1 terminal flush itself is fully untouched: `AggregationOperatorHandler::triggerAllWindows`
  delegates VERBATIM to `WindowBasedOperatorHandler::triggerAllWindows`
  (`AggregationOperatorHandler.cpp:265-269`) when `numberOfPartitions==1` (default), and
  `triggerSlices` routes through the shared `emitChunk` with `INITIAL`/`lastChunk=true`/empty
  `inputMaps` (`:115-126`), which is the byte-identical emit (the 2c0 baseline).

## Q5 — Ownership / lifetime at P>1 (L8)

**SAFE — leak-total, no double-free, no UAF.**

- The freshly-materialized partition maps (`pending.maps`) are `std::move`d into `emitChunk`
  (`AggregationOperatorHandler.cpp:367-377`) → `registerChunk` `inputs` (`:201-214`), so the handler
  registry OWNS them (`ChunkMaps{inputs, finalAccumulator}`, hpp:162-166).
- `pending.ptrs` (raw `HashMap*` into those maps) stay valid after the move: a `unique_ptr` move
  transfers ownership of the pointer only — the pointed-to `HashMap` objects do not move, so the raw
  pointers copied into `EmittedAggregationWindow.hashMaps[]` (`:248 placement-new`,
  hpp:66-68) keep pointing at the registry-owned maps. C++ argument-eval order is irrelevant here:
  `ptrs` and `maps` are distinct members and moving the `maps` vector does not relocate the
  HashMaps.
- `EmittedAggregationWindow` carries only raw ptrs + the `(seq,chunk)` key and is
  `static_assert`'d trivially destructible (hpp:82-85), so the recycle-only unpooled buffer dropping
  it on any path frees no heap.
- Freed exactly once: the async probe combines the input maps into `finalHashMapPtr`
  (`AggregationProbePhysicalOperator.cpp:95-130`) then calls `releaseChunk(seq, chunk)`
  (`:164-179`) which erases the registry entry (`AggregationOperatorHandler.cpp:225-234`). Any
  chunk whose probe never ran is swept by `stop()` (`:236-254`). `registerChunk`'s
  `INVARIANT(inserted, ...)` (`:218`) guarantees keys are unique, so an entry is freed once.
- No path frees a map the probe still reads: the registry holds the maps until the probe ITSELF
  signals drain via `releaseChunk` (after the combine loop). The `stop()` sweep is a teardown-only
  safety net; per Phase-2 design (G3) it runs at engine teardown, after the probe pipeline is no
  longer scheduled.

## Q6 — Exception safety mid-window

**SAFE.** A throw mid-window leaves claimed slices consistent and leaks neither a reservation nor a
half-registered chunk:

- `emitChunk` allocates the buffer BEFORE any registry insert (`:152-166`), so
  `CannotAllocateBuffer` (`:159-162`) throws BEFORE `registerChunk` — no orphan registry entry.
- If `emitBuffer` (`:243`) or anything after `registerChunk` threw, the chunk would be in the
  registry but undrained; `stop()` sweeps it (leak-total). No double-free (single-shot erase).
- Claimed slices on a throw stay `emittedKeys`-marked. That is harmless: an emitted slice is never
  re-spilled (Q1), and GC / `deleteState` clean `emittedKeys` regardless of whether the slice was
  ever drained. The partitioned path takes NO `spillInProgress` reservation (Q2), so there is no
  reservation to leak (unlike the unspill path, which is correctly guarded by `ReservationGuard`).
- `streamWindowPartition`/`materializeResidentPartition` allocate only stack-local fresh maps held
  in local `unique_ptr` vectors; a throw mid-build unwinds them cleanly (RAII). `pending.maps` /
  `nonEmptyPartitions` are stack RAII (`triggerAllWindows` `:288-294`), freed on unwind.
- The buffer-exhaustion guard in `materializeResidentPartition` is `INVARIANT` (terminate, not
  throw) at `SpillableAggregationSlice.cpp:268` — a deliberate constrained-device contract choice,
  consistent with `replayEntriesInto` (`:68`); it does not leave half-claimed state because it
  aborts the process.

---

## Ranked concurrency/lifetime concerns

**None Critical. None High.** The spill-vs-emit UAF surface is closed.

### Low (observations — no fix required)

- **L-1 (robustness, not a bug):** `streamWindowPartition`'s spilled branch and
  `triggerAllWindows`'s accumulator build both `dynamic_cast<ChainedHashMap*>(map.get())` without a
  null check (`AggregationOperatorHandler.cpp:308`) before
  `createNewMapWithSameConfiguration(*...)`. A non-ChainedHashMap map would be UB, but every map
  produced by `materializeResidentPartition`/`streamEmitByPartition`/`deserialize` is a
  `ChainedHashMap`, and this mirrors the pre-existing pattern in `triggerSlices` (`:109`). Safe in
  practice; an `INVARIANT` would document the contract but is not required.

- **L-2 (defense-in-depth):** `streamWindowPartition` documents (and relies on) the precondition that
  `claimWindowForPartitionedEmit` ran first; it has no runtime assertion that the slice is in
  `emittedKeys`. The handler is the sole caller and always claims first
  (`triggerAllWindows:283` before `:296-298`), so this holds. A debug-only emitted-membership
  assertion in `streamWindowPartition` would catch a future misuse, but is optional.

- **L-3 (statistics only):** a claimed-but-streaming slice is counted by a concurrent
  `residentBytes()` (Q2). This can transiently overcount resident footprint during the terminal
  flush (the spiller may briefly think it is over-soft and try to pick — and correctly find no
  eligible slice since all are emitted). Purely a metrics/effort artifact, not a correctness or
  memory-bound issue; the terminal flush is the end-of-query path.

---

## Evidence summary (claim → spiller fence chain)

```
claimWindowForPartitionedEmit (cpp:327)
  → claimSliceForEmit (cpp:245) [under evictionMutex]
      wait spillerCv on !spillInProgress.contains(end)   (cpp:252-256)
      emittedKeys.insert(end) BEFORE unlock               (cpp:259)   ← load-bearing
streamWindowPartition (cpp:347) [OUTSIDE evictionMutex, slices claimed]
  resident → materializeResidentPartition (read-only, fresh maps)     (slice.cpp:230)
  spilled  → streamEmitByPartition (disk read, fresh maps, stays spilled) (slice.cpp:187)
spiller mutators both reject emitted:
  pickAndReserveColdSlice: emittedKeys.contains(end)→continue          (cpp:754)
  forceSpill: throws on emitted                                        (cpp:656)
ownership: maps → registerChunk inputs (registry owns)                 (cpp:201)
  freed once: probe releaseChunk after combine                         (probe.cpp:176)
  teardown net: stop() sweep                                           (cpp:241)
EmittedAggregationWindow trivially destructible (raw ptrs only)        (hpp:82)
```
