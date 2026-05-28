# 2c0 CORE — Handler-Owned Chunk Registry: Current-State Confirmation + Change Map

> READ-ONLY exploration for step 2c0 (DESIGN-BRIEF §5; LOCKED L8/G3 "Option 2").
> Goal of 2c0: move ALL per-chunk hash maps OFF the recycle-only emit buffer into a NEW
> mutex-guarded registry owned by `AggregationOperatorHandler`, keyed `(SequenceNumber, ChunkNumber)`;
> make `EmittedAggregationWindow` trivially destructible; keep P=1 emit BYTE-IDENTICAL.
> Verified against current working tree (branch `main`). Date 2026-05-24.

## VERDICT: audit CONFIRMED at current HEAD. No drift. One sharpening (probe→handler plumbing) below.

Every file:line cited by `DESIGN-BRIEF.md` L8 and `EMITTEDWINDOW-LIFETIME-AUDIT.md` matches the
code in the tree today. The numeric line anchors in the audit are correct to within ±1 (the audit's
`:144` placement-new, `:120` getUnpooledBuffer, `:167` finalHashMap.reset, `WindowBasedOperatorHandler.cpp:50`
empty stop, `TupleBufferImpl.cpp:208` recycle no-content-dtor, `QueryEngine.cpp:481` terminating-skip,
`:633` drop-on-teardown — ALL confirmed). The one item that needs explicit flagging is NOT a drift but a
missing-detail: the probe does NOT currently look up its operator handler in `open()`, so the per-chunk
release call needs a new handler lookup the audit/brief assumed implicitly. See §3 and CHANGE-3.

---

## 1. `AggregationOperatorHandler.hpp` — struct + handler shape (CONFIRMED)

File: `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp`

### Current `EmittedAggregationWindow` (`:39-55`) — quoted verbatim

```cpp
struct EmittedAggregationWindow
{
    EmittedAggregationWindow(const WindowInfo windowInfo, std::unique_ptr<HashMap> finalHashMap, const std::vector<HashMap*>& allHashMaps)
        : windowInfo(windowInfo), finalHashMap(std::move(finalHashMap)), numberOfHashMaps(allHashMaps.size())
    {
        finalHashMapPtr = this->finalHashMap.get();
        /// Copying the hashmap pointers after this object, hence this + 1
        hashMaps = std::bit_cast<HashMap**>(this + 1);
        std::ranges::copy(allHashMaps, std::bit_cast<HashMap**>(hashMaps));
    }

    WindowInfo windowInfo;                     // :50  (trivially destructible: 2x Timestamp)
    HashMap* finalHashMapPtr;                  // :51  raw alias of finalHashMap.get()
    std::unique_ptr<HashMap> finalHashMap;     // :52  *** THE ONLY non-trivial member *** -> must be DROPPED in 2c0
    uint64_t numberOfHashMaps;                 // :53  trivial
    HashMap** hashMaps;                        // :54  raw, = this+1 (pointer array in the buffer tail)
};
```

Key facts confirming the audit:
- **Exactly one non-trivially-destructible member:** `std::unique_ptr<HashMap> finalHashMap` (`:52`).
  Everything else is trivial. This is why today's hand-rolled `finalHashMap.reset()` is "accidentally total."
- **`hashMaps` is `this+1`** (`:46`): the `HashMap*` array is copied into the buffer tail (the
  `getUnpooledBuffer` was sized `sizeof(EmittedAggregationWindow) + allHashMaps.size()*sizeof(HashMap*)`,
  see §2). At P=1 today these pointees are **slice-owned** (returned by `getHashMapPtr`), kept alive by the
  base GC/`emittedKeys` regime — NOT owned by this struct.
- **The struct does NOT carry `(seq, chunk)` today.** It carries only `windowInfo` (`:50`). The
  `(sequenceNumber, chunkNumber)` live on the **TupleBuffer metadata** (set in `triggerSlices` at cpp `:133-134`),
  NOT inside the struct. ⚠️ FLAG vs brief: DESIGN-BRIEF L8 says the `(seq,chunk)` key is "already present for
  chunk metadata" — that is true of the *TupleBuffer*, not of `EmittedAggregationWindow`. For 2c0 the cleanest
  total form is to **store the `(seq,chunk)` key as two trivial members inside the struct** so the probe's
  release invoke reads them straight from the buffer (no extra Nautilus reads of the buffer header). This is a
  design choice for CHANGE-1/CHANGE-3 — see those.

### Target shape (the join precedent — `EmittedHJWindowTrigger`, `HJOperatorHandler.hpp:38-60`)

`EmittedHJWindowTrigger` is the model: members are `WindowInfo windowInfo; uint64_t leftNumberOfHashMaps;
uint64_t rightNumberOfHashMaps; HashMap** leftHashMaps; HashMap** rightHashMaps;` — **all trivially
destructible, raw pointers only.** `EmittedAggregationWindow` must converge to this shape:
`WindowInfo windowInfo; SequenceNumber seq; ChunkNumber chunk; HashMap* finalHashMapPtr;
uint64_t numberOfHashMaps; HashMap** hashMaps;` — drop the `unique_ptr`, keep `finalHashMapPtr` (raw).

### Handler class shape (`:57-83`) — where the registry lives

```cpp
class AggregationOperatorHandler final : public WindowBasedOperatorHandler {
public:
    // ctor (:60-65), getCreateNewSlicesFunction (:67-68)
    std::atomic<bool> setupAlreadyCalled;                                                 // :71
    std::shared_ptr<CreateNewHashMapSliceArgs::NautilusCleanupExec> cleanupStateNautilusFunction; // :73
protected:
    void triggerSlices(...) override;                                                     // :76-78
    folly::Synchronized<RollingAverage<uint64_t>> rollingAverageNumberOfKeys;             // :79
    uint64_t maxNumberOfBuckets;                                                          // :80
    bool spillEnabled;                                                                    // :82
};
```

- **Existing concurrency primitive in this class:** `folly::Synchronized<RollingAverage<uint64_t>>
  rollingAverageNumberOfKeys` (`:79`) — accessed via `.rlock()` / `.wlock()` in cpp `:64,:101`.
  So `folly::Synchronized` is already an established idiom on THIS class (matches project memory
  `nes_cpp_idioms.md`: "folly `Synchronized` sets").
- **No existing per-chunk map / registry exists** on the handler today. There is no map member of any kind.
- **Where `chunkRegistry` naturally lives:** a new `protected` member on `AggregationOperatorHandler`
  (alongside `rollingAverageNumberOfKeys`, `:79`). Recommended:
  `folly::Synchronized<std::map<std::pair<SequenceNumber, ChunkNumber>, ChunkMaps>> chunkRegistry;`
  with `struct ChunkMaps { std::vector<std::unique_ptr<HashMap>> inputs; std::unique_ptr<HashMap> finalAccumulator; };`
  defined either in the .hpp near `EmittedAggregationWindow` or as a private nested type.
  NOTE: at P=1 `inputs` is empty (today's `hashMaps[]` point at slice-owned maps that 2c0 does NOT move —
  see §6); only `finalAccumulator` (today's `finalHashMap`) actually moves into the registry at 2c0.
  The `inputs` vector is the seam for 2c1/2d's re-materialized partition maps. At 2c0 it stays empty and
  the byte-identical property holds.

### Key type viability for the registry key (`NESStrongType`)

`SequenceNumber` / `ChunkNumber` are `NESStrongType<uint64_t,...>` (`Identifiers.hpp:37-38`). `NESStrongType`
has a defaulted `operator<=>` (`NESStrongType.hpp:49`) and `std::hash` (NESIdentifier concept requires it,
`:151`). So `std::pair<SequenceNumber,ChunkNumber>` is usable as BOTH an ordered `std::map` key and a hashed
`std::unordered_map` key. (Brief/audit say `std::map` — fine.)

---

## 2. `AggregationOperatorHandler.cpp::triggerSlices` (CONFIRMED — `:80-157`)

File: `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp`

Per-window loop (`:84`). For each window:

1. **`finalHashMap` creation** — declared as a STACK LOCAL `std::unique_ptr<ChainedHashMap> finalHashMap;`
   (`:87`); created lazily on the first non-empty slice map via
   `ChainedHashMap::createNewMapWithSameConfiguration(*dynamic_cast<ChainedHashMap*>(hashMap))` (`:108`).
   → It is ALREADY a stack local moved in at placement-new (`:144`), satisfying audit guardrail G4 for the
   final map. (G4 for `inputs` is moot at 2c0 — no input maps are materialized.)
2. **`allHashMaps[]` population** (`:88,:104`): `std::vector<HashMap*> allHashMaps;` filled with the raw
   `hashMapSlice->getHashMapPtr(WorkerThreadId(hashMapIdx))` for every slice map with `getNumberOfTuples() > 0`
   (`:95-104`). These are **slice-owned** raw pointers. `totalNumberOfTuples` accumulated at `:105`.
3. **Unpooled buffer sizing + obtain** (`:119-125`):
   - `neededBufferSize = sizeof(EmittedAggregationWindow) + (allHashMaps.size() * sizeof(HashMap*))` (`:119`)
   - `pipelineCtx->getBufferManager()->getUnpooledBuffer(neededBufferSize)` (`:120`)  ← **unpooled**, confirms audit
   - on nullopt: `throw CannotAllocateBuffer(...)` (`:123`) — thrown BEFORE the placement-new (`:144`), so the
     rc=141 path (g) has no constructed object (audit path g confirmed).
4. **Zero-fill** (`:128`): `std::ranges::fill(tupleBuffer.getAvailableMemoryArea(), std::byte{0})`.
5. **Chunk metadata set on the TupleBuffer** (`:132-139`) — THIS is the byte-identical contract for P=1:
   - `setOriginId(outputOriginId)` (`:132`)
   - `setSequenceNumber(windowInfo.sequenceNumber)` (`:133`)
   - `setChunkNumber(ChunkNumber(ChunkNumber::INITIAL))` (`:134`)  ← single chunk
   - `setLastChunk(true)` (`:135`)
   - `setWatermark(windowInfo.windowInfo.windowStart)` (`:136`)
   - `setNumberOfTuples(totalNumberOfTuples)` (`:137`)
   - `setCreationTimestampInMS(...)` (`:138-139`)
6. **Placement-new** (`:143-144`):
   `auto tmp = tupleBuffer.getAvailableMemoryArea();`
   `new (tmp.data()) EmittedAggregationWindow{windowInfo.windowInfo, std::move(finalHashMap), allHashMaps};`
7. **emit** (`:148`): `pipelineCtx->emitBuffer(tupleBuffer);`

Reached from: `WindowBasedOperatorHandler::triggerAllWindows` (terminal flush, one window at a time,
`WindowBasedOperatorHandler.cpp:91-110`) and `checkAndTriggerWindows` (steady state, `:73-89`).

---

## 3. `AggregationProbePhysicalOperator.cpp::open` drain (CONFIRMED — `:51-170`)

File: `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp`

- **Seq/chunk ARE available to the probe at drain time.** `open()` reads them off the record buffer into
  the execution context at the very top: `executionCtx.sequenceNumber = recordBuffer.getSequenceNumber();`
  (`:56`) and `executionCtx.chunkNumber = recordBuffer.getChunkNumber();` (`:57`). They are
  `nautilus::val<SequenceNumber>` / `nautilus::val<ChunkNumber>` (RecordBuffer.hpp:53,58). So the release
  invoke can recover the `(seq,chunk)` key — EITHER from these execution-context vals, OR (preferred, simpler)
  from two new trivial `seq`/`chunk` members stored inside `EmittedAggregationWindow` and read via
  `getMemberRef` exactly like `numberOfHashMaps` is read at `:64-65`.
- **The combine loop** (`:75-130`): reads `hashMapRefs[curHashMap]` (`:77`) for `curHashMap < numberOfHashMaps`
  (`:75`), wraps each as a `ChainedHashMapRef` (`:78`), and `finalHashMap.insertOrUpdateEntry(...)` (`:87-128`)
  into the final map. **It only READS through `hashMaps[]`; the only free is `finalHashMap.reset()` at `:167`**
  (audit double-free check #1 confirmed — combine never frees through `hashMaps[]`).
- **The lowering loop** (`:133-157`): iterates `finalHashMap`, lowers each aggregation state, `executeChild(...)`
  (`:149`), then `cleanup` (`:154`).
- **The host cleanup invoke** (`:160-169`) — quoted:

```cpp
    nautilus::invoke(
        +[](EmittedAggregationWindow* emittedAggregationWindow)
        {
            NES_TRACE(
                "Resetting final hash map of emitted aggregation window start at {} and end at {}",
                emittedAggregationWindow->windowInfo.windowStart,
                emittedAggregationWindow->windowInfo.windowEnd);
            emittedAggregationWindow->finalHashMap.reset();   // :167  <-- THE SITE THAT CHANGES
        },
        aggregationWindowRef);
```

- The `EmittedAggregationWindow*` is recovered at `:63`:
  `static_cast<nautilus::val<EmittedAggregationWindow*>>(recordBuffer.getMemArea())` — so the buffer mem area
  IS the struct. ⚠️ Note: this is a **bare statement at the end of `open()`**, NOT scope-guarded — an exception
  in the combine/lower loops (`:75-157`) skips it (audit path c). Under Option-2 (2c0) this is harmless for the
  buffer (it owns no heap once trivially destructible) but the **registry release would be skipped** on that
  path → the `stop()` sweep (§4) is the safety net for it. Per-chunk leak-window only, swept at teardown.
- **⚠️ MISSING PLUMBING (flag):** `open()` does NOT currently fetch its operator handler. The release call
  needs an `AggregationOperatorHandler*`. The established pattern (used by the BUILD operator) is
  `dynamic_cast<AggregationOperatorHandler*>(RawValueResolver<OperatorHandler*>::getRawValue(
  executionCtx.getGlobalOperatorHandler(operatorHandlerId)))` (`AggregationBuildPhysicalOperator.cpp:53-54`,
  same idiom `HJBuildPhysicalOperator.cpp:58`). The probe HAS `operatorHandlerId` (via
  `WindowProbePhysicalOperator`, `WindowProbePhysicalOperator.hpp:49`), so this is available — but it is NEW
  code the probe does not have today. CHANGE-3 must add it (recover handler, then `nautilus::invoke` a free
  function that calls `handler->releaseChunk(seq, chunk)`).

---

## 4. `WindowBasedOperatorHandler.cpp::stop` + trigger paths (CONFIRMED)

File: `nes-physical-operators/src/WindowBasedOperatorHandler.cpp`

- **`stop()` is currently EMPTY** (`:50-52`):
  `void WindowBasedOperatorHandler::stop(QueryTerminationType, PipelineExecutionContext&) {}`
  This is the correct teardown hook for the registry sweep. It is a base-class virtual
  (`WindowBasedOperatorHandler.hpp:68`, `override` of `OperatorHandler::stop`). ⚠️ DESIGN CHOICE: it is shared
  with joins. The sweep must live where the registry lives, i.e. on `AggregationOperatorHandler`. Two options:
  (a) make `stop` virtual-overridable and override it on `AggregationOperatorHandler` to sweep then call base;
  (b) add a protected virtual hook the base `stop` calls. Project memory (`nes_cpp_idioms.md` Increment C)
  warns: putting agg-specific behavior in the SHARED `WindowBasedOperatorHandler` is high blast-radius — so
  **override `stop` on `AggregationOperatorHandler`** (option a) and clear `chunkRegistry` there. Confirm
  `OperatorHandler::stop` signature allows the override (base sig: `stop(QueryTerminationType, PipelineExecutionContext&)`).
- **`triggerAllWindows`** (`:91-110`): terminal flush. `getAllNonTriggeredSlices()` (`:98`), then per window
  `ensureWindowSlicesResident(slices)` (`:102`) + `triggerSlices(oneWindow, pipelineCtx)` (`:108`). One window
  at a time (paced L1).
- **`checkAndTriggerWindows`** (`:73-89`): steady state. `getTriggerableWindowSlices(newGlobalWatermark)`
  (`:87`) → `triggerSlices(...)` (`:88`).

---

## 5. Threading + lock recommendation (CONFIRMED)

- **`triggerSlices` runs on the BUILD pipeline worker** (terminal flush is driven from build-pipeline `stop`
  per the audit; steady-state from build watermark advance). The placement-new + registry INSERT happens here.
- **The probe `open()` drain runs on a (possibly different) PROBE worker thread.** Confirmed thread boundary:
  `emitBuffer` (`AggregationOperatorHandler.cpp:148`) → `DefaultPEC` emit lambda → `pool.emitWork(...)`
  (`QueryEngine.cpp:506`) enqueues a `WorkTask` that holds the `TupleBuffer buf` by refcount; a worker dequeues
  and runs `pipeline->stage->execute(task.buf, pec)` (`:525`) → probe `open()`. Construction thread ≠
  destruction thread. The registry release happens on the PROBE thread.
- **⇒ the registry MUST be mutex-guarded.** Two threads concurrently mutate it (build inserts, probe erases),
  and with threads>1 multiple probe workers may erase different keys concurrently.
- **RECOMMENDED lock type: `folly::Synchronized<std::map<...>>`.** Rationale: it is already the established
  idiom on THIS exact class (`rollingAverageNumberOfKeys`, hpp `:79`) and across the OOC store
  (`nes_cpp_idioms.md`). The whole-map critical sections here are tiny (one `emplace`, one `erase`/`find`),
  so a coarse `folly::Synchronized` rlock/wlock is simplest and idiomatic. A plain `std::mutex` + `std::map`
  is acceptable but introduces a second locking style on the class for no benefit. **Prefer `folly::Synchronized`.**
- **Interaction with the existing `evictionMutex` discipline — NO conflict, DIFFERENT objects.** The
  `evictionMutex` lives in the SLICE STORE (`SpillableTimeBasedSliceStore`), guarding spill/unspill/emit
  decisions on SLICES. The new `chunkRegistry` lives on the AGG HANDLER, guarding the lifetime of EMITTED
  CHUNK maps. They are distinct objects guarding distinct state. **No lock-ordering concern arises at 2c0**
  because the registry is touched ONLY at (a) `triggerSlices` AFTER the store has already returned the slices
  (the store's `evictionMutex`/`ensureWindowSlicesResident` work is done before `triggerSlices` is entered,
  `WindowBasedOperatorHandler.cpp:102` then `:108`), and (b) the probe release, which never touches the store.
  So the registry lock and the store's `evictionMutex` are never held nested. **Guidance for the implementer:
  keep it that way — never call into the slice store while holding the `chunkRegistry` lock, and never insert
  into the registry while holding `evictionMutex`** (today `triggerSlices` holds neither store lock, so this is
  free as long as the registry critical section stays self-contained: build the `ChunkMaps` as a stack local,
  then take the registry wlock only for the `emplace`).

---

## 6. Behavior-identical checklist (what must NOT change for P=1 byte-identity)

At P=1, 2c0 is a PURE OWNERSHIP MOVE. The following MUST stay exactly as today:

1. **Chunk metadata values** (`AggregationOperatorHandler.cpp:132-139`): originId=`outputOriginId`,
   sequenceNumber=`windowInfo.sequenceNumber`, chunkNumber=`INITIAL`, lastChunk=`true`,
   watermark=`windowInfo.windowInfo.windowStart`, numberOfTuples=`totalNumberOfTuples`. UNCHANGED.
   Exactly ONE chunk emitted per window (no partition loop).
2. **`allHashMaps[]` still points at the SAME slice-owned maps** (`:104` `getHashMapPtr`). 2c0 does NOT
   move the input maps into the registry — at P=1 there are no re-materialized maps; `hashMaps[]` keeps
   pointing at slice memory protected by the base GC/`emittedKeys`. Only `finalHashMap` (the accumulator)
   moves into the registry. ⇒ `ChunkMaps.inputs` is EMPTY at 2c0; `numberOfHashMaps` and `hashMaps[]`
   semantics are identical to today. (This is what keeps the combine loop byte-identical.)
3. **The probe combine loop** (`AggregationProbePhysicalOperator.cpp:75-130`): UNCHANGED — no JIT/codegen edit.
   It still reads `hashMaps[curHashMap]` and `finalHashMapPtr` exactly as today. `finalHashMapPtr` must still
   be a valid raw pointer to the (now registry-owned) final accumulator at drain time.
4. **The lowering loop + `executeChild`** (`:133-157`): UNCHANGED — identical output records, same field
   writes (`windowStart`/`windowEnd`).
5. **GC / output-watermark gating** (`WindowBasedOperatorHandler.cpp:59-71`, `garbageCollectSlicesAndWindows`):
   UNCHANGED. The slice-side `emittedKeys`/GC regime that keeps the input maps alive is untouched (the input
   maps are still slice-owned). The registry only adds lifetime management for the FINAL accumulator, which
   today is freed by the probe and was never under GC.
6. **Allocation site of the final accumulator** (`createNewMapWithSameConfiguration`, `:108`): UNCHANGED —
   same map config, same `getUnpooledBuffer` for the emit buffer (the buffer SHRINKS only by `sizeof(unique_ptr)`
   worth of struct layout, which does not affect emitted tuple content; verify `neededBufferSize` `:119`
   still covers the (smaller) struct + the tail pointer array).
7. **No new chunk, no renumbering, no extra emit.** The registry insert/erase are pure side-bookkeeping
   invisible to the data path.

The 2c0 leak-totality gate (DESIGN-BRIEF §7) is the proof obligation: ASAN/LSAN, `frees == chunksEmitted`,
zero leaks, under (a) clean flush, (b) `stop()` mid-flush, (c) worker-shutdown with queued chunks.

---

## 7. Ownership / per-chunk release point (CONFIRMED, with the path-(c) caveat)

- **The probe cleanup invoke at `AggregationProbePhysicalOperator.cpp:160-169` IS the single drain-completion
  site** to hook the per-chunk release onto. It is the one-for-one swap the brief describes:
  `finalHashMap.reset()` (`:167`) → `handler->releaseChunk(seq, chunk)`. This frees BOTH the final accumulator
  and (in 2c1/2d) the input maps for that chunk, exactly when the probe finishes draining it — preserving the
  `1/P` memory bound.
- **`stop()` sweep is the teardown safety net** for any chunk whose probe release never ran. Per the audit,
  those paths are: (c) exception in the probe combine/lower loop (bare cleanup at `:160` is skipped);
  (d) query failure with buffers queued (`QueryEngine.cpp:488` lock fails); (e) early stop/cancellation;
  (f) worker/engine teardown — the NORMAL end-of-query path (`QueryEngine.cpp:481-485` skip when terminating;
  `:633-637` drops buffers during termination). On ALL of these the buffer is dropped WITHOUT running the
  release; the registry entry survives and is swept by `AggregationOperatorHandler::stop`.
- **Is there a path where the probe processes the buffer but does NOT reach the cleanup point (other than
  exception)?** Confirmed answer: **No, other than an exception inside `open()` (audit path c).** The cleanup
  invoke at `:160` is the last statement of `open()`; if the combine (`:75-130`) and lower (`:133-157`) loops
  complete without throwing, control reaches `:160` unconditionally (no early `return`, no `continue` out of
  `open()` between the recovery at `:63` and the invoke at `:160`). So within a successfully-draining `open()`
  the release always runs. The only intra-`open()` skip is a thrown exception — which the `stop()` sweep
  covers. (The buffer being dropped BEFORE `open()` runs at all — paths d/e/f — is also covered by `stop()`.)
- **Single-shot requirement:** each chunk = its own buffer = its own `WorkTask` = its own `open()` (audit
  path b), so a given `(seq,chunk)` key is released at most once on the happy path. The registry `erase` is
  naturally single-shot (erasing a missing key is a no-op); make the release tolerate a missing key so a
  `stop()` sweep after a normal release, or a double-invoke, is harmless. This subsumes audit guardrails
  G1/G2/G5 (which become MOOT under Option 2 — the buffer owns no heap).

---

## CHANGE MAP — "what changes where" for 2c0 (P=1, byte-identical)

**CHANGE-1 — `AggregationOperatorHandler.hpp`**
- Add `struct ChunkMaps { std::vector<std::unique_ptr<HashMap>> inputs; std::unique_ptr<HashMap> finalAccumulator; };`
  (near `EmittedAggregationWindow`).
- Rewrite `EmittedAggregationWindow` (`:39-55`) to the trivially-destructible shape: DROP
  `std::unique_ptr<HashMap> finalHashMap` (`:52`); keep `finalHashMapPtr` (raw, `:51`); ADD trivial
  `SequenceNumber seq; ChunkNumber chunk;` members (so the probe release reads the key off the buffer);
  ctor now takes `(WindowInfo, SequenceNumber, ChunkNumber, HashMap* finalAccumulatorPtr, const std::vector<HashMap*>&)`
  and stores raw pointers only. Verify `static_assert(std::is_trivially_destructible_v<EmittedAggregationWindow>)`.
- Add the registry member to `AggregationOperatorHandler` (protected, by `:79`):
  `folly::Synchronized<std::map<std::pair<SequenceNumber, ChunkNumber>, ChunkMaps>> chunkRegistry;`
- Declare a `void releaseChunk(SequenceNumber, ChunkNumber);` method and a `stop(...)` override.
- Add includes: `<utility>` (pair) already present; `folly/Synchronized.h` is pulled transitively via
  `RollingAverage`/existing usage — confirm.

**CHANGE-2 — `AggregationOperatorHandler.cpp::triggerSlices` (`:80-157`)**
- `finalHashMap` stays a stack local (`:87,:108`) — already G4-correct.
- After computing `allHashMaps`/`finalHashMap`/`totalNumberOfTuples`, build a `ChunkMaps` local:
  `inputs` EMPTY (P=1), `finalAccumulator = std::move(finalHashMap)`.
- Take `chunkRegistry.wlock()` and `emplace({windowInfo.sequenceNumber, INITIAL}, std::move(chunkMaps))`.
  Keep this critical section self-contained (no store calls inside it — §5 lock-ordering).
- Placement-new (`:144`) now passes the **raw** `finalAccumulatorPtr` (registry-owned `.get()`) + the
  `(seq, chunk)` key + `allHashMaps`. The struct no longer owns the unique_ptr.
- Metadata `:132-139` UNCHANGED (byte-identity).
- Implement `releaseChunk(seq, chunk)`: `chunkRegistry.wlock()->erase({seq, chunk})` (no-op if absent).
- Implement `AggregationOperatorHandler::stop(...)`: `chunkRegistry.wlock()->clear();` then call base
  `WindowBasedOperatorHandler::stop(...)`.

**CHANGE-3 — `AggregationProbePhysicalOperator.cpp::open` (`:160-169`)**
- Recover the handler at the release point (new): `getGlobalOperatorHandler(operatorHandlerId)` →
  `RawValueResolver<OperatorHandler*>::getRawValue` → `dynamic_cast<AggregationOperatorHandler*>`
  (idiom from `AggregationBuildPhysicalOperator.cpp:53-54`).
- Replace the `:160-169` invoke body: instead of `finalHashMap.reset()`, read `seq`/`chunk` off the struct
  (via `getMemberRef`, like `numberOfHashMaps` at `:64-65`) and `nautilus::invoke` a free function
  `releaseAggregationChunkProxy(AggregationOperatorHandler*, SequenceNumber, ChunkNumber)` that calls
  `handler->releaseChunk(seq, chunk)`. Single-shot/tolerant-of-missing-key.
- Combine/lower loops (`:75-157`) UNCHANGED.

**CHANGE-4 — `WindowBasedOperatorHandler.cpp` (`:50`)**
- No change needed here IF `stop` is overridden on `AggregationOperatorHandler` (recommended, §4). The base
  empty `stop` (`:50-52`) stays. (Do NOT put agg-specific sweep logic in the shared base — high blast-radius
  per `nes_cpp_idioms.md` Increment C.)

---

## FLAGS / deltas vs the authority docs

1. **Not a drift, a missing detail (sharpen):** `EmittedAggregationWindow` does NOT carry `(seq,chunk)` today
   (it carries only `windowInfo`, hpp `:50`). DESIGN-BRIEF L8's "the `(sequenceNumber, chunkNumber)` key
   already present for chunk metadata" refers to the TupleBuffer metadata (cpp `:133-134`), not the struct.
   2c0 should ADD trivial `seq`/`chunk` members to the struct so the probe release reads the key directly.
2. **Not a drift, a missing detail (plumbing):** the probe `open()` does NOT fetch its operator handler today
   (§3). CHANGE-3 must add the standard `getGlobalOperatorHandler` recovery — it is available
   (`operatorHandlerId` is held by `WindowProbePhysicalOperator`) but is new code.
3. **`stop` ownership:** the sweep belongs on `AggregationOperatorHandler` (override), NOT in the shared
   `WindowBasedOperatorHandler::stop` (§4). The audit/brief say "the currently-empty
   `WindowBasedOperatorHandler::stop`" — at 2c0 the cleaner placement is an override on the agg handler so
   joins are untouched. Flag for the implementer; both compile, but the override is the lower-blast-radius choice.
4. **All audit line anchors confirmed at HEAD** (no relitigation needed): placement-new `:144`,
   getUnpooledBuffer `:120`, finalHashMap.reset `:167`, recycle no-content-dtor (`TupleBufferImpl.cpp:208`,
   `UnpooledChunksManager.cpp:160-178`), terminating-skip (`QueryEngine.cpp:481-485`), drop-on-teardown
   (`:633-637`), empty `stop` (`WindowBasedOperatorHandler.cpp:50`). The audit is accurate; proceed on it.
