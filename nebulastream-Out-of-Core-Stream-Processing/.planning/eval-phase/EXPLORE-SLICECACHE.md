# EXPLORE — SliceCache invalidation feasibility (host-side, no JIT change)

**Question gated:** Can a per-worker `SliceCache` entry for a spilled slice be invalidated from
HOST C++ (next access misses → `getSlicesOrCreate` → unspills) **without modifying Nautilus / JIT
code**? This decides whether OOC spill of windows stays inside the proposal's "no code-gen edits"
scope.

**Repo / branch:** `nebulastream-Out-of-Core-Stream-Processing` @ `feat/ooc-phase1-spill-skeleton`
**Method:** read-only source inspection; file:line + quotes below. No build/run performed.

---

## TL;DR verdict

- **What is cached:** a plain C++ struct `SliceCacheEntry { sliceStart, sliceEnd, const std::byte*
  dataStructure }` — i.e. the **raw `HashMap*`** (as `const std::byte*`), keyed by the slice's
  `[sliceStart, sliceEnd)` time range. **No generation/epoch/residency field.**
- **Who owns it:** one `SliceCache` instance **per pipeline-copy** (held by
  `DefaultTimeBasedSliceStoreRef::sliceCache`), backed by **one contiguous buffer partitioned
  per worker thread** (`[Thread0 entries]…[ThreadN entries][replIdx…]`). 10 entries/thread default.
- **Hit-path is JIT (Nautilus `val<>`):** `SliceCacheSecondChance::getDataStructureRef` /
  `searchInCache` are fully traced/compiled. A HIT returns the cached pointer and **never calls back
  into host C++**; only a MISS does (`nautilus::invoke(defaultTimeBasedSliceStoreRefCacheMissProxy)`).
- **Can it be invalidated host-side?** **Not today** (no API, store holds no `SliceCache*`), **but it
  is achievable IN-SCOPE** with a small host-only addition, because the store already owns the raw
  cache *memory buffers* and the entry is a POD whose layout host code already constructs. Writing
  `sliceEnd = sliceStart = 0` into the matching entry forces the next lookup to MISS — **no JIT edit**.
  The blocker is *concurrency*, not scope (see §4).
- **Natural eviction + quiescence:** for **quiescent (cold) windows the existing design is already
  safe without any invalidation** — the build hot-path provably never touches a spilled slice. So for
  the in-scope target (spill QUIESCENT windows) the answer is: **option (iii) is sufficient and
  IN-SCOPE; no cache invalidation is needed at all.** Natural eviction *alone* is **not** a sound
  guarantee and should not be relied on.
- **Depth case** (spill a window while workers may still write it): a stale cache HIT returns a freed
  `HashMap*` and **bypasses every host check** → use-after-free. Preventing it *robustly* needs a
  residency/generation check **on the HIT path = generated code = OUT-OF-SCOPE**. Host-side
  invalidation can help but cannot be made race-free against a concurrently-reading worker without
  either marshalling onto the owner thread or a generated-side check.

---

## 1. SliceCache: structure, ownership, lifetime, hit-path

### Entry layout (what is cached) — raw pointer, no epoch
`nes-physical-operators/include/SliceStore/SliceCache/SliceCache.hpp:33-48`:
```cpp
struct SliceCacheEntry {
    using DataStructure = const std::byte*;
    ...
    Timestamp::Underlying sliceStart;
    Timestamp::Underlying sliceEnd;
    DataStructure dataStructure;   // <-- the raw HashMap* (as const std::byte*)
};
```
`SliceCacheSecondChance` adds one `bool secondChanceBit`
(`SliceCacheSecondChance.hpp:28-32`). Both are asserted **trivially destructible**
(`SliceCache.hpp:52`, `SliceCacheSecondChance.hpp:34`) — deliberately POD "because we use the structs
purely in nautilus" (`SliceCache.hpp:50-51`). **There is no generation, epoch, version, or residency
bit on the entry.** (Confirmed by grep: the only `version` in `SliceStore` is the on-disk serializer
format, `SliceSerializer.cpp:122`.)

The cached `dataStructure` is exactly the pointer that `spill()` frees and `unspill()` re-creates:
the extractor stores `getHashMapPtrOrCreate(...)` 's result
(`LowerToPhysicalWindowedAggregation.cpp:261-262`) into `entryToReplace->dataStructure`
(`DefaultTimeBasedSliceStoreRef.cpp:65`). `spill()` does `hashMaps[pos].reset()`
(`SpillableAggregationSlice.cpp:112`); `unspill()` rebuilds via `SliceSerializer::deserialize` at a
**new address** (`SpillableAggregationSlice.cpp:133`). **Prior findings confirmed verbatim.**

### Lookup = hit vs miss (range match on event timestamp)
`SliceCacheSecondChance::searchInCache` (`SliceCacheSecondChance.cpp:51-62`):
```cpp
if (sliceStart <= timestamp && timestamp < sliceEnd) { return {entry, true}; }  // HIT
```
A hit just sets the second-chance bit and returns `dataStructure`
(`SliceCacheSecondChance.cpp:84-88`). **The key is the event-time being inside a cached slice's
range** — not a SliceEnd handle. Empty entries have `sliceStart==sliceEnd==0` so nothing matches
(test confirms: `SliceCacheTest.cpp:74`).

### Ownership / count — per-pipeline instance, per-worker partition; store holds NO cache object
- `DefaultTimeBasedSliceStoreRef` holds **one** `std::unique_ptr<SliceCache> sliceCache`
  (`DefaultTimeBasedSliceStoreRef.hpp:84`). It is **cloned per pipeline copy**
  (`DefaultTimeBasedSliceStoreRef.cpp:88`, driven by `WindowBuildPhysicalOperator.cpp:131-132`:
  "The SliceStoreRef is shared across pipeline copies — it manages per-pipeline caches internally").
- Inside that one instance, the buffer is **partitioned per worker thread**: layout
  `[Thread0 entries]…[ThreadN entries]` (`SliceCache.hpp:70`), and each worker indexes only its own
  partition: `threadLocalStart = start + workerThreadId * numberOfEntries`
  (`SliceCacheSecondChance.cpp:77-79`). So effectively **one cache region per (pipeline × worker
  thread)**. Default 10 entries/thread (`SliceCacheConfiguration.hpp:39`).
- The **store** (`DefaultTimeBasedSliceStore`) does **not** own any `SliceCache` object. It owns only
  the **raw backing memory**: `pipelineIdToSliceCacheStarts : map<PipelineId, unique_ptr<TupleBuffer>>`
  (`DefaultTimeBasedSliceStore.hpp:84`), allocated once per pipeline in `allocateSpaceForSliceCache`
  (`DefaultTimeBasedSliceStore.cpp:291-308`). There is **no registry of `SliceCache*` instances** and
  no list of worker contexts in the store.

### Hit-path is JIT; only the miss path is host
`getDataStructureRef` takes/returns `nautilus::val<>` and is traced
(`SliceCacheSecondChance.cpp:65-113`). On HIT it returns the cached pointer directly
(`:84-88`) — **no host call**. On MISS it invokes the host proxy:
`DefaultTimeBasedSliceStoreRef.cpp:99-112` →
`nautilus::invoke(defaultTimeBasedSliceStoreRefCacheMissProxy, …)`. The proxy
(`DefaultTimeBasedSliceStoreRef.cpp:40-66`) is host C++ and routes through
`sliceStore->getSlicesOrCreate(...)` (`:59`) — already overridden by the spillable store. **So any
host-side residency check can only run on a MISS; a HIT is invisible to host code.** This is the crux
of the depth-case scope problem.

---

## 2. Reachability from host (the core question)

**Today: NOT reachable.** From the spillable store or its background spiller thread there is **no
pointer to any `SliceCache` instance** — the store holds only `PipelineId → TupleBuffer` raw memory
(`DefaultTimeBasedSliceStore.hpp:84`), and the operator handler holds no worker/cache registry either.
There is also **no invalidate/clear/evict/reset API** on `SliceCache` (grep confirmed: the only
"evict" in the store is *slice-store spill* eviction, not cache-entry invalidation).

**But it is achievable IN-SCOPE** with a small **host-only** change, and crucially **without touching
generated code**, because:
1. The store already owns the **raw cache buffer** for every pipeline
   (`pipelineIdToSliceCacheStarts`), and the byte layout is fully known
   (`[thread][entry]`, entry stride `sizeof(SliceCacheEntrySecondChance)`).
2. `SliceCacheEntry` is a POD that host code already reads & writes (the miss proxy writes
   `entryToReplace->sliceStart/sliceEnd/dataStructure` in plain C++,
   `DefaultTimeBasedSliceStoreRef.cpp:63-65`).
3. To force a future MISS for a spilled slice, host code only has to find the entry whose
   `[sliceStart,sliceEnd)` equals the spilled slice's range (in each worker partition of each
   pipeline buffer) and **zero its `sliceStart`/`sliceEnd`** (or its `dataStructure`). The next
   `searchInCache` then fails to match → MISS → `getSlicesOrCreate` → (after a small store change)
   unspill. **The eviction write itself is plain host C++ over a `std::byte*` buffer — it does NOT run
   inside generated code.**

What it "would take": (a) a host method on `DefaultTimeBasedSliceStore` (or a thin `SliceCacheRegistry`)
that, given a `SliceEnd`/range, walks `pipelineIdToSliceCacheStarts` and clears matching entries; the
spiller calls it right after a successful `spill()`. No new generated code, no new `nautilus::invoke`
on the read path. **(IN-SCOPE.)** The real cost is concurrency (§4), not scope.

> Caveat: the entry stride must match the concrete cache type (`SliceCacheNone` 1 entry vs
> `SliceCacheSecondChance` 10 + per-thread replIdx tail, `SliceCacheSecondChance.cpp:43-46`). Host
> eviction must know `numberOfEntries`, `numberOfWorkerThreads`, and `sizeof(entry)` — all available
> store-side at setup. This is bookkeeping, not a scope violation.

---

## 3. Existing eviction / natural churn

**Replacement policy:** second-chance / clock, 10 entries per worker by default
(`SliceCacheSecondChance.cpp:90-112`; mirrored reference impl in `SliceCacheTest.cpp:77-119`).
As a worker builds NEWER slices, misses replace clock victims; an old cold slice's entry **will**
eventually be overwritten — but only after enough *distinct* slices in that worker's range churn
through, and second-chance can grant a touched victim one extra pass. With 10 entries this is "soon"
in steady state but **not bounded by anything tied to spill timing**.

**Is "natural eviction + quiescence delay" safe to rely on?** **No — not as a guarantee.** There is no
ordering between "spiller frees the map" and "the worker's clock happens to overwrite that entry." A
late/out-of-range access in the interim would HIT the stale pointer. Relying on natural eviction is a
*probabilistic* mitigation only; do not use it as the correctness argument.

**Existing invalidate/clear API:** none. (No bulk clear at query boundaries either; the buffer is
zero-filled once at allocation, `DefaultTimeBasedSliceStore.cpp:303`, and freed with the pipeline.)

---

## 4. Concurrency

The cache is **per-worker-thread by partition and is written lock-free from generated code** (each
worker mutates only its own partition + second-chance bits + `replacementIndexPtr`,
`SliceCacheSecondChance.cpp:76-106`). There is **no synchronization** on entries.

Spills occur on **two** host contexts: (a) the store-owned **background `jthread` spiller**
(`spillerLoop`, `SpillableTimeBasedSliceStore.cpp:694-737`), and (b) the **worker thread's own**
`getSlicesOrCreate` hard path (`SpillableTimeBasedSliceStore.cpp:171-175` → `evictUntilUnderHard`).

Implication for host-side invalidation:
- If a **background** thread writes a worker's cache entry while that worker is mid-`searchInCache`,
  that is an unsynchronized concurrent read/write on `sliceStart/sliceEnd/dataStructure` → **data race
  / UB** under the current lock-free design. So a naive cross-thread host eviction is **not sound**.
- It is only trivially safe when the eviction runs **on the owning worker thread itself** — e.g.
  case (b), the worker is *inside its own miss/create path* and not concurrently reading. Marshalling
  background-spiller invalidations onto the owner worker (or adding atomics to the entry — which would
  change the generated access type, **OUT-OF-SCOPE**) would be required to make cross-thread
  invalidation race-free.

This is *why* host-side invalidation, although in-scope on the *scope* axis, is hard on the
*correctness* axis for the depth case.

---

## 5. Verdict — ranked options to prevent a stale HIT on a spilled slice

### (iii) Rely on quiescence + NES's no-late-write-to-finished-window assumption — **IN-SCOPE, RECOMMENDED for the quiescent target. RANK #1.**
The current OOC design only spills **COLD** slices: `sliceEnd < lastSeenWatermark`, resident, **not in
`emittedKeys`** (`pickAndReserveColdSlice`, `SpillableTimeBasedSliceStore.cpp:496-534`; forceSpill
guards the same, `:435-450`). For the **build hot path** to produce a cache HIT on such a slice, a
worker must process a record whose **event-time lies in that already-past `[start,end)` range** — i.e.
a record older than a watermark that has already advanced past the window. NES treats that as a late
record for a finished window, and the store **refuses** it: adding a slice to an already-triggered
window trips `INVARIANT(... != EMITTED_TO_PROBE ...)` and terminates
(`DefaultTimeBasedSliceStore.cpp:94-96`); and the extractor explicitly assumes residency
("Build-path slices are always resident", `LowerToPhysicalWindowedAggregation.cpp:258-259`, calling
`getHashMapPtrOrCreate` whose `INVARIANT(resident,…)` would otherwise fire,
`SpillableAggregationSlice.cpp:60`).
**Therefore, for spilling QUIESCENT windows, no cache invalidation is required at all — the build path
provably never re-touches a spilled slice's range.** This is pure host/runtime semantics; **no JIT
edit. IN-SCOPE.**
**Residual risk (must be characterized by experiment, not assumed):** a slice that is *cold but not
yet GC'd* could still be re-entered by `getSlicesOrCreate` returning the existing (spilled) shared_ptr
on a cache MISS (`DefaultTimeBasedSliceStore.cpp:66-69`) — the spillable override does **not** unspill
on the build path (`SpillableTimeBasedSliceStore.cpp:157-185`). That would hit
`getHashMapPtrOrCreate`'s `INVARIANT(resident,…)` and terminate. This is a *crash*, not a UAF, and it
only triggers on a genuinely late record — but it means "quiescent is safe" depends on the engine
truly delivering no in-range records after spill. **→ runtime experiment needed (see below).**

### (i) Host-side invalidate-on-spill (spiller clears the matching cache entry) — **IN-SCOPE on scope, RISKY on concurrency. RANK #2 (defensive backstop).**
Feasible without JIT changes (§2): the store owns the raw buffers and the entry is POD; clearing
`sliceStart/sliceEnd` forces a future MISS. **What's needed:** a host registry/accessor of the
per-pipeline cache buffers (already half-present as `pipelineIdToSliceCacheStarts`) + the cache
geometry, and a `spill()`-completion hook in the spiller. **Thread-safety is the catch (§4):** sound
only when executed on the owning worker (e.g. the worker-path hard-evict, case (b)); a *background*
spiller clearing another worker's partition races the lock-free reader. Usable as a **belt-and-braces
backstop for the quiescent case** (where (iii) already makes a hit unreachable, so the race is never
actually exercised), but **not a stand-alone fix for the depth case.**

### (iv) Lazy free / mark-for-spill, free on a host-reachable path — **PARTIAL, IN-SCOPE-ish. RANK #3.**
Spill could *serialize+mark* without freeing, deferring the actual `hashMaps[pos].reset()` until a
host-reachable boundary (e.g. GC, or the owning worker's next miss). This shrinks the UAF window for
the depth case without a JIT edit, but does **not** reclaim memory promptly (defeating much of the OOC
goal) and still needs (i)/(iii) to be fully safe. A tactical mitigation, not a solution.

### (ii) Generation/epoch or residency flag checked on the cache HIT — **OUT-OF-SCOPE.**
This is the only **robust** fix for the depth case, but the HIT check lives in
`SliceCacheSecondChance::getDataStructureRef`/`searchInCache`, which are **Nautilus `val<>` traced =
generated code** (§1). Adding "if entry stale → treat as miss" there **edits the JIT path → forbidden
by the proposal.** Confirmed: the hit decision is generated, not host.

---

## Bottom line for the thesis scope decision

- **(a) Quiescent-window spilling: IN-SCOPE.** Option **(iii)** alone is sufficient — the engine's
  watermark/finished-window semantics make a build-path cache HIT on a spilled (cold, non-emitted)
  slice unreachable, so no JIT change and no cache invalidation are required. Optionally add the
  host-side invalidate-on-spill (i) as a cheap defensive backstop (safe because the race is never
  exercised in this regime). **This is the recommended thesis scope.**
- **(b) Depth case (spill a window workers may still write): NOT cleanly reachable in-scope.** A stale
  cache HIT returns a freed `HashMap*` and bypasses all host checks; the only robust guard is a
  residency/epoch test on the generated HIT path **(ii) = JIT edit = OUT-OF-SCOPE**. Host-only
  mitigations ((i)/(iv)) cannot be made race-free against a concurrently-reading worker without either
  marshalling onto the owner thread or making entries atomic (which itself changes the generated
  access). **Recommend explicitly declaring the depth case out of scope** (consistent with the
  proposal's no-code-gen constraint).

## Where a runtime experiment is needed
1. **Confirm the no-in-range-access-after-spill assumption** for quiescent spill: drive late/out-of-order
   records straddling a just-spilled cold window and verify the engine never produces a build-path
   access into it (expect either no access, or the `INVARIANT(resident,…)` crash — which would prove
   the assumption is the real safety boundary and quantify the residual risk).
2. **Characterize natural eviction timing** (10-entry clock) vs. spill cadence — only to bound the (i)
   backstop's window; not load-bearing for correctness.
3. If the depth case is ever pursued: a deliberate spill-while-writing stress test to demonstrate the
   stale-HIT UAF and thereby justify why (ii)/JIT would be required.
