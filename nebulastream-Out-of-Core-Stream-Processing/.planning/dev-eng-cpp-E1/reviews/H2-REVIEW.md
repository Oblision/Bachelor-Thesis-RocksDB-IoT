# H2 Write-Stall Metric — Code Review

**Reviewer:** code-reviewer agent  
**Date:** 2026-05-25  
**Scope:** uncommitted working-tree diff for H2 instrumentation across four files  
**Build baseline:** 896/896 (already green, confirmed by caller)  
**LOCKED constraints source:** `.planning/dev-eng-cpp-E1/DESIGN-BRIEF.md`

---

## Verdict: APPROVE

No CRITICAL or HIGH issues. Two MEDIUM observations are flagged below; both are
minor and do not block the experiment. One LOW note on test semantics.

---

## Check-by-check findings

### 1. API Correctness

**`statistics->getTickerCount(rocksdb::STALL_MICROS)`**
- `STALL_MICROS` exists in `rocksdb/statistics.h` line 203 with the comment
  "Writer has to wait for compaction or flush to finish." — correct ticker.
- `getTickerCount` returns `uint64_t` and is assigned to
  `BackendStats::writeStallMicros` (`std::uint64_t`). Types match; no truncation.
- Cumulative semantics are correct for H2: the harness computes
  `stall_fraction = writeStallMicros / (elapsed_s * 1_000_000)` as documented.
  Calling `getBackendStats()` once at query stop reads the full cumulative total
  since DB open — this is the right moment and the right granularity.

**`statistics->histogramData(rocksdb::WRITE_STALL, &stallHist)`**
- `WRITE_STALL` exists in `rocksdb/statistics.h` line 591 with no stats-level
  annotation (confirmed: the `kExceptDetailedTimers` restriction applies only to
  the `FILE_READ_*` group at lines 599-606).
- `rocksdb::HistogramData stallHist{}` is value-initialized (C++ aggregate
  zero-initialization), so `stallHist.count` is 0 before the call. If
  `histogramData()` is a no-op on a fresh-stall-free DB, the field correctly
  stays 0.
- `HistogramData::count` is `uint64_t` (statistics.h line 712); assignment to
  `BackendStats::writeStallCount` (`std::uint64_t`) is exact. No truncation.

**Null-safety**
- The existing early-return guard at `RocksDBSpillBackend.cpp:227-230`
  (`if (!statistics) return std::nullopt;`) covers all new code. The H2 additions
  live after that guard and cannot reach a null `statistics` pointer. Correct.

**Cumulative semantics for "stall fraction"**
- Both `STALL_MICROS` (ticker) and `WRITE_STALL` (histogram count) are monotone
  non-decreasing for the lifetime of the DB object and reset to 0 only when the
  statistics object is recreated (i.e., on next DB open). Reading them once at
  query stop yields the total for that query run. This is what the harness needs.

### 2. Stats level — STALL_MICROS and WRITE_STALL at kExceptDetailedTimers

**Confirmed correct.** Neither `STALL_MICROS` nor `WRITE_STALL` carry a
"requires kAll" or "requires stats level greater than kExceptDetailedTimers"
annotation in the installed `statistics.h`. The only ticker with that restriction
is `DB_MUTEX_WAIT_MICROS` (line 205-206). The only histograms with that
restriction are `FILE_READ_GET_MICROS` and its four siblings (lines 599-606).

The stats level was NOT raised (confirmed: constructor at line 142 calls
`rocksdb::CreateDBStatistics()` with the default `kExceptDetailedTimers`;
no `set_stats_level(kAll)` call anywhere in the diff). LOCKED constraint #6
("do NOT perturb the eviction-mutex hold time") is satisfied.

**Risk of silently reading 0:** If a future RocksDB version moves `WRITE_STALL`
to a higher stats level, `writeStallCount` would silently read 0 while
`writeStallMicros` remains correct (since `STALL_MICROS` is a plain ticker with
no such annotation). The field comments document the level assumption; a
maintainer can re-verify by grepping `statistics.h`. This is acceptable for the
thesis eval scope.

### 3. Const-correctness; hot-path / eviction-mutex impact

- `getBackendStats()` is `const` (declared `const override` on the virtual in
  `SpillBackend.hpp`). Both `getTickerCount` and `histogramData` are `const`
  methods of `rocksdb::Statistics`. Const-correctness is preserved throughout.
- `getBackendStats()` is called from `logMetrics()`, which is called at query
  stop (i.e., outside the steady-state write path and outside the eviction mutex
  hold time). LOCKED constraint #6 is not violated.
- `histogramData()` is a struct copy of six doubles and two `uint64_t` fields —
  O(1), no I/O. No concern.

### 4. logMetrics line — field order and grammar

The new fields are appended after `block_cache_miss={}` in both the format
string and the argument list:

```
"... block_cache_hit={} block_cache_miss={} write_stall_micros={} write_stall_count={}"
```

Format-string argument count (9 format specifiers) matches the 9 positional
arguments passed. No mismatch. All prior fields remain in their original
positions. The shell harness sed pattern for existing fields is not broken.
New fields use `{}` (default unsigned integer formatting for `uint64_t`);
`write_stall_micros` does not use `{:.4f}` — correct since it is not a
floating-point value.

### 5. Tests

**`writeStallFieldsPresentAfterWrites`**
- Asserts `stats.has_value()` — correct.
- Asserts `writeStallMicros >= 0U` and `writeStallCount >= 0U` — always true
  by type (`uint64_t`), but documents the field-presence contract. Acceptable.
- Duration-count consistency check (`micros == 0 → count == 0` and vice versa)
  is logically sound because `STALL_MICROS` and `WRITE_STALL` are both
  incremented by the same RocksDB write-stall code path. Not flaky under the
  no-stall unit-test scenario (both will be 0).

**`writeStallFieldsAreNonDecreasingAcrossMorePuts`**
- Two-batch snapshot monotonicity test. Not flaky: RocksDB tickers/histograms
  never decrement within a DB lifetime. If no stalls occur both snapshots will
  be 0 and `GE(0, 0)` passes.

**`writeStallFieldsAbsentForNullAndInMemoryBackends`**
- Structurally identical to the existing `nullAndInMemoryBackendsReturnNulloptStats`
  test, named for H2 to make regression intent explicit. Correct and not flaky.
  The nullopt contract is exercised via the base-class default implementation
  which returns `std::nullopt` unconditionally.

No test asserts that stalls are non-zero (which would be permanently broken in
a unit-test environment). The test suite is robust.

### 6. Standard C++ — RAII, exception-safety, UB

- `rocksdb::HistogramData stallHist{}` is a local on the stack; no dynamic
  allocation. Aggregate value-initialization is standard and correct.
- No raw `new`/`delete` introduced.
- `getBackendStats()` is `noexcept`-agnostic: `getTickerCount` and
  `histogramData` do not throw; if they did, the function would propagate the
  exception cleanly (no resource to leak). The caller `logMetrics()` does not
  use the result after potential exception, so no use-after-free.
- No UB found.

---

## Findings by Severity

### MEDIUM — M1: `EXPECT_GE` on unsigned 0 is vacuously true and provides no signal
**File:** `nes-physical-operators/tests/RocksDBSpillBackendTest.cpp` lines ~497-500  
**Detail:** `EXPECT_GE(stats->writeStallMicros, 0U)` and
`EXPECT_GE(stats->writeStallCount, 0U)` can never fail because both fields are
`std::uint64_t`. The test comment correctly acknowledges this ("unsigned — always
true") but the assertion still adds noise to the test report. The meaningful
signal comes from `ASSERT_TRUE(stats.has_value())` and the duration-count
consistency check, which do provide genuine discriminating power.  
**Recommendation:** Replace the two `EXPECT_GE(..., 0U)` assertions with a
comment-only explanation, or replace with a compile-time `static_assert` on the
field type in the header to document the unsigned contract without polluting the
runtime test log. Not a correctness issue; does not affect green/red status.

### MEDIUM — M2: Duration-count consistency assumes STALL_MICROS and WRITE_STALL are always co-incremented
**File:** `nes-physical-operators/tests/RocksDBSpillBackendTest.cpp` lines ~511-520  
**Detail:** The test asserts `(writeStallMicros == 0) ↔ (writeStallCount == 0)`.
This holds if and only if every stall event contributes to both the
`STALL_MICROS` ticker and the `WRITE_STALL` histogram simultaneously. In the
installed RocksDB version this is true. However, a delayed-write stall (throttled
rate, not full stop) may record duration in `STALL_MICROS` but does not
necessarily produce a histogram sample in all RocksDB versions — the histogram
records intervals, whereas the ticker accumulates any blocked microseconds
including from the throttle path. If both fire in the same code path (as in the
installed build), the invariant holds. If they diverge in a future version, this
test will produce a spurious failure even though the individual fields are
correct.  
**Recommendation:** Document the co-increment assumption in the test comment
explicitly ("verified in RocksDB ≥ X.Y: both are incremented by the same
write-stall code path"). Consider weakening the bi-directional implication to the
one-way direction that is strictly provable: `(count > 0) → (micros > 0)` (you
cannot have stall events with zero stall time). The reverse (`micros > 0 →
count > 0`) is empirically true for full-stop stalls but less certain for
throttle-only scenarios. This is a low-probability concern for the thesis eval
scope.

### LOW — L1: `NUM_RECORDS = 30` is an arbitrary small batch with no relation to flush thresholds
**File:** `nes-physical-operators/tests/RocksDBSpillBackendTest.cpp` line ~470  
**Detail:** The test writes 30 records of 4 bytes each (120 bytes total), far
below any memtable flush threshold. This is intentional for speed, but a brief
comment explaining why the small batch is sufficient (field-presence check, not
stall-elicitation) would aid future maintainers who might otherwise wonder if
the batch should be larger.  
**Recommendation:** Add one-line comment: `// Small batch — enough to exercise the stat read; stalls require sustained overload (harness only).`

---

## LOCKED Constraint Compliance

| Constraint | Status |
|------------|--------|
| #1 No JIT/Nautilus edits | Pass — only physical-operators touched |
| #2 No sliding windows | Pass — no window changes |
| #3 Build/test in Docker | Pass — no build changes needed from this diff |
| #4 Regression >= 875 | Pass — 3 new tests added, none remove existing ones |
| #5 P=1 byte-identical behavior | Pass — observation-only, no data path changes |
| #6 No eviction-mutex perturbation | Pass — getBackendStats() called at query stop, outside the lock |
| #7 Production-visible stable one-liner | Pass — `RocksDB stats:` line extended by appending at end |
| #8 Manual push / no co-author | N/A for code review |
| #9 .planning gitignored | N/A for code review |

---

## Summary

The H2 instrumentation is correct, minimal, and well-reasoned. The RocksDB API
names (`STALL_MICROS`, `WRITE_STALL`) are confirmed against the installed
`statistics.h`. Stats level is not raised. The eviction-mutex hot path is not
touched. The log-line grammar is stable. Tests are not flaky. Both MEDIUM
findings are style/robustness notes that do not affect correctness or the eval
harness.
