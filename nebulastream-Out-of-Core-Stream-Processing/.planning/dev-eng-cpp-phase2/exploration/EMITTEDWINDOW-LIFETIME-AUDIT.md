# EmittedAggregationWindow Lifetime Totality Audit

> Read-only pre-implementation safety audit for Phase 2 / step 2c (LOCKED decision L8).
> Source: `everything-claude-code:architect`, persisted by orchestrator. Date 2026-05-24.

## VERDICT: RED → with a precise YELLOW remediation

**RED on the literal LOCKED design as written.** The premise that the change "rides" an existing total destruction mechanism is **false**. There is **no** mechanism today that runs `~EmittedAggregationWindow()`. The object is destructed *partially and by hand*: the probe cleanup lambda calls **only** `emittedAggregationWindow->finalHashMap.reset()` (`AggregationProbePhysicalOperator.cpp:167`); it does **not** invoke the struct destructor. Today that is *accidentally sufficient* because `finalHashMap` is the struct's only non-trivial member and `reset()` happens to free it. The moment you add `std::vector<std::unique_ptr<HashMap>> ownedPartitionMaps`, that manual partial-cleanup no longer covers all members, and — critically — the buffer is a recycle-only segment whose reclamation path (`BufferControlBlock::release` → `recycleCallback`) **never calls a C++ destructor on placement-new'd content**. So on *every* non-drain termination path the vector of `unique_ptr<HashMap>` leaks, and even on the normal path it leaks unless the lambda is extended.

**The remediation is well-defined and local (this is why it is also "YELLOW-fixable"):** replace the hand-rolled `finalHashMap.reset()` with a real, idempotent, RAII-guarded `->~EmittedAggregationWindow()` invoked once per emitted buffer, AND close the non-drain termination paths (b–g) where the probe `open()` never runs at all. The partition-map ownership must be made destructor-total, not lambda-total.

One framing correction to the brief: the emit buffer is **`getUnpooledBuffer`**, not a pooled `TupleBuffer` (`AggregationOperatorHandler.cpp:120`). This does not change the verdict — unpooled segments are *also* recycle-only (`UnpooledChunksManager.cpp:155-178` deallocates the chunk; no content dtor) — but it is the accurate mechanism.

---

## Construction-site table

| # | Type | file:line | Buffer | When `ownedPartitionMaps` would be populated |
|---|------|-----------|--------|-----------|
| 1 | `EmittedAggregationWindow` | `AggregationOperatorHandler.cpp:144` (placement-new into `tupleBuffer.getAvailableMemoryArea().data()`) | **Unpooled** buffer from `getUnpooledBuffer(neededBufferSize)` (`:120`), sized `sizeof(EmittedAggregationWindow) + allHashMaps.size()*sizeof(HashMap*)` (`:119`) | At emit time, in the handler, **before** `emitBuffer` (`:148`) — i.e. before the async gap |
| 2 | `EmittedHJWindowTrigger` (sibling, NOT changed) | `HJOperatorHandler.cpp:164` | Unpooled (`:145`) | n/a — trivially destructible, no owned heap |
| 3 | `EmittedNLJWindowTrigger` (sibling, NOT changed) | `NLJOperatorHandler.cpp:87` | placement-new into buffer | n/a for this audit |

Only construction site #1 is in scope. Grep for `EmittedAggregationWindow(` confirms exactly one constructor and one placement-new across the whole repo.

---

## Member lifetime table (today)

| Member (`AggregationOperatorHandler.hpp`) | Alloc site | Owner | Destruction site | Crosses async gap? |
|---|---|---|---|---|
| `WindowInfo windowInfo` (:50) | trivial, inline in buffer | the placement-new'd object | trivially destructible (no-op) | yes, but no obligation |
| `HashMap* finalHashMapPtr` (:51) | raw alias of `finalHashMap.get()` set in ctor (:44) | non-owning | trivial (no-op) | yes, no obligation |
| `std::unique_ptr<HashMap> finalHashMap` (:52) | created at **emit time** in `triggerSlices` via `ChainedHashMap::createNewMapWithSameConfiguration` (`AggregationOperatorHandler.cpp:108`), moved into the object (`:144`) | the placement-new'd object | **manually** via `finalHashMap.reset()` in probe lambda (`AggregationProbePhysicalOperator.cpp:167`) — NOT via struct dtor | **YES — created in handler, freed in probe `open()`. Already crosses the gap.** |
| `uint64_t numberOfHashMaps` (:53) | trivial, set in ctor (:42) | object | trivial (no-op) | yes, no obligation |
| `HashMap** hashMaps` (:54) | `this + 1`, set in ctor (:46-47); pointer array copied into the tail of the same buffer | non-owning; points at **slice-owned** maps today (`getHashMapPtr`, `:97`) | trivial (no-op); pointees freed by base GC after watermark advance | yes, no obligation (pointees outlive via `emittedKeys`) |

**Pivotal sub-question (step 2):** `finalHashMap` **is** created at EMIT time (handler, `:108/:144`) and freed at DRAIN time (probe, `:167`). So it **does** cross the async gap — there IS an existing precedent for handler-created heap state freed after async drain. But that precedent is a *manual single-member `reset()`*, not a destructor, and provides **no coverage on any path where `open()` does not run** (paths b–g). `ownedPartitionMaps` has the same cross-gap lifetime shape, so the *model* is sound in the happy path; the danger is that the *implementation* (hand-reset, drain-only) does not generalize to a second owned member and was never total to begin with.

---

## The destruction mechanism (exact)

1. **Today's "destructor":** `AggregationProbePhysicalOperator.cpp:160-169` — a `nautilus::invoke` of a host lambda whose entire body is `emittedAggregationWindow->finalHashMap.reset();`. This is **not** `->~EmittedAggregationWindow()`. The `std::vector`/`unique_ptr` aggregate destructor is never called. (Harmless *today* only because every other member is trivially destructible.)

2. **Does buffer recycling run any content dtor? NO.** Trace:
   - `TupleBuffer::~TupleBuffer` / `release()` → `BufferControlBlock::release()` (`TupleBufferImpl.cpp:180`).
   - At refcount 0 (`:193`): releases children (`:195-199`), then calls `recycleCallback(owner, recycler.get())` (`:208`) and returns.
   - For **unpooled** buffers (our case) the callback is the lambda in `UnpooledChunksManager.cpp:155-178`: decrements `activeMemorySegments`, sets `size = 0`, and (when the chunk empties) `deallocate`s raw memory. **No placement-new'd content destructor is ever invoked.**
   - `MemorySegment::~MemorySegment` (`TupleBufferImpl.cpp:68-95`) only destroys the *control block*, not the data region.
   - There is **no** registration point on `TupleBuffer`/`BufferControlBlock` for a user "content destructor." The only callback (`recycleCallback`, `TupleBufferImpl.hpp:136`) is owned by the buffer manager for memory reclamation; the handler cannot supply one.

   → **The buffer reclamation path can never clean up `EmittedAggregationWindow`.** Cleanup is 100% the responsibility of whoever processes the buffer (the probe). This is the crux of the RED verdict.

3. **Codebase precedent that the right fix exists:** `HJBuildPhysicalOperator.cpp:83` calls `pagedVectorMemArea->~PagedVector()` explicitly on placement-new'd content (paired with `new (...) PagedVector()` at `:134`). The project already uses the "explicit placement-dtor on buffer content" idiom — the aggregation path just doesn't.

---

## The async emit → drain path (hops + thread boundaries)

1. `AggregationOperatorHandler::triggerSlices` placement-news the object (`:144`) and calls `pipelineCtx->emitBuffer(tupleBuffer)` (`:148`). Reached on terminal flush via `WindowBasedOperatorHandler::triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91-110`, one window at a time after `ensureWindowSlicesResident`), driven from the build pipeline `stop`; and on steady-state via `checkAndTriggerWindows` (`:73-89`).
2. `PipelineExecutionContext::emitBuffer` → `DefaultPEC::emitBuffer` (`QueryEngine.cpp:261-265`) → the `handler` lambda (`:496-508`) → per successor `pool.emitWork(...)` (`:506`).
3. `ThreadPool::emitWork` (`:313-348`): increments `node->pendingTasks` (`:320`), builds a `WorkTask` holding the `TupleBuffer buf` by value/refcount (`Task.hpp:135`), enqueues it.
4. A (possibly different) worker thread dequeues (`:753`) → `handleTask` (`:755`) → `WorkerThread::operator()(WorkTask&)` (`:478-534`) → `pipeline->stage->execute(task.buf, pec)` (`:525`) → `AggregationProbePhysicalOperator::open` drains and runs the cleanup lambda (`:160-169`).
5. `handleTask` (`Task.hpp:212-233`): wraps execution in `cpptrace::try_catch`; on exception calls `failTask`; **always** calls `completeTask`, then the `WorkTask` (and its `buf`) is destroyed at scope exit → refcount drop → recycle.

**Thread boundary:** between hop 3 (handler thread = build-pipeline worker running `stop`/`triggerSlices`) and hop 4 (probe worker). The object is constructed on one thread, destructed on another. The only thing keeping the buffer alive across the gap is the `TupleBuffer` refcount held by `WorkTask::buf`.

---

## Totality table: termination paths a–g

| Path | Cleanup runs? | Today (`finalHashMap` only) | After change (`+ ownedPartitionMaps`) | Evidence |
|---|---|---|---|---|
| **(a) Normal drain** | Partial-by-hand: `finalHashMap.reset()` runs **once**; struct dtor does **not** run | **OK** (only member is `finalHashMap`; `hashMaps` pointees slice-owned) | **LEAK of `ownedPartitionMaps`** unless lambda extended; even then brittle | lambda `AggregationProbePhysicalOperator.cpp:167`; recycle no-op `TupleBufferImpl.cpp:208` |
| **(b) Multi-chunk (P chunks, our case)** | Each chunk = own buffer = own `WorkTask` = own `open()` → P partial-cleanups, one per chunk, IF every chunk drained. No cross-chunk double-free (disjoint vectors, `hash%P`) | n/a (today 1 chunk: `INITIAL`,`lastChunk=true`, `:134-135`) | Per-chunk free correct in principle but inherits (a)'s leak; ordering hazard: GC gating protects the *slice*, not the vectors — vectors protected only by each chunk's buffer refcount, fine **only if each chunk is drained** | per-chunk independent `WorkTask` `Task.hpp:135` |
| **(c) Exception during `open()` drain** | **SKIPPED** — cleanup is a **bare call at end of `open()`** (`:160-169`), not scope-guarded; exception in `:51-157` skips it; `handleTask` try_catch can't see the object | **LEAK of `finalHashMap`** (pre-existing latent, 1 map) | **LEAK of `finalHashMap` + entire `ownedPartitionMaps`** (W maps + buffers) per failed chunk | bare call `:160`; catch `Task.hpp:214-230`; recycle no-op |
| **(d) Query failure elsewhere, buffers queued/undrained** | **SKIPPED** — if pipeline torn down, `task.pipeline.lock()` fails (`QueryEngine.cpp:488`) → returns without executing → `buf` dropped → recycle → no dtor | LEAK of `finalHashMap` | LEAK of `finalHashMap` + `ownedPartitionMaps` | `:488`, `:530-533` |
| **(e) Early stop / cancellation, buffers in flight** | **SKIPPED** for any undrained chunk; `stop()` enqueues `StopQueryTask` (`:789-793`); tasks behind it during termination won't run | LEAK of `finalHashMap` (in-flight) | LEAK of `finalHashMap` + `ownedPartitionMaps` (in-flight) | `:792`; (f) below |
| **(f) Worker/engine teardown ordering** | **SKIPPED** — on shutdown `WorkerThread::operator()` checks `terminating` first and **returns without executing the pipeline** (`:481-485`) → `buf` dropped → recycle → no dtor. `StopPipelineTask` emit lambda explicitly **drops** buffers during termination (`:633-637`). Undrained emitted buffers discarded, not drained | **LEAK** of `finalHashMap` per undrained in-flight buffer | **LEAK** of `finalHashMap` + `ownedPartitionMaps` per undrained in-flight buffer — **dominant leak surface**: terminal flush emits a *burst* of P-per-window chunks right as the build pipeline stops | terminating skip `:481-485`; drop `:633-637`; drain-on-shutdown `:762-764` |
| **(g) Backpressure / pool-exhaustion abort (rc=141) mid-emit** | **SKIPPED / partial** — if `getUnpooledBuffer` returns nullopt, `triggerSlices` throws `CannotAllocateBuffer` (`:121-124`) **before** placement-new — no object, no leak (locals freed by unwinding **iff** partition maps are stack locals until placement-new). Later-chunk abort follows (e)/(f) | No leak at throw site | Same — **provided** partition maps are stack locals (RAII) up to placement-new | throw before placement-new `:121-124` vs `:144` |

**Summary:** the only path where cleanup runs is **(a)/(b) full drain**, and even there as a *manual partial member-reset*, not a destructor. Paths **(c), (d), (e), (f)** all skip cleanup entirely. Today each skip leaks one `unique_ptr<HashMap>` (`finalHashMap`) — a tolerated small leak. After the change each skip additionally leaks a `std::vector<std::unique_ptr<HashMap>>` of W maps **and** their backing TupleBuffers, **precisely during the terminal-flush burst (path f) which is the normal end-of-query shutdown** — so the leak is the **common case** for this feature, not an edge case.

---

## Today vs after-change delta (the new leak surface)

- No *brand-new obligation type* — `finalHashMap` already establishes "handler-allocated heap freed after async drain," so the lifetime model is a widening of an existing pattern. **But** the existing pattern is itself **non-total** (leaks on c/d/e/f). The change **widens an already-leaking gap from "1 small map, tolerated" to "W maps + W buffers per chunk on the normal shutdown path"** — turning a latent nuisance into a real OOM / rc=137 regression on the constrained-device runs this thesis measures.
- **Aliasing widening:** today `hashMaps[]` points at **slice-owned** memory protected by `emittedKeys`/GC. After the change it points **into `ownedPartitionMaps`** owned by the object itself — removing the GC safety net and concentrating the pointees' lifetime on the buffer refcount, which is **not** coupled to any content destructor.

---

## Double-free / aliasing checks (step 7)

1. **Probe combine loop never frees through `hashMaps[]`.** Confirmed: `AggregationProbePhysicalOperator.cpp:75-130` only *reads* `hashMapRefs[curHashMap]`; the only free is `finalHashMap.reset()` (`:167`). So aliasing `ownedPartitionMaps` causes **no double-free via the combine loop** — provided cleanup frees the vector exactly once.
2. **Will the placement-new'd `std::vector`'s own destructor run? NO** — not today, not under the design as written. A `std::vector`/`unique_ptr` member in placement-new'd buffer memory is destroyed **only** via an explicit `->~EmittedAggregationWindow()`. The current `finalHashMap.reset()` (`:167`) would leave `ownedPartitionMaps` un-destructed (vector heap + each map leaked). The correct, total form is `->~EmittedAggregationWindow()`.
3. **Cross-chunk double-free:** none — chunk p's `ownedPartitionMaps` is a distinct vector; chunks share no `unique_ptr`. Safe **iff** per-chunk cleanup runs at most once (need a single-shot guard for the exception path).

---

## Required implementation guardrails (to make it total)

1. **Replace the hand-reset with a real, single-shot destructor call.** At `AggregationProbePhysicalOperator.cpp:160-169`, change the invoked host-lambda body from `finalHashMap.reset()` to `emittedAggregationWindow->~EmittedAggregationWindow()`. Member-count-agnostic (covers `finalHashMap`, `ownedPartitionMaps`, anything added later). Same idiom as `HJBuildPhysicalOperator.cpp:83` (`->~PagedVector()`).

2. **Make the destructor call exception-safe (closes path c).** The cleanup at `:160` is a bare statement after the combine/lower loops; an exception in `:51-157` skips it. Wrap `open()` from where the object pointer is obtained (`:63`) in a host-side RAII scope guard whose destructor `nautilus::invoke`s the dtor, so it runs on **any** exit. (`handleTask`'s try_catch cannot see the object, so the guard must live in `open()`.)

3. **Close the non-drain paths (d/e/f) — the hard part.** Buffer recycling never runs the dtor (`TupleBufferImpl.cpp:208`); undrained buffers are dropped on teardown (`QueryEngine.cpp:481-485`, `:633-637`, `:488`). No hook runs content cleanup on these drops. Options, preferred order:
   - **(Preferred) Prove terminal-flush emitted buffers cannot be dropped undrained.** Verify (runtime, see unknowns) that `pendingTasks` gating (`QueryEngine.cpp:320` inc, `:116` dec) + `PendingPipelineStopTask` (`:578-620`, re-queues while `pendingTasks > 0`) **forces every emitted aggregation chunk to drain before the probe pipeline stops.** If so, only path (c) remains, closed by guardrail 2. NOTE: the join N²-chunk "precedent" proves *ordering* only — join structs are trivially destructible, so the join never *needed* a dtor to run on drop; aggregation does.
   - **(Fallback, structurally safest — removes RED) Move partition-map ownership OFF the recycle-only buffer.** Home the maps in a handler-side registry keyed by `(sequenceNumber, chunkNumber)`, freed when the probe acknowledges drain (per-chunk, to preserve the memory bound) **and** swept by the currently-empty `WindowBasedOperatorHandler::stop` (`WindowBasedOperatorHandler.cpp:50`) as a teardown safety net for anything undrained. The buffer then carries only raw `HashMap*` (like `EmittedHJWindowTrigger`), restoring trivial destructibility and removing the dtor obligation from the buffer path entirely. CAVEAT: freeing only at `stop()` would hold the whole band → defeats the memory bound; the per-chunk drain-ack free is REQUIRED, `stop()` is only the net.

4. **rc=141 / abort-before-placement-new safety (path g).** Build the W partition maps as **stack-local `std::vector<std::unique_ptr<HashMap>>`** in `triggerSlices` and only `std::move` them into the placement-new'd object at `:144` — exactly as `finalHashMap` is a local at `:87` moved in at `:144`. Any throw between deserialization and placement-new then unwinds locals safely.

5. **Single-shot dtor guard (path b correctness).** Ensure the dtor cannot run twice for one chunk. Use one mechanism only (a scope guard running the dtor exactly once on `open()` exit) and delete the standalone `:160-169` invoke.

---

## Residual unknowns (only a runtime test can settle)

1. **Does `pendingTasks` / `PendingPipelineStopTask` gating guarantee every terminal-flush aggregation chunk drains before the probe pipeline stops?** Static reading shows the *intent* (`QueryEngine.cpp:578-620`, `:320`/`:116`) but cannot prove no race where an emitted buffer is still queued when the probe pipeline's stop finalizes, nor that hard-stop/system-shutdown (`:633-637`) can't fire mid-flight. **Test:** ASAN+LSAN systest, spilled aggregation P>1, under (a) clean end-of-stream flush, (b) `QueryEngine::stop()` mid-terminal-flush, (c) worker-shutdown with queued chunks; instrument the `EmittedAggregationWindow` dtor with a counter and assert `dtors == chunksEmitted`, zero leaks.
2. **Path (c) under forced exception:** inject a throw in the probe combine loop (`:80-129`); with guardrail 2 confirm the dtor still runs exactly once; without it, confirm the leak reproduces (proves the guard is load-bearing).
3. **`HashMap` page return:** confirm (LSAN) `~HashMap`/`~ChainedHashMap` returns its pages to the pool on normal drain — a `ChainedHashMap` dtor question outside the audited files; follow-up read if guardrail 1 is taken.

---

## Bottom line for the implementer

The LOCKED design's sentence "the answer maps onto the existing `EmittedAggregationWindow` ownership model … freed in the host cleanup lambda" is only half-true: the existing model frees **one** member by hand and is **not** destructor-total and **not** drop-safe. Before writing 2c:
- the cleanup at `AggregationProbePhysicalOperator.cpp:167` must become a real `->~EmittedAggregationWindow()` under an exception-safe scope guard (guardrails 1, 2, 5),
- the partition maps must be stack-RAII until placement-new (guardrail 4), and
- you must either **prove by ASAN test** that no undrained terminal-flush chunk is ever dropped (guardrail 3-preferred) **or move ownership off the recycle-only buffer** into a handler registry freed per-chunk on drain-ack + swept in `WindowBasedOperatorHandler::stop` (guardrail 3-fallback).

Without guardrail 3 resolved, the feature leaks W maps + W buffers per chunk **on every normal query shutdown** — the exact OOM this thesis exists to prevent.
