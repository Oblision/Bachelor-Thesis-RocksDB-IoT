# Phase 0b Review — Observability Instrumentation

**Reviewer:** Claude (code-reviewer agent)
**Verdict:** APPROVE WITH NITS
**Diff base:** `9b5894f71e`
**Files reviewed:**
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`

---

## Summary

No CRITICAL or HIGH issues. The instrumentation is structurally sound: lock ordering is correct (sample-vector wlocks are always acquired outside `evictionMutex`), the CAS-max loop is lock-free-correct, percentile arithmetic is correct, and the `getMetrics()` copy-then-sort pattern avoids sorting the live vector. Four MEDIUM findings and a handful of nits, all easily addressable before landing.

---

## Findings

### CRITICAL — None

### HIGH — None

### MEDIUM

---

#### M1 — Inconsistent memory ordering on `spillCount` increment in `recordSpillSample`
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:832`

`recordSpillSample` uses `++spillCount` (which is `fetch_add` with `std::memory_order_seq_cst` on `std::atomic<std::size_t>`), while every other new counter (`bytesSpilled`, `slicesUnspilled`, `bytesRestored`) uses explicit `memory_order_relaxed`. The comment on `spillCount` in the header states the ordering is intentional and explicitly warns against strengthening to `seq_cst`. The implicit `seq_cst` on `++spillCount` does not break correctness here, but it is an inconsistency that:

1. Silently contradicts the "relaxed — this is a monotone statistic" philosophy documented on the sibling counters.
2. Imposes an unnecessary full memory fence on every eviction-path spill.

**Fix:** replace `++spillCount` in `recordSpillSample` with `spillCount.fetch_add(1, std::memory_order_relaxed)` (same form used for the other counters and consistent with the existing header comment on `lastSeenWatermark`).

---

#### M2 — `peakResidentBytes` CAS-max inside `residentBytes()` while `evictionMutex` is held
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:800–803`

`residentBytes()` acquires `evictionMutex` (a `std::mutex`) at line 781 and holds it for the entire `createdSlices` scan. The CAS-max loop for `peakResidentBytes` runs at lines 800–803 while that lock is still held. The `peakResidentBytes` atomic is `mutable` and documented as "relaxed — a monotone high-water mark", which is correct for the atomic itself. The issue is that the peak-update code executes inside the `evictionMutex` section for no reason — the `sum` is fully computed before line 800, so the CAS loop could run after releasing the mutex. In the current design this does not introduce a deadlock (the CAS loop takes no further locks), but it unnecessarily lengthens `evictionMutex` hold time, adding latency for all concurrent spiller/worker threads that contend on `evictionMutex`.

**Fix:** move the CAS-max block to after `return sum;`... actually, since `sum` is a local, restructure `residentBytes()` so the `unique_lock` releases before the CAS loop:

```cpp
uint64_t sum = 0;
{
    std::unique_lock lock(evictionMutex);
    createdSlices.withRLock([&](const auto& tracked) {
        for (const auto& [end, weak] : tracked) {
            if (spillInProgress.contains(end)) { continue; }
            if (const auto slice = weak.lock()) { sum += residentBytesOf(*slice); }
        }
    });
} // release evictionMutex here
// Phase 0b: CAS-max outside the lock — no nesting with evictionMutex.
auto observedPeak = peakResidentBytes.load(std::memory_order_relaxed);
while (sum > observedPeak
       && !peakResidentBytes.compare_exchange_weak(observedPeak, sum, std::memory_order_relaxed)) {}
return sum;
```

This keeps the lock-ordering and relaxed-ordering justifications intact while reducing contention.

---

#### M3 — `getMetrics()` reads atomic counters with `memory_order_relaxed` AFTER releasing the sample-vector rlock, creating a torn snapshot
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:850–871`

`getMetrics()` takes an rlock on `spillLatenciesUs`, copies the vector, releases it, then calls `spillCount.load()` with the default ordering (`seq_cst` via the implicit load). Later it reads `bytesSpilled`, `bytesRestored`, `slicesUnspilled`, and `peakResidentBytes` all with `memory_order_relaxed`. Because there is no synchronization fence between releasing the rlock and reading the atomics, a concurrent thread could have incremented `spillCount` and pushed a sample into `spillLatenciesUs` between the rlock release and the `spillCount.load()`. The result is `m.slicesSpilled > m.spillSampleCount` for an observer calling `getMetrics()` during an active spill burst, which is confusing but not a crash.

For a metrics-only snapshot this is arguably acceptable ("best-effort, not transactional"). However the **existing test** `metricsRecordSpillRestoreAndPeak` asserts `m1.spillSampleCount == 1U` and `m1.slicesSpilled == 1U` in a single-threaded context, so it cannot expose the race. The concern is real when called from the eval driver during a multi-threaded run.

**Fix (low cost):** document explicitly in the `getMetrics()` contract (comment) that the snapshot is non-transactional (counter reads may differ by ±1 from the sample-count reads during concurrent operations). Alternatively, read both the sample vector AND the paired atomic count under the same rlock scope:

```cpp
std::uint64_t spillCountSnapshot = 0;
auto spillSamples = spillLatenciesUs.withRLock([&](const auto& v) {
    spillCountSnapshot = spillCount.load(std::memory_order_acquire);
    return v; // copy
});
```

If strict consistency is not required, the documentation fix is sufficient.

---

#### M4 — `bytesRestored` semantics mismatch: recorded as post-unspill footprint, but `bytesSpilled` is pre-spill footprint
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:103, 113, 123` (unspill sites) vs. `cpp:133, 151` (spill sites)

At every spill site `residentBefore` is captured **before** `spill()` (correct — it measures the bytes freed). At every unspill site `residentBytesOf(*spillable)` is called **after** `unspill()` (returns the bytes now re-materialized). This is symmetric and actually correct for the "bytes freed vs. bytes restored" accounting. **However**, the test at line 316 asserts `m2.bytesRestored == m2.bytesSpilled`, which only holds when the slice's footprint is unchanged by the round-trip. If a partial unspill (e.g. sparse maps) or a different page-fill level were possible, the assertion would be brittle. More importantly, the header comment for `bytesRestored` says "Σ resident bytes re-materialized by unspills" — calling `residentBytesOf` after unspill is the correct measurement for that definition.

The real issue is: there is **no inline comment at the three unspill call sites** explaining why `residentBytesOf` is called AFTER `unspill()` (in contrast to the spill sites which have the `/// footprint freed by the spill (0 after spill())` comment). Without the comment, a future reader will assume this is a bug (measuring 0 for a still-spilled slice).

**Fix:** add a `/// footprint re-materialized by the unspill (non-zero only after unspill())` comment at each of the three `recordUnspillSample` call sites. No code change required.

---

### LOW

---

#### L1 — `spillLatenciesUs.rlock()` and `restoreLatenciesUs.rlock()` taken sequentially in `getMetrics()`, not held simultaneously
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:850–851`

```cpp
auto spillSamples  = *spillLatenciesUs.rlock();
auto restoreSamples = *restoreLatenciesUs.rlock();
```

Each rlock is taken and immediately released (the dereference-copy idiom). Between the two releases, a concurrent `recordSpillSample` could push a new sample into `spillLatenciesUs` and a concurrent `recordUnspillSample` could push into `restoreLatenciesUs`. For a metrics snapshot this is fine. The finding is that `m.spillSampleCount` and `m.restoreSampleCount` can each be stale relative to each other, but given the non-transactional nature of the snapshot (M3 above) this is a documentation issue, not a correctness issue.

No code change required; address alongside M3's documentation fix.

---

#### L2 — `percentileOfSorted` with `p == 0` returns `sorted[0]` (min), but `p100` returns `sorted[n-1]` (max); neither is tested directly
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:67`

For `p == 0`: `rank = ((0 * n) + 99) / 100 = 0` (integer division), then `clamp(0, 1, n) = 1` → `sorted[0]`. That is correct — the minimum. For `p == 100`: `rank = ((100 * n) + 99) / 100`. For `n = 1`: `rank = 199/100 = 1` → `sorted[0]`. For `n = 2`: `rank = 299/100 = 2` → `sorted[1]` (max). So for all `n >= 1` and `p = 100` this resolves to `sorted[n-1]`, which is the maximum. Correct.

The max is computed separately in `getMetrics()` as `spillSamples.back()`, which is redundant with `percentileOfSorted(spillSamples, 100)` but harmless. The real gap is there are no direct unit tests for `percentileOfSorted` with a known multi-element sample (see Test Coverage Gaps below). This is a nit rather than a bug.

---

### Nits

**N1 — `SpillMetrics` struct members could be grouped more readably**
`hpp:179`: `spillLatencyP50Us, spillLatencyP95Us, spillLatencyP99Us, spillLatencyMaxUs` are four fields on one line. Expanding to one field per line with a trailing comment improves readability and diff hygiene for future additions (p999, histogram buckets, etc.).

**N2 — `logMetrics()` format string: `"restored {} slices / {} bytes"` argument order reversal**
`cpp:879`: The log string reads `"restored {} slices / {} bytes"` and the arguments are `m.slicesUnspilled, m.bytesRestored` — correct. However `"spilled {} slices / {} bytes"` is followed immediately by `m.slicesSpilled, m.bytesSpilled` — also correct. This is fine. The nit is that the format string says `"peak resident {} bytes"` but formats `m.peakResidentBytes`, which is `uint64_t` — no mismatch. Nothing actionable; noting it was checked.

**N3 — `getMetrics()` silently conflates `slicesSpilled == spillCountForTest()`**
`cpp:856`: `m.slicesSpilled = spillCount.load()` (default `seq_cst`). The header comment says "`slicesSpilled` reuses the monotonic spillCount". This is intentional and documented. The nit is that `spillCountForTest()` now has a shadow in the public API (`SpillMetrics::slicesSpilled`). For test clarity, new tests should prefer `getMetrics().slicesSpilled` over `spillCountForTest()` since the former is the stable public surface. Not a bug.

**N4 — No `[[nodiscard]]` on `percentileOfSorted`**
`cpp:60` (anon namespace): since this is an anon-namespace free function and its result is always used, `[[nodiscard]]` would enforce correct usage if it ever escapes the anon namespace. Low value since it is already anon-scoped. Nit only.

---

## Test Coverage Gaps

1. **`percentileOfSorted` with a known multi-element sample**: No test calls `percentileOfSorted` (or `getMetrics()` percentile fields) with a synthetic sample of known values and asserts the exact p50/p95/p99 outputs. A test with, say, `{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}` and expected p50=5, p95=10, p99=10, max=10 would catch any off-by-one in the rank formula without having to infer correctness from the formula's math.

2. **Empty-metrics state for percentile fields**: `metricsRecordSpillRestoreAndPeak` checks `m0.spillSampleCount == 0` but does not assert that `m0.spillLatencyP50Us == 0` (et al.). The empty-vector guard in `percentileOfSorted` returns 0 for these, but there is no test asserting that. One line of `EXPECT_EQ(m0.spillLatencyP50Us, 0U)` covers it.

3. **Peak update on a spilled-then-unspilled cycle**: The test checks `m0.peakResidentBytes >= residentNow` but does not verify that `peakResidentBytes` does NOT decrease after the forceSpill (i.e., asserts `m1.peakResidentBytes == m0.peakResidentBytes` or `>= residentNow`). This would confirm the high-water-mark semantics are preserved across a spill.

4. **Multi-sample percentile plausibility**: With more than one spill and unspill (e.g., two `forceSpill` calls on two distinct slices), `spillSampleCount == 2` and `spillLatencyP50Us > 0` (ordering: first sample ≤ p50 ≤ second sample). Currently all tests exercise exactly one spill/unspill sample, so the sort+rank path with `n > 1` is untested.

5. **`logMetrics()` path via `stopSpiller()`**: No test explicitly calls `stopSpiller()` (or destroys the store while metrics are non-zero) and asserts NES_INFO was emitted (or at minimum that no crash occurs). This is a minor gap for the idempotent double-log guard (`joinable()` guard).

---

## Compliments

- The CAS-max loop for `peakResidentBytes` is textbook lock-free-correct: `compare_exchange_weak` reloads `observedPeak` on failure, the `sum > observedPeak` guard prevents the loop from running when no update is needed, and `memory_order_relaxed` is correctly justified as a monotone high-water mark that needs no acquire/release fence.
- The `getMetrics()` copy-then-sort pattern is the right approach: takes a brief rlock, copies the vector out, releases the lock, then sorts the copy. The live vector is never sorted in-place and the sort is never done under the lock.
- Lock ordering discipline is fully respected: every `recordSpillSample` / `recordUnspillSample` call site is outside `evictionMutex` (verified against all five instrumented sites in the diff), consistent with LOCKED constraint #1.
- `logMetrics()` is correctly gated on `spiller.joinable()` (once) so the dtor + `deleteState()` double-call pattern cannot double-log. Clean idempotency.
- The `static_cast<std::uint64_t>(duration_cast<microseconds>(latency).count())` conversion is correct: `duration::rep` for `microseconds` is `int64_t` on all major platforms, and the cast to `uint64_t` is safe for non-negative durations (which `steady_clock::now() - earlier` always produces for a forward-moving clock).
- `<algorithm>` is correctly added to the `.cpp` include list for `std::sort` and `std::clamp`.
- The `[[nodiscard]]` attribute on `getMetrics()` is present and appropriate.
- Percentile formula (`ceil(p*n/100)` via integer arithmetic `(p*n + 99)/100`, clamped to `[1,n]`) is the standard nearest-rank method and produces correct results for all tested percentile values (p50, p95, p99) and edge cases (empty vector, n=1).
