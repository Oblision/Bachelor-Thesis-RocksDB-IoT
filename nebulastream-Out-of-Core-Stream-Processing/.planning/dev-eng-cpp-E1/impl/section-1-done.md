# E1-PR1 Done Note

## Summary

Closes the partitioned-restore instrumentation gap: `getMetrics().bytesRestored` and
`restoreSampleCount` now reflect the partition blob read-back cost on the headline P>1
terminal-emit path (`streamWindowPartition` driving `streamEmitByPartition`).

---

## Files Changed

### 1. `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp`

**Function:** `streamEmitByPartition` declaration  
**Change:** Return type changed `void` → `uint64_t`.  
Removed `[[nodiscard]]` (the return is optional for callers that only care about the
emit side-effect, e.g. existing slice-level tests — adding `[[nodiscard]]` would produce
compiler warnings on the three discard call-sites in `SpillableAggregationSliceTest.cpp`,
which we are not permitted to modify).  
Added doc comment explaining the returned value = Σ `record.size()` for every present
partition blob across all workers; absent blobs (skip-empty) contribute 0.

### 2. `nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp`

**Function:** `streamEmitByPartition`  
**Change:** Added a `uint64_t bytesRead = 0` accumulator in the worker loop. For each
present partition blob (`backend.get()` returns a value), `bytesRead += record->size()`
is computed **before** `SliceSerializer::deserialize(*record, bufferProvider)` consumes
(moves from) the record. The function returns `bytesRead`. Also updated the `NES_TRACE`
log to include `bytesRead={}` so the slice-level read cost is visible at TRACE.

**Why before deserialize:** `record` is `std::vector<std::byte>` returned by value; once
moved into `deserialize` it is empty. We size it first to avoid a race with the move.

### 3. `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`

**Function:** `streamWindowPartition` (the `else` branch for spilled slices)  
**Change:**  
- Captures `std::chrono::steady_clock::now()` immediately before the
  `streamEmitByPartition` call (`streamStart`).
- Stores the returned `bytesRead`.
- Computes `streamLatency = now() - streamStart` immediately after.
- If `bytesRead > 0`: logs at `NES_DEBUG` (site-level) and calls
  `recordUnspillSample(streamLatency, bytesRead)`.

**Lock discipline (LOCKED #6):** `streamWindowPartition` runs OUTSIDE `evictionMutex`
by design — `claimWindowForPartitionedEmit` releases it before returning and the
PRECONDITION comment documents this. The timing therefore satisfies the "no I/O or timing
under the eviction mutex" invariant, consistent with the existing `recordSpillSample` /
`recordUnspillSample` sites.

**Why skip bytesRead == 0:** An all-empty partition (every worker's blob was absent at
spill time via SKIP-EMPTY) does no backend I/O; recording a zero-byte, near-zero-latency
sample would inflate `restoreSampleCount` without useful signal. The guard keeps the count
meaningful.

### 4. `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`

Two new tests added before `defaultStoreReportsSinglePartition`:

**`partitionedStreamingReadRecordsRestoreSample`** (the RED test):  
- Constructs a P=4 store.
- Populates a slice on 2 workers (20 + 15 keys; hash distribution guarantees multiple
  non-empty partition blobs).
- Spills P-way via `forceSpill`.
- Snapshots `bytesRestored` and `restoreSampleCount` (both 0 on baseline).
- Claims the window (`claimWindowForPartitionedEmit`) then streams all P partitions via
  `streamWindowPartition`.
- Asserts residency invariant: slice stays spilled throughout (observation-only).
- PRIMARY assertions (RED on baseline, GREEN after fix):
  - `m1.bytesRestored > bytesRestoredBefore`
  - `m1.restoreSampleCount > sampleCountBefore`
- SECONDARY plausibility:
  - `bytesRestored > 0`
  - `bytesRestored <= bytesSpilled`
  - `restoreLatencyMaxUs >= 0`

**`partitionedStreamingReadDoesNotAffectPOneRestoreCount`** (LOCKED P=1 regression):  
- Constructs a P=1 store, spills a slice, triggers it (existing unspill path).
- Asserts `restoreSampleCount == 1` (exactly one restore sample from the unspill path, no
  extra from any accidentally-applied P>1 instrumentation).

---

## O2 Design Decision

**Chose Option (a):** have `streamEmitByPartition` return the bytes it read.

Rationale:
- The slice sees the `record` values and their sizes at the exact point of the backend
  `get()` call. The store does not know which workers have present blobs, nor their sizes,
  without re-querying the backend.
- Option (b) (store pre-sums blob sizes) would require the store to `get()` once to check
  presence/size and again inside `streamEmitByPartition` to read — a double round-trip
  with more complex logic.
- The signature change is minimal (return type only, no new parameters), and the store
  remains the sole owner of `SpillMetrics` / `recordUnspillSample`.

---

## `logMetrics()` Grammar

**Unchanged.** The one-liner format remains:

```
Spill metrics: spilled {N} slices / {B} bytes, restored {N} slices / {B} bytes, peak resident {B} bytes; spill latency us p50={} p95={} p99={} max={} (n={}); restore latency us p50={} p95={} p99={} max={} (n={})
```

Reusing `recordUnspillSample()` means the partitioned read-back cost flows into the
existing `slicesUnspilled`, `bytesRestored`, and `restoreLatenciesUs` fields — no new
fields, no reordering. The harness grep (`run-rss-part.sh`) is unaffected.

**Semantic note (honesty for the reviewer):** `slicesUnspilled` is now a slight misnomer
on the P>1 path — it actually counts "restore events" (one per non-empty partition blob
read-back), not "slices fully unspilled". The field's docstring in the header already
says "Σ resident bytes re-materialized by unspills"; the partitioned read-back does not
fully re-materialize the slice (it reads 1/P), so the count reflects read events rather
than full unspills. This is documented in-code at the `recordUnspillSample` call site. If
the reviewer prefers a distinct counter (`partitionBlobsRead` / `bytesStreamRead`), that
is a straightforward extension — see Risk note below.

---

## Risks for Reviewer

1. **`slicesUnspilled` semantic drift:** As noted above, on P>1 the "slices unspilled"
   count conflates full unspills with per-partition streaming reads. If the eval harness
   requires a strict separation, a new `SpillMetrics` field (e.g. `partitionReadsCount` /
   `bytesStreamedFromPartitions`) should be added. Adding it is additive: append to
   `SpillMetrics`, add a new atomic, extend the `getMetrics()` snapshot, and append to
   the `logMetrics()` one-liner. This was judged lower-risk than changing the field
   grammar mid-sprint.

2. **Empty-partition skip:** Partitions where every worker's blob was absent (SKIP-EMPTY
   at spill time) record no sample. This means `restoreSampleCount` after streaming all P
   partitions may be < P if some partitions are empty. The test uses enough keys (20+15)
   across P=4 that at least one non-empty partition per stream call is guaranteed by the
   hash distribution — but the test does NOT assert `restoreSampleCount == P`, only
   `restoreSampleCount > 0` (strictly: `> sampleCountBefore`). This is intentional.

3. **No lock verification at compile time:** The "OUTSIDE evictionMutex" invariant is
   documented in the comment but not statically enforced. The reviewer should confirm that
   `streamWindowPartition`'s call chain from `claimWindowForPartitionedEmit` does not
   re-acquire `evictionMutex` between the claim return and the `streamEmitByPartition`
   call. Reading the code: `claimWindowForPartitionedEmit` acquires and releases
   `evictionMutex` inside `claimSliceForEmit` for each slice; by the time control returns
   to `streamWindowPartition`, the mutex is free. The timing code wraps only the
   `streamEmitByPartition` call, which holds no lock.
