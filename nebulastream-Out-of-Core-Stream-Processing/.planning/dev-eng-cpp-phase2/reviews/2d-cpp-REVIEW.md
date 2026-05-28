# 2d C++ Review

Reviewer: Claude Sonnet 4.6 (general C++ / idioms; concurrency depth covered separately in 2d-concurrency-REVIEW.md)
Date: 2026-05-24

---

## Verdict

**BLOCK (1 Critical / 1 High)**

The design and structure are solid: the `emitChunk` extraction is clean and byte-identical to the pre-2d path; the `claimSliceForEmit` extraction correctly shares the concurrency-sensitive claim logic; `materializeResidentPartition` correctly mirrors `partitionedSerialize`'s `hash % P` routing; ownership via the handler registry (L8) is correctly propagated to P>1. The two issues below must be fixed before merge; every other finding is medium or below.

---

## Critical

None.

---

## High

### H1 — `forceSpill` and `spillOneColdSlice` pass `numberOfPartitions = 1` (implicit default) — spill blobs are NEVER P-way

**Files:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:687` and `:818`

Both store-level spill call sites use `spillable->spill(*backend)` with the default `numberOfPartitions = 1`, regardless of the store's configured `this->numberOfPartitions`. This means every slice is spilled as a single blob under partition=0. When `triggerAllWindows` subsequently calls `streamWindowPartition(slices, p)` → `spillable->streamEmitByPartition(..., numberOfPartitions=8, partition=p)`, only `partition=0` finds any data (`backend.get` returns `nullopt` for p=1..7). Skip-empty then emits K=1 chunk containing ALL the window's data — functionally correct output but with ZERO partitioning benefit; the full window is materialised at once, defeating the 1/P memory bound that is the entire point of 2d.

The same inconsistency propagates through the `unspill` path: if a slice were hypothetically spilled with P=8 and then `ensureWindowSlicesResident` called `spillable->unspill(*backend, *provider)` (also defaulting to P=1), `unspill` would only read partition=0 and lose 7/8 of the data.

**Both tests pass despite this bug** because the skip-empty logic handles the degenerate case silently: `partitionedEmitUnionEqualsWindowMixedResidentAndSpilled` uses `forceSpill` (P=1 effective) then streams with P=8 — partition=0 wins all data; `partitionedTriggerEmitsKContiguousChunksAndSkipsEmpty` similarly `forceSpill`s with P=1 then claims `K < P` by pigeonhole, which is trivially satisfied with K=1. Neither test proves true P-way partitioned emission.

**Fix — two-line change:**
```cpp
// SpillableTimeBasedSliceStore.cpp:687 (forceSpill)
spillable->spill(*backend, numberOfPartitions);

// SpillableTimeBasedSliceStore.cpp:818 (spillOneColdSlice)
spillable->spill(*backend, numberOfPartitions);
```

Also update `unspill` call sites (lines 311, 439, 529) to pass `numberOfPartitions` if full-window unspill is ever expected to work correctly under P>1 (the steady-state `ensureWindowSlicesResident` path; currently L6 defers steady-state to P=1, but the calls should still be consistent):
```cpp
spillable->unspill(*backend, *provider, numberOfPartitions);
```

**Test fix required (or add new test):** The two existing 2d store/handler tests must be strengthened to prove true P>1 partitioning. The simplest approach: in both tests, manually call `spillable->spill(*backend, P)` directly instead of `store->forceSpill()`, so the blob layout on disk matches the emit-time partition count. Then assert that `K > 1` (multiple non-empty partitions confirm hash-distributing across P residues). Alternatively, fix the store call sites and re-run.

---

## Medium

### M1 — Missing upper-bound validation for `numberOfPartitions` vs. `PartitionId` width (uint16_t)

**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:134`

`PartitionId` is `uint16_t` (max 65535, per L2). The ctor PRECONDITION checks `numberOfPartitions >= 1` but has no `<= UINT16_MAX` guard. If a user passes `numberOfPartitions = 65536`, the `static_cast<PartitionId>(partition)` at lines 367 and 380 wraps: `static_cast<uint16_t>(65535) = 65535` (fine), but the loop iterates `partition = 0 .. 65535`, and `static_cast<uint16_t>(65536) = 0` would produce a duplicate partition=0 read. No known user will hit this (edge device, P=4..16 in practice), but a defensive PRECONDITION is cheap:

```cpp
PRECONDITION(numberOfPartitions <= std::numeric_limits<PartitionId>::max(),
    "numberOfPartitions {} exceeds PartitionId max {}",
    numberOfPartitions, std::numeric_limits<PartitionId>::max());
```

Also worth adding a PRECONDITION in `streamWindowPartition` and `materializeResidentPartition` that `static_cast<PartitionId>(partition) == partition` (i.e. no truncation), but the `partition < numberOfPartitions` check already covers it transitively once the ctor guard is in place.

### M2 — `triggerAllWindows` K==0 fallback calls `ensureWindowSlicesResident` after `claimWindowForPartitionedEmit` has already claimed the slices

**File:** `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:335`

When all P partitions return empty maps (`numberOfNonEmptyPartitions == 0`), the code calls `sliceAndWindowStore->ensureWindowSlicesResident(slices)` on slices that were already claimed by `claimWindowForPartitionedEmit`. `ensureWindowSlicesResident` calls `claimSliceForEmit` again, which is idempotent (`std::set::insert` on an already-inserted key is a no-op). The double-claim is safe, but unnecessary and potentially confusing (an RAII or "already-claimed" flag would be cleaner). The K==0 path "does not arise for a triggered window" (documented in a comment), so the risk is theoretical. Low-severity but worth noting.

No change required; document in the comment or add an INVARIANT asserting `false` before the fallback with a "never reached for a triggered window" message.

### M3 — `emitChunk`: `inputMaps` silently dropped when `finalAccumulator` is null

**File:** `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:175`

```cpp
if (finalAccumulator) {
    finalHashMapPtr = registerChunk(..., std::move(finalAccumulator), std::move(inputMaps));
}
```

If `finalAccumulator` is null but `inputMaps` is non-empty (a caller contract violation), the maps are silently destroyed here without reaching the registry. Today all callers that pass `inputMaps={}` also pass `finalAccumulator=nullptr` (the K==0 empty-window fallback), and the P>1 path only creates non-empty `inputMaps` when `partitionTuples > 0` (which implies `finalAccumulator` is non-null). So no actual leak exists. However, for defense-in-depth, consider an INVARIANT:

```cpp
INVARIANT(finalAccumulator || inputMaps.empty(),
    "emitChunk: inputMaps non-empty but finalAccumulator is null — maps would be silently leaked");
```

### M4 — `makeStoreWithPartitions` fixture helper not available in `SpillableTimeBasedSliceStoreConcurrencyTest`

**File:** `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp:211`

The concurrency test constructs the store directly with `numberOfPartitions` defaulting to 1 (not passed). This is correct for the existing concurrency test (which is a P=1 regression test). But there is no concurrency test for P>1 (`claimWindowForPartitionedEmit` running concurrently with the background spiller). The concurrency reviewer (2d-concurrency-REVIEW.md) may flag this; noted here for completeness.

---

## Low

### L1 — `streamWindowPartition` creates a new `bufferProvider` per call (P times per window)

**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:353`

`auto provider = bufferProvider.copy()` is called once per `streamWindowPartition` invocation, i.e., P times per window in `triggerAllWindows`. The `copy()` is cheap (a shared pointer or similar), so this is not a performance problem, but it is semantically redundant — a single provider for the whole window would suffice. No correctness issue; the pattern mirrors `ensureWindowSlicesResident` which also calls `copy()` once per window.

### L2 — `triggerAllWindows` `PendingChunk` struct defined inside a loop scope

**File:** `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:288`

The `PendingChunk` struct is defined inside the `for (auto& [windowInfo, slices] : windows)` loop body. It is re-defined on each iteration (harmless since each definition is identical). The struct should be moved above the loop or made a private nested type of `AggregationOperatorHandler`. Minor readability issue; no functional impact since the struct has no static members.

### L3 — Comment in `SpillableTimeBasedSliceStore.hpp` for `claimWindowForPartitionedEmit` says "UNLIKE ensureWindowSlicesResident, this does NOT unspill" but the K==0 fallback in `triggerAllWindows` then calls `ensureWindowSlicesResident`

**File:** `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` (header comment for `claimWindowForPartitionedEmit`)

Technically accurate — `claimWindowForPartitionedEmit` itself does not unspill. The subsequent `ensureWindowSlicesResident` call in the K==0 path is in the handler, not in `claimWindowForPartitionedEmit`. No code bug; just a potential reader surprise. A note like "the K==0 fallback in triggerAllWindows calls ensureWindowSlicesResident separately after claim" would clarify.

---

## Nits

- `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:359`: `ChunkNumber(ChunkNumber::INITIAL + j)` is correct but relies on `ChunkNumber::INITIAL` being a raw `uint64_t` constant (it is, via `constexpr static T INITIAL = initial`). A comment "ChunkNumber::INITIAL is a raw uint64_t; + j is plain integer arithmetic before the explicit ctor" would help future readers who might expect `NESStrongType::operator+`.
- `nes-physical-operators/tests/AggregationOperatorHandlerRegistryTest.cpp:620`: The comment "mirrors SpillableTimeBasedSliceStoreTest.cpp:897-898" is a line-number reference that will drift as tests are added. Consider referencing the test name instead.
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp:1143`: The test asserts `ASSERT_EXCEPTION_ERRORCODE(store->forceSpill(residentSlice->getSliceEnd()), ErrorCode::SpillStoreFailure)` after `claimWindowForPartitionedEmit`. Good fencing proof. Consider also asserting `store->forceSpill(spilledSlice->getSliceEnd())` throws (spilled+emitted should also be refused). Currently only the resident slice is tested.

---

## Test-Coverage Assessment

| Test | Proves | Gap |
|---|---|---|
| `SpillConfigTests::numberOfPartitionsParsesSetValue` | knob parses | None |
| `SpillConfigTests::numberOfPartitionsEmbeddedCommandLineOverride` | nested dotted key | None |
| `SpillableAggregationSliceTest::materializeResidentPartitionUnionEqualsResidentAndStaysResident` | disjoint+complete union, slice unchanged | None |
| `SpillableAggregationSliceTest::materializeResidentPartitionEmptyPartitionProducesNoMap` | skip-empty on resident | None |
| `SpillableAggregationSliceTest::materializeResidentPartitionMatchesSpilledStream` | resident and spilled paths produce identical partitions | None |
| `SpillableTimeBasedSliceStoreTest::partitionedEmitUnionEqualsWindowMixedResidentAndSpilled` | union == full window (resident+spilled slices) | **Does NOT prove P-way partitioning** (forceSpill writes P=1 blob; all data lands in partition=0; other P-1 partitions are trivially empty) |
| `AggregationOperatorHandlerRegistryTest::partitionedTriggerEmitsKContiguousChunksAndSkipsEmpty` | K<P chunks, contiguous renumber, lastChunk, union, leak-totality | **Does NOT prove true skip-empty via hash distribution** (forceSpill P=1 → only partition=0 non-empty; K=1 satisfies pigeonhole trivially) |
| K==0 fallback in `triggerAllWindows` | (no test) | No test for P>1 all-empty window; the code comment says "does not arise for a triggered window" — acceptable |

**Primary gap:** both 2d store and handler tests should be updated to use `spillable->spill(*backend, P)` directly (bypassing the store-level call) so the blob layout matches the emit-time partition count. Without H1 fixed (or the tests using direct spill), neither test proves P-way partitioning.

---

## Compliments

- The `emitChunk` extraction is clean and genuinely behavior-identical to the pre-2d `triggerSlices` emit core — same buffer sizing formula, same zero-fill, same six `setX` calls, same `registerChunk` + placement-new shape. The diff is straightforward to verify.
- `claimSliceForEmit` extraction into a shared helper is the right call. Keeping the concurrency-sensitive claim in exactly one place reduces the maintenance surface for the most dangerous part of the store.
- `materializeResidentPartition` correctly mirrors `partitionedSerialize`'s `hash % P` routing (verified against the spill path), and the `PRECONDITION(resident, ...)` guard is appropriate. The INVARIANT on `insertEntry` returning null is correct (resource exhaustion on a constrained device is a process-fatal condition, not user input).
- REVISED L3 skip-empty + contiguous-renumber is correctly implemented: Pass 1 collects only non-empty `PendingChunk`s, Pass 2 emits K chunks with `ChunkNumber(INITIAL + j)` and `lastChunk = (j == K-1)`. The K==0 empty fallback correctly delegates to P=1-style emit.
- `static_assert(std::is_trivially_destructible_v<EmittedAggregationWindow>)` is an excellent guardrail that prevents future accidentally-owning members from silently introducing the original leak.
- The ownership chain (partition maps → `registerChunk` inputs → handler registry → `releaseChunk`/`stop()` sweep) is L8-compliant and provably total.
