# Code-Review Fixes Applied — E1 PR1/PR2/PR3

Date: 2026-05-25
Reviewer source: `.planning/dev-eng-cpp-E1/reviews/PR{1,2,3}-REVIEW.md`
No build/test run (Docker-only constraint honored).

---

## FIX 1 — PR3 M1: Reset listener accumulators after emit (CORRECTNESS)

**Problem:** `logAndEmit()` copied `durationsUs` under the lock but never cleared it, causing
cross-query accumulation when the engine runs multiple sequential queries.

**Change:** In `logAndEmit()`, combined the snapshot copy and both clears into ONE lock scope:
```cpp
const std::lock_guard lock(mutex);
snapshot = durationsUs;
durationsUs.clear();
pendingStarts.clear();
```
This takes a snapshot, clears both accumulators, then releases the lock — no double-lock, no
deadlock risk. The emitted values reflect only the just-finished query; the next query starts fresh.

**Files touched:**
- `nes-query-engine/PercentileTaskLatencyListener.cpp` — `logAndEmit()` (~lines 127-145)

**Test added:**
- `nes-query-engine/tests/PercentileTaskLatencyListenerTest.cpp` — new test
  `TwoSequentialCycles_DoNotAccumulate`: feeds cycle-1 (1 task × 1000 µs), triggers QueryStop,
  then feeds cycle-2 (2 tasks × 500 µs each), verifies via `computePercentiles` seam that cycle-2
  sees n=2 and max=500 (not n=3 or max=1000 from cross-query bleed), then triggers a second QueryStop.

---

## FIX 2 — PR1 M1: Stop inflating `slicesUnspilled` on the streaming path (THESIS-NUMBER CORRECTNESS)

**Problem:** `streamWindowPartition` called `recordUnspillSample()` which increments `slicesUnspilled`,
making "restored N slices" count per-partition blob reads instead of full slice unspills. On a P=4
run of 1 slice, `slicesUnspilled` reported 4 instead of 0 (no full unspill occurred).

**Changes:**

1. `SpillMetrics` struct: added `partitionRestoreCount` field with doc comment explaining orthogonality.
   Updated `slicesUnspilled` doc comment to clarify "full unspills only". Updated `bytesRestored` comment.

2. Private `recordPartitionRestoreSample(latency, bytes)` method: increments `partitionRestoreCount`
   (NOT `slicesUnspilled`), accumulates `bytesRestored`, and pushes to `restoreLatenciesUs` — keeping
   restore-latency percentiles and `restoreSampleCount` meaningful across both paths.

3. `std::atomic<std::uint64_t> partitionRestoreCount{0}` added to private atomics section.

4. Streaming path in `streamWindowPartition`: changed call from `recordUnspillSample` to
   `recordPartitionRestoreSample` with an explanatory comment.

5. `getMetrics()`: populated `m.partitionRestoreCount` from the new atomic.

6. `logMetrics()`: existing `"restored {} slices / {} bytes"` substring preserved unchanged (harness
   grep stays valid). Appended ` partition_restores={}` at the END of the same Spill-metrics line.

**Files touched:**
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` — `SpillMetrics`
  struct fields + doc, `recordPartitionRestoreSample` declaration, `partitionRestoreCount` atomic member
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` — `recordPartitionRestoreSample`
  implementation, streaming path call-site, `getMetrics()`, `logMetrics()`

**Test updated:**
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` —
  `partitionedStreamingReadRecordsRestoreSample`: added assertions `partitionRestoreCount > 0`,
  `slicesUnspilled == 0` (pure streaming emit), and `restoreLatencyMaxUs > 0` (replaces the vacuous GE).
  Also updated the test's leading comment list to reflect the new assertions (items 5–6).

---

## FIX 3 — PR1 M3: Vacuous `EXPECT_GE(m1.restoreLatencyMaxUs, 0U)` assertion

**Problem:** `restoreLatencyMaxUs` is `std::uint64_t`; `>= 0` is always true, giving no protection
against the uninitialized/zero case.

**Change:** The original `EXPECT_GE(m1.restoreLatencyMaxUs, 0U)` line was replaced (as part of FIX 2's
test update) with `EXPECT_GT(m1.restoreLatencyMaxUs, 0U)` with a comment: "a backend I/O was performed".
This fires if the max latency remains at 0 when at least one non-trivial blob was read.

**Files touched:**
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (~line 1371, now part of FIX 2 block)

---

## FIX 4 — PR1 N1, N2: Nits

**N1 — Contradictory comment removed:**
In `streamWindowPartition` just before `if (bytesRead > 0)`, the two-sentence contradictory comment
("Record even if bytesRead == 0 … the instrumentation gap is closed. However, to keep … only record
when there were bytes") was replaced with a single accurate sentence matching the code: "Only record
when there were bytes to account for."

**Files touched:**
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` (~lines 403-407)

**N2 — Duplicate assertion removed:**
In `partitionedStreamingReadDoesNotAffectPOneRestoreCount`, the second consecutive
`EXPECT_EQ(m.restoreSampleCount, 1U)` was removed (the comment introducing it was also removed).

**Files touched:**
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (~line 1400)

---

## FIX 5 — PR2 doc: Comment accuracy in `SpillBackend.hpp` and `RocksDBSpillBackend.hpp`

**SpillBackend.hpp struct comment block:**
- Write-amp formula: replaced `max(1, bytesFlushed)` denominator notation with explicit text:
  "writeAmplification = (bytesFlushed + bytesCompacted) / bytesFlushed; reported as 0.0 when
  bytesFlushed == 0 (no-flush sentinel)". Also updated the inline field comment on
  `writeAmplification` to match.
- `bytesWritten` field comment: changed from "User bytes issued via Put/Write (BYTES_WRITTEN)" to
  "WAL-path bytes per Put/Write (includes key + RocksDB record overhead), not pure user payload".

**RocksDBSpillBackend.hpp:**
- Added two-line comment above the `statistics`/`db` member declarations explaining the destruction
  order invariant: "`statistics` declared before `db`: db must be destroyed first (its destructor may
  write stats during background-work drain; destroying statistics first would be use-after-free)."

**Files touched:**
- `nes-physical-operators/include/SliceStore/SpillBackend.hpp` (~lines 36-56)
- `nes-physical-operators/include/SliceStore/RocksDBSpillBackend.hpp` (~lines 74-79)

No behavior change. Documentation only.

---

## FIX 6 — PR3 M2 doc: taskId uniqueness comment

**File:** `nes-query-engine/include/PercentileTaskLatencyListener.hpp` (~line 76)

**Change:** The comment `// Key: (pipelineId, taskId) — identifies an in-flight task uniquely within a query.`
was corrected to:
```
// Key: (pipelineId, taskId) — identifies an in-flight task uniquely across the engine lifetime
// (taskId comes from a ThreadPool-monotonic counter that never resets between queries).
```
This matches the actual invariant (taskId is globally monotonic per engine instance, not per-query).

**Files touched:**
- `nes-query-engine/include/PercentileTaskLatencyListener.hpp`

No behavior change. Documentation only.

---

## Flags / Items Not Done Cleanly

None. All six fixes applied cleanly. Notes:

- **FIX 1 test** verifies the reset via the public `computePercentiles` seam and `EXPECT_NO_FATAL_FAILURE`
  on the second QueryStop. It cannot directly inspect internal state (private members), so the reset
  correctness is confirmed indirectly via the reference computation. This is the same test-seam discipline
  used by the existing tests.

- **FIX 2 logMetrics format change**: the harness grep `restored [0-9]+ slices / [0-9]+ bytes` is
  preserved verbatim; `partition_restores=N` is appended after `(n=<restoreSampleCount>)` so existing
  sed patterns are unaffected and the new field is at a stable, parseable tail position.

- **FIX 3** was subsumed into FIX 2's test block as the old `EXPECT_GE` line was replaced in the same
  hunk. No separate edit was needed.
