# 2c0 CONTRACTS — Test surface map + TDD plan for the handler-owned-registry ownership refactor

> READ-ONLY exploration. No code modified. Scope: step 2c0 (DESIGN-BRIEF §5 / §7, EMITTEDWINDOW-LIFETIME-AUDIT G3=Option 2).
> Goal of the refactor under test: make `EmittedAggregationWindow` **trivially destructible** (raw pointers + `(seq,chunk)` key only), move all per-chunk map ownership (`finalHashMap` accumulator + W input maps) into a mutex-guarded `chunkRegistry` on `AggregationOperatorHandler`, freed **per-chunk on probe drain-ack** and **swept in `WindowBasedOperatorHandler::stop`**.
> Date: 2026-05-24.

---

## 0. Headline findings (read first)

1. **There is NO existing C++ unit/integration test that drives `AggregationOperatorHandler::triggerSlices` → emit → `AggregationProbePhysicalOperator::open` (drain).** Aggregation *output correctness* is proven exclusively by **systests** (`nes-systests/.../aggregation/*.test`, `nes-systests/differential/AggregationEquiValence.test`). The closest unit tests drive the *slice store* (`SpillableTimeBasedSliceStoreTest`) or the *slice* (`SpillableAggregationSliceTest`) directly — neither touches the handler's emit path, the `EmittedAggregationWindow` struct, or the probe. **The 2c0 leak gate is therefore a NEW test file**, not an extension of an existing one.
2. **The reusable harness for a handler-scope test already exists**: `EmitPhysicalOperatorTest.cpp`'s `MockedPipelineContext` (a `PipelineExecutionContext` subclass that overrides `emitBuffer` to capture emitted `TupleBuffer`s into a `folly::Synchronized<std::vector<TupleBuffer>>`). This is the smallest-scope vehicle for teardown variants (a) and (b). See §2.
3. **`WindowBasedOperatorHandler::stop` is virtual and currently empty** (`nes-physical-operators/src/WindowBasedOperatorHandler.cpp:50-52`); `StreamJoinOperatorHandler` does **NOT** override it (only `triggerSlices`) — confirmed `nes-physical-operators/include/Join/StreamJoinOperatorHandler.hpp:37-48`. So adding a registry sweep to the base `stop` is ABI-safe for the join (it inherits the new behavior harmlessly; its registry is empty). See §5.
4. **ASAN/LSAN is a build-time switch** (`cmake/Sanitizers.cmake:35-38`, `USE_SANITIZER=address` or env `VCPKG_SANITIZER`), applied to the whole build. The leak gate is "run the suite under an ASAN/LSAN build" PLUS an in-test **deterministic free-counter assertion** (so the gate fails loudly even on a non-sanitizer build). Don't rely on LSAN alone.

---

## 1. Existing-test inventory (fixtures + what they actually drive)

| Test file (`nes-physical-operators/tests/`) | Fixture base | Drives | Reusable for 2c0? |
|---|---|---|---|
| `SpillableAggregationSliceTest.cpp` | `BaseUnitTest` | `SpillableAggregationSlice` spill/unspill, per-worker maps, `getHashMapPtr*` seam | **YES** — `makeArgs()` / `populate()` / `sortedEntries()` are the canonical way to build a `ChainedHashMap`-backed slice and read it back order-independently. Reuse verbatim to build the input slices the handler will emit. |
| `SpillableTimeBasedSliceStoreTest.cpp` | `BaseUnitTest` | `SpillableTimeBasedSliceStore` (spill thresholds, GC, terminal flush, `getTriggerableWindowSlices`/`getAllNonTriggeredSlices`/`ensureWindowSlicesResident`) | **PARTIAL** — provides `spillableSliceFactory()`, `makeStoreWithThresholds()`, and the `ThrowingSpillBackend` throw-injection precedent (`:57-88`). Does NOT drive the handler/probe. Use as the slice-store the handler is constructed with if a store-coupled variant is needed. |
| `EmitPhysicalOperatorTest.cpp` | `Testing::BaseUnitTest` | `EmitPhysicalOperator::open/close` against a **mock PEC** that captures emitted buffers; chunk/seq/lastChunk metadata asserts | **YES — the load-bearing reuse.** `MockedPipelineContext` (`:63-111`) is the only existing pattern that captures `emitBuffer` output at unit scope. Its `checkBufferAt`/`checkForDups`/`checkLastChunks` helpers (`:158-216`) are exactly the chunk-metadata assertions 2c0 (and later 2d) need. |

### Most reusable example test + setup (quote)

**Building a `ChainedHashMap`-backed aggregation slice** (`SpillableAggregationSliceTest.cpp:71-94`):

```cpp
static CreateNewHashMapSliceArgs makeArgs() {
    auto noopCleanup = std::make_shared<CreateNewHashMapSliceArgs::NautilusCleanupExec>(
        std::function<void(nautilus::val<HashMap*>)>([](nautilus::val<HashMap*>) { }));
    return CreateNewHashMapSliceArgs({std::move(noopCleanup)}, KEY_SIZE, VALUE_SIZE, PAGE_SIZE, NUMBER_OF_BUCKETS);
}
void populate(SpillableAggregationSlice& slice, WorkerThreadId wid, uint64_t count, uint64_t offset) const {
    auto* map = dynamic_cast<ChainedHashMap*>(slice.getHashMapPtrOrCreate(wid));
    for (uint64_t i = 0; i < count; ++i) {
        const uint64_t hash = (offset + i + 1) * 2654435761ULL;
        auto* entry = static_cast<ChainedHashMapEntry*>(map->insertEntry(hash, bufferManager.get()));
        auto* payload = reinterpret_cast<std::byte*>(entry) + sizeof(ChainedHashMapEntry);
        const uint64_t key = offset + i; const uint64_t value = (i * 7) + 1;
        std::memcpy(payload, &key, sizeof(key));
        std::memcpy(payload + sizeof(key), &value, sizeof(value));
    }
}
```

**The mock-PEC emit-capture harness** (`EmitPhysicalOperatorTest.cpp:63-111`, abridged) — subclass `PipelineExecutionContext`, override only what the handler calls (`emitBuffer`, `getBufferManager`, `getNumberOfWorkerThreads`, `getOperatorHandlers`):

```cpp
struct MockedPipelineContext final : PipelineExecutionContext {
    bool emitBuffer(const TupleBuffer& buffer, ContinuationPolicy) override {
        buffers.wlock()->emplace_back(buffer); return true;   // capture
    }
    std::shared_ptr<AbstractBufferProvider> getBufferManager() const override { return bufferManager; }
    uint64_t getNumberOfWorkerThreads() const override { return 1; }
    // ... allocateTupleBuffer / pinBuffer / getOperatorHandlers / getPipelineId / repeatTask
    folly::Synchronized<std::vector<TupleBuffer>>& buffers;
    std::shared_ptr<BufferManager> bufferManager;
};
```

> The handler's `triggerSlices` calls exactly: `pipelineCtx->getBufferManager()->getUnpooledBuffer(n)` (`AggregationOperatorHandler.cpp:120`) and `pipelineCtx->emitBuffer(tupleBuffer)` (`:148`). The mock above already covers `getBufferManager`; **the mock must additionally support `getUnpooledBuffer`** via its returned `AbstractBufferProvider` (real `BufferManager::create()` provides it — no extra mocking needed; `getUnpooledBuffer` is a real BufferManager method). So a `MockedPipelineContext` backed by a real `BufferManager` is sufficient to drive `triggerSlices` end-to-end at unit scope.

### How an emit→drain cycle is driven today (only at systest scope)
Build pipeline `stop` → `WindowBasedOperatorHandler::triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91-110`, one window at a time after `ensureWindowSlicesResident`) → `AggregationOperatorHandler::triggerSlices` (`AggregationOperatorHandler.cpp:80-157`) placement-news `EmittedAggregationWindow` at `:144` and `emitBuffer`s at `:148` → async hop to a probe worker → `AggregationProbePhysicalOperator::open` (`AggregationProbePhysicalOperator.cpp:51-170`) combines `hashMaps[]` into `finalHashMap`, lowers, then runs the **drain-ack lambda** at `:160-169` (today: `finalHashMap.reset()`).

---

## 2. The three teardown variants — smallest viable scope each

The §7 leak gate requires three teardowns. Because no handler-level emit→drain test exists, **the unit-scope vehicle is a `triggerSlices(...)` call against a `MockedPipelineContext` + a hand-driven `AggregationProbePhysicalOperator::open(...)` over the captured buffer** (or, for variant (a)/(b), a hand-invoked "drain-ack" that calls the registry-release path). This isolates 2c0 cleanly and keeps the gate deterministic.

### Variant (a) — clean end-of-stream terminal flush (normal full drain)
- **Scope: HANDLER UNIT TEST** (new `AggregationOperatorHandlerLeakTest.cpp`, `BaseUnitTest`).
- **Drive:** build N input slices via `makeArgs()`/`populate()`; construct an `AggregationOperatorHandler` (spill off, in-memory store) with `setWorkerThreads(W)`; call `triggerSlices(oneWindow, &mockPec)` (mirrors `triggerAllWindows`' per-window call shape, `WindowBasedOperatorHandler.cpp:107-108`). Then **drain every captured buffer**: run the registry-release path once per `(seq,chunk)` (the production seam the probe lambda invokes at `AggregationProbePhysicalOperator.cpp:167`).
- **Assert:** registry empty + `frees == chunksEmitted` (at P=1, `chunksEmitted == windowsEmitted == 1`).

### Variant (b) — `QueryEngine::stop()` injected MID-flush (some chunks NOT drained)
- **Scope: HANDLER UNIT TEST** (same file). Do NOT reach for the real `QueryEngine` — `QueryEngineTestingInfrastructure.hpp` uses synthetic `TestPipeline`/`TestSink` stages (`:202-397`), never a real aggregation handler, so it cannot exercise the registry. Simulate the engine stop at handler scope.
- **Drive:** `triggerSlices(...)` to populate the registry with K chunks; **drain only J < K of them** (call the release path for J chunks); then call `handler.stop(QueryTerminationType::Graceful, mockPec)` — the sweep. This reproduces "engine stop while some emitted chunks are still queued/undrained" exactly: the undrained `(seq,chunk)` entries are the buffers the engine drops on teardown (audit paths d/e/f, `EMITTEDWINDOW-LIFETIME-AUDIT.md:78-80`).
- **Assert:** after `stop()` → registry empty + every map freed (`frees == chunksEmitted`), even though only J were drain-acked.

### Variant (c) — worker/engine shutdown with chunks STILL queued (none drained)
- **Scope: HANDLER UNIT TEST** (same file). The degenerate case of (b) with J=0.
- **Drive:** `triggerSlices(...)` populates the registry; drain **nothing**; call `handler.stop(...)`. Mirrors the dominant leak surface in the audit: the terminal-flush burst dropped wholesale on worker teardown (`EMITTEDWINDOW-LIFETIME-AUDIT.md:80`, `QueryEngine.cpp:481-485`, `:633-637`).
- **Assert:** `stop()` sweep frees all K chunks → registry empty + `frees == chunksEmitted`.

### Full-engine variant (integration, run at 2d not 2c0)
For an end-to-end proof that the engine's real drop-on-teardown paths don't leak, the existing **systest-in-Docker harness** is the vehicle: `.planning/eval-phase/phase0c/run-rss.sh` runs `nes-systests/systest/systest -t <SpillSmoke.test>` with spill knobs via `-- --worker.default_query_execution.spill.<knob>=<val>` passthrough (`run-rss.sh:46-52`). Build that image with `VCPKG_SANITIZER=address` (`Sanitizers.cmake:15`) and read LSAN output + exit code. **This is the §7 (b)/(c) integration confirmation, deferred to 2d** — 2c0's gate is the deterministic unit test above (smaller, faster, no Docker). The brief explicitly says the unit gate must pass at 2c0 P=1 *before* partitioning (DESIGN-BRIEF.md:124, :88).

> **Recommendation: all three 2c0 variants are handler-unit-scope.** No full-engine run is needed to pass the 2c0 gate; the engine-level confirmation rides along at 2d's `run-rss.sh` ASAN build. Reasoning: (a) the registry + `stop()` sweep is the *entire* mechanism under test, and it is fully observable at handler scope; (b) the real engine teardown merely *drops* the buffer (no dtor) — which, post-refactor, is harmless (trivially-destructible buffer) — so the only thing that needs proving is "the registry frees everything," which `stop()` does deterministically in a unit test.

---

## 3. The leak assertion (exact)

The registry needs a test-observable surface. **Design contract for the registry to make the gate writable:**
- a `size()` accessor (or `bool registryEmpty()`), and
- a **free counter** incremented exactly once per `ChunkMaps` actually freed (on per-chunk release AND on sweep), exposed read-only to the test (e.g. `std::atomic<uint64_t> framesFreed` / `freedChunks()`).
- `chunksEmitted` is observable from the mock PEC (`buffers.rlock()->size()`), so the test computes the expected free count itself.

| Teardown variant | Assertion |
|---|---|
| (a) clean full drain | `registry.size() == 0` AND `freedChunks() == chunksEmitted` (P=1 ⇒ `chunksEmitted == 1`). |
| (b) mid-flush `stop()` (J<K drained) | After `stop()`: `registry.size() == 0` AND `freedChunks() == chunksEmitted` (= K). No double-free of the J already drain-ack'd (single-shot per `(seq,chunk)`). |
| (c) shutdown, 0 drained | After `stop()`: `registry.size() == 0` AND `freedChunks() == chunksEmitted` (= K). |
| ASAN/LSAN overlay (all) | Under `USE_SANITIZER=address`: zero leaked `HashMap`/`ChainedHashMap`/`TupleBuffer` reported. `~ChainedHashMap()` is declared non-trivial (`ChainedHashMap.hpp:79`) and returns pages on destruction, so freeing the `unique_ptr<ChainedHashMap>` in `ChunkMaps` is what LSAN verifies (audit residual-unknown #3, `EMITTEDWINDOW-LIFETIME-AUDIT.md:122`). |

### Throw-in-combine-loop case
- **Mechanism:** inject a throw inside the probe combine loop (`AggregationProbePhysicalOperator.cpp:80-129`) — e.g. a test-only aggregation function whose `combine` throws, or (cleaner) drive the registry-release path under a simulated exception. The audit calls for this explicitly (`EMITTEDWINDOW-LIFETIME-AUDIT.md:121`, DESIGN-BRIEF.md:124).
- **Expected result post-Option-2:** **no leak by construction** — the buffer owns no heap (trivially destructible raw pointers), so an exception that drops the buffer leaks nothing; the maps remain owned by the registry and are freed by the `stop()` sweep. The test PROVES this: trigger → simulate throw during/before drain-ack of chunk X → `stop()` → `registry.size()==0`, `freedChunks()==chunksEmitted`, LSAN clean.
- **Do NOT** test `PRECONDITION`/`INVARIANT` via try/catch — they terminate (see §6). The combine-loop throw must be a real `Exception`/`std::exception`, not a precondition trip.

---

## 4. Behavior-identical / regression tests to keep green (P=1 output unchanged)

There is no C++ unit test asserting aggregation output values; the P=1 byte-identical guarantee is held by **systests**. These MUST still pass unchanged after 2c0:

| Must-stay-green | Path | Proves |
|---|---|---|
| `nes-systests/differential/AggregationEquiValence.test` | differential | Logically-equivalent aggregations agree — exercises the combine/lower path end-to-end. |
| `nes-systests/operator/aggregation/WindowAggregationDifferentDataTypes.test` | operator | Tumbling/keyed SUM/etc. output correctness across types. |
| `nes-systests/operator/aggregation/WindowAggregationDifferentDataTypesKeyed.test` | operator | Keyed multi-worker aggregation (the W-map combine the probe does). |
| `nes-systests/operator/aggregation/WindowAggregationStackedAggregations.test` | operator | Multi-aggregation-function combine/lower. |
| `nes-systests/operator/aggregation/WindowWithoutAggregation.test`, `WindowAggregationNull.test`, `WindowAggregationProjection.test`, `AggregationOfUnion.test`, `ExpressionInsideAggregation.test` | operator | Broader emit/probe coverage. |
| Full suite **≥ 835 tests** | all | The brief's hard regression gate at every step (DESIGN-BRIEF.md:82, :88). |

The existing physical-operator unit tests (`SpillableAggregationSliceTest`, `SpillableTimeBasedSliceStoreTest`, `EmitPhysicalOperatorTest`) must also stay green — 2c0 touches the handler/probe/`stop`, not the slice or store, so these are unaffected but confirm no collateral break.

---

## 5. Contracts that must not break (ABI / aliasing / struct layout)

1. **Probe combine loop reads `hashMaps[]` read-only — CONFIRMED.** `AggregationProbePhysicalOperator.cpp:75-130` only *reads* `hashMapRefs[curHashMap]` (`:77`) and iterates `currentMap`; the only free is `finalHashMap.reset()` at `:167`. Aliasing the registry-owned maps via raw `HashMap**` therefore causes **no double-free through the combine loop** (also confirmed by audit step 7.1, `EMITTEDWINDOW-LIFETIME-AUDIT.md:96`). The refactor must keep `hashMaps[]` pointing at the registry-owned `ChunkMaps.inputs[i].get()`.
2. **What the probe assumes about `EmittedAggregationWindow` layout (must be preserved):** the probe reads members *by `getMemberRef`/`readValueFromMemRef` against the live struct layout* placement-new'd in the buffer (`AggregationProbePhysicalOperator.cpp:63-70`):
   - `&EmittedAggregationWindow::numberOfHashMaps` (`:65`)
   - `&EmittedAggregationWindow::windowInfo` → `windowStart`/`windowEnd` (`:66-68`)
   - `&EmittedAggregationWindow::hashMaps` (the `HashMap**` array, set to `this+1` in ctor, `AggregationOperatorHandler.hpp:46-47`) (`:69`)
   - `&EmittedAggregationWindow::finalHashMapPtr` (the raw accumulator pointer) (`:70`)
   So the trivially-destructible struct MUST keep these four members **with the same names and a `HashMap**`-at-`this+1` tail layout** (the `getMemberRef` offsets are computed against the C++ type). The change is: **drop `std::unique_ptr<HashMap> finalHashMap`** (`AggregationOperatorHandler.hpp:52`) — the only non-trivial member — and keep `finalHashMapPtr` as a raw alias of the registry-owned accumulator. Result: struct = `{WindowInfo, HashMap* finalHashMapPtr, uint64_t numberOfHashMaps, HashMap** hashMaps, + the (seq,chunk) key}` = trivially destructible, **exactly the `EmittedHJWindowTrigger` shape** (`HJOperatorHandler.hpp:38-60` — raw `HashMap**` only, no `unique_ptr`).
3. **The `(seq,chunk)` registry key must be reachable from the buffer at drain time.** The probe's drain-ack lambda (`:160-169`) needs `(sequenceNumber, chunkNumber)` to release the right registry entry. The buffer already carries `setSequenceNumber`/`setChunkNumber` (`AggregationOperatorHandler.cpp:133-134`) read via `recordBuffer.getSequenceNumber()/getChunkNumber()` (`AggregationProbePhysicalOperator.cpp:56-57`), so the key is available without adding struct fields — but the design notes the struct *also* carries the key for clarity; either source must agree.
4. **`WindowBasedOperatorHandler::stop` ABI — SAFE.** Declared `virtual ... override` (overrides `OperatorHandler::stop`) at `WindowBasedOperatorHandler.hpp:68`; defined empty at `WindowBasedOperatorHandler.cpp:50-52`. **`StreamJoinOperatorHandler` does NOT override `stop`** (only `triggerSlices`, header `:37-48`) — confirmed by `grep "void stop("` returning only the base + `EmitOperatorHandler`. So putting the registry sweep in the base `stop` is inherited by the join harmlessly (its registry is empty; joins use trivially-destructible `EmittedHJWindowTrigger` and need no sweep). **Do NOT** make `stop` non-virtual or change its signature. If 2c0 instead overrides `stop` on `AggregationOperatorHandler` (rather than the base), it must call the base `stop` — but the brief (DESIGN-BRIEF.md:88, :39) and audit (`:110`, `:131`) both target the *base* `WindowBasedOperatorHandler::stop` for the sweep; the registry lives on `AggregationOperatorHandler`, so a clean shape is: base `stop` calls a `protected virtual sweepChunkRegistry()` (no-op in base, real in aggregation). Either way, base-`stop`-is-the-net is the contract.
5. **`getUnpooledBuffer` (not pooled) is the emit buffer** (`AggregationOperatorHandler.cpp:120`). Recycling never runs a content dtor (`EMITTEDWINDOW-LIFETIME-AUDIT.md:46-53`) — which is exactly *why* the trivially-destructible struct + registry ownership is correct: the buffer being dropped undrained is now harmless. The test must keep using `getUnpooledBuffer` semantics (the real `BufferManager` supplies it).

---

## 6. GoogleTest / macro patterns to use

- **Fixture:** `class AggregationOperatorHandlerLeakTest : public NES::Testing::BaseUnitTest` with `SetUpTestSuite()` (logging) + `SetUp()` calling `BaseUnitTest::SetUp()` then `bufferManager = BufferManager::create();` — same shape as `SpillableAggregationSliceTest.cpp:46-59` and `EmitPhysicalOperatorTest.cpp:114-124`.
- **CMake registration:** add to `nes-physical-operators/tests/CMakeLists.txt` via `add_nes_physical_operator_test(AggregationOperatorHandlerLeakTest AggregationOperatorHandlerLeakTest.cpp)` (links `nes-physical-operators nes-memory-test-utils nes-test-util`, `:13-29`).
- **Asserts:** `EXPECT_EQ`/`ASSERT_EQ` for `registry.size()`/`freedChunks()`/`chunksEmitted`. Reuse `EmitPhysicalOperatorTest`'s `checkForDups()`/`checkLastChunks()` idiom for the (later 2d) multi-chunk metadata; at 2c0 P=1 assert the single `INITIAL`/`lastChunk=true` chunk (`AggregationOperatorHandler.cpp:134-135`).
- **Expected exceptions (the throw-in-combine case):** use `ASSERT_EXCEPTION_ERRORCODE(statement, errorCode)` (`BaseUnitTest.hpp:71-80`) if the injected throw is an NES `Exception` with a code; otherwise `EXPECT_THROW`/`EXPECT_ANY_THROW` (cf. `SpillableTimeBasedSliceStoreTest.cpp` `EXPECT_ANY_THROW` at `:738`). Then assert the leak invariant *after* the throw.
- **PRECONDITION / INVARIANT terminate — do NOT test-catch them.** `triggerSlices` uses none directly, but the probe proxy has `PRECONDITION(emittedAggregationWindow != nullptr, ...)` and `PRECONDITION(currentHashMapVal < numberOfHashMaps, ...)` (`AggregationProbePhysicalOperator.cpp:45-47`); these abort, they are not catchable. Death-tests would be `ASSERT_DEATH_DEBUG` (`BaseUnitTest.hpp:54/68`) but are NOT in scope for the leak gate — only the real-`Exception` combine throw is.
- **ASAN/LSAN:** build the test target under `-DUSE_SANITIZER=address` (`Sanitizers.cmake:35-38`) for the leak overlay; the deterministic `freedChunks() == chunksEmitted` assertion is the primary gate so the test also fails on a plain build.

---

## 7. Cited file:line index (for the implementer)

- Struct to make trivially destructible: `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp:39-55` (drop `unique_ptr<HashMap> finalHashMap` at `:52`; keep `finalHashMapPtr :51`, `numberOfHashMaps :53`, `hashMaps :54`, `windowInfo :50`).
- Registry insert site (build maps as stack-RAII, move into registry): `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:87-112` (build), `:119-120` (buffer sizing/alloc), `:144` (placement-new), `:148` (emit).
- Drain-ack release seam (swap `finalHashMap.reset()` → registry release): `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp:160-169`; key from `:56-57`.
- Probe read-only combine loop (must keep `hashMaps[]` valid raw pointers): `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp:75-130`; member reads `:63-70`.
- `stop()` sweep site (virtual base, empty today): `nes-physical-operators/src/WindowBasedOperatorHandler.cpp:50-52`; decl `nes-physical-operators/include/WindowBasedOperatorHandler.hpp:68`.
- Join does NOT override `stop` (ABI safe): `nes-physical-operators/include/Join/StreamJoinOperatorHandler.hpp:37-48`.
- Trivially-destructible precedent (raw `HashMap**`): `nes-physical-operators/include/Join/HashJoin/HJOperatorHandler.hpp:38-60`.
- Explicit placement-dtor precedent: `nes-physical-operators/src/Join/HashJoin/HJBuildPhysicalOperator.cpp:80-83` (`->~PagedVector()`) and `:131-134` (placement-`new`).
- Mock-PEC harness to reuse: `nes-physical-operators/tests/EmitPhysicalOperatorTest.cpp:63-111` (`MockedPipelineContext`), assert helpers `:152-216`, fixture `:114-244`.
- Slice-build fixture to reuse: `nes-physical-operators/tests/SpillableAggregationSliceTest.cpp:46-116` (`makeArgs`/`populate`/`sortedEntries`).
- Throw-injection precedent: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp:57-88` (`ThrowingSpillBackend`).
- `ChainedHashMap` non-trivial dtor (LSAN page-return target): `nes-nautilus/include/Nautilus/Interface/HashMap/ChainedHashMap/ChainedHashMap.hpp:79`.
- ASAN/LSAN switch: `cmake/Sanitizers.cmake:13-41`.
- `ASSERT_EXCEPTION_ERRORCODE`: `nes-common/tests/Util/include/BaseUnitTest.hpp:71-80`.
- Spill config knob home (no `number_of_partitions` yet — 2d): `nes-query-compiler/interface/SpillConfig.hpp:41-75`.
- Full-engine integration vehicle (2d, not 2c0): `.planning/eval-phase/phase0c/run-rss.sh:46-80`, `.planning/eval-phase/SpillSmoke.e0-redo.test`.
- Regression systests to keep green: `nes-systests/differential/AggregationEquiValence.test`, `nes-systests/operator/aggregation/*.test`.
