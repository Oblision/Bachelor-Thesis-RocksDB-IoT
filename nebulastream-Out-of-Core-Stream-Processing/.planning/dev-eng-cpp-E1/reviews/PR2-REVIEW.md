# E1-PR2 Code Review — RocksDB Write-Amp + Footprint Statistics

**Reviewer:** cpp-code-reviewer agent  
**Date:** 2026-05-25  
**Branch:** feat/ooc-phase1-spill-skeleton  
**Scope:** Uncommitted working-tree changes for PR2 only  
**Authority doc:** `.planning/dev-eng-cpp-E1/DESIGN-BRIEF.md` (LOCKED constraints respected)

---

## Verdict: APPROVE WITH ONE MEDIUM NOTE

No CRITICAL or HIGH issues found. One MEDIUM issue (write-amp semantic note for the
thesis), two LOW/INFO items. All LOCKED constraints respected. The implementation is
correct, safe, and production-ready for the eval harness.

---

## Issue Inventory

### MEDIUM — Stats Correctness: write-amp formula edge case underdocumented for thesis consumers

**File:** `nes-physical-operators/include/SliceStore/SpillBackend.hpp` (struct comment, line ~44–46)  
**File:** `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` (lines 248–251)

The formula `(bytesFlushed + bytesCompacted) / max(1, bytesFlushed)` is correct and
divide-by-zero is properly guarded (reports 0.0 when bytesFlushed == 0). However the
struct comment documents that `max(1, bytesFlushed)` is used in the denominator, while
the code uses the conditional branch `if (bytesFlushed > 0)` and leaves `writeAmplification`
at its default `0.0` when the condition is false. These are semantically equivalent but
textually inconsistent: the comment implies `max(1, bytesFlushed)` is always the live
denominator, which would yield 1.0 when bytesFlushed==0 (not 0.0). The comment should
read "if bytesFlushed == 0, reported as 0.0 (no-flush sentinel)" rather than the `max(1,…)`
notation. This is a documentation-only inconsistency that will confuse anyone comparing
the struct docstring to the microbenchmark's `parse_write_amp` column value and seeing `0.0`
vs an expected minimum of `1.0`. The thesis should call this out explicitly (section-2-done.md
already does, but the header comment does not).

**Additionally:** `BYTES_WRITTEN` in RocksDB counts WAL-path bytes (key + value +
internal RocksDB overhead including the sequence number and record type) — it is *not*
pure user payload bytes. This is correctly noted in section-2-done.md ("may include metadata
overhead") but the struct field comment (`///< User bytes issued via Put/Write`) implies
pure user payload. This is acceptable for the thesis's use case (monotonicity is the
property tested, not exact byte accounting), but the comment should read "WAL-path bytes
per Put/Write (includes key encoding overhead)" to prevent misreading in future diff passes.

**Fix:** Documentation-only. No code change required.

---

### LOW — Destruction order: statistics before db is correct but should be commented

**File:** `nes-physical-operators/include/SliceStore/RocksDBSpillBackend.hpp` (lines 78–79)

```cpp
std::shared_ptr<rocksdb::Statistics> statistics;   // line 78 — declared first
std::unique_ptr<rocksdb::DB> db;                   // line 79 — declared second
```

C++ destroys members in **reverse declaration order**: `db` is destroyed first, then
`statistics`. This is the correct order — `rocksdb::DB::~DB()` may flush background work
that writes to the Statistics object; destroying Statistics before DB would be a
use-after-free in that internal RocksDB flush path.

The current declaration order (statistics first, db second) produces the correct
destruction sequence (db first, statistics second) by the language rule. However, there
is no comment explaining why the order matters. If a future maintainer swaps the two
lines for cosmetic "alphabetical" reasons, the destruction order silently inverts.

**Fix:** Add a one-line comment: `// Must be declared before db so that db is destroyed
first (RocksDB internals may write tickers during DB::Close).`

**Severity:** LOW — currently safe; risk is from future editing without context.

---

### INFO — const-correctness of GetIntProperty on a non-const DB*

**File:** `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` (line 260)

`getBackendStats()` is `const` on `RocksDBSpillBackend`. The `db` member is
`std::unique_ptr<rocksdb::DB>`. Inside a `const` method, `db` is
`const std::unique_ptr<rocksdb::DB>&`, whose `operator->()` returns the stored raw
pointer as `rocksdb::DB*` (non-const) — `unique_ptr::operator->() const` returns `T*`,
not `const T*`. Therefore `db->GetIntProperty(...)` calls `GetIntProperty` on a
`rocksdb::DB*`, which is valid regardless of the const qualifier on `GetIntProperty` in
the RocksDB header.

The section-2-done.md pre-review correctly identifies this mechanism. No code change
needed. This INFO item is recorded for completeness.

---

### INFO — Statistics overhead: kExceptDetailedTimers is the right choice

**File:** `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` (lines 136–143)

`rocksdb::CreateDBStatistics()` defaults to `kExceptDetailedTimers`. This level collects
all byte/count tickers via atomic increments on the Put/Get hot path. The overhead is
real: each `Put` increments `BYTES_WRITTEN` and potentially `FLUSH_WRITE_BYTES` (on
flush completion). Measured overhead for `kExceptDetailedTimers` in RocksDB benchmarks
is typically 2–5% throughput reduction vs no-statistics.

For RQ2 latency measurements this is a concern: if the eval harness compares statistics-
enabled runs to baseline numbers (from the microbenchmark's 10k runs without statistics),
the comparison is not apples-to-apples. The RQ2 methodology should note that statistics
are always-on in E1 runs. Since `logMetrics()` is called only at query stop (not on the
hot path), the statistics object itself is read only once — that is fine. The overhead
is only at write time.

**Recommendation:** Document in the thesis eval section that E1 runs have statistics
enabled. Do not use `kNone` (that would defeat PR2's purpose); `kExceptDetailedTimers`
is the correct minimum-overhead choice that still collects all required tickers. No code
change needed.

---

## Detailed Analysis by Checklist Item

### 1. rocksdb::Statistics lifetime and ownership

**PASS.**

- `statistics` is a `shared_ptr<rocksdb::Statistics>` member of `RocksDBSpillBackend`.
- In the constructor, `statistics = rocksdb::CreateDBStatistics()` is called *before*
  `DB::Open()`. `options.statistics = statistics` copies the shared_ptr into the Options
  struct, which RocksDB's internal `DBImpl` then stores. Both `RocksDBSpillBackend` and
  the `DBImpl` hold a shared_ptr reference.
- Because `db` (the unique_ptr) is declared *after* `statistics` in the header, `db` is
  destroyed first: `DB::~DB()` / `DBImpl::~DBImpl()` releases the DBImpl's shared_ptr
  reference to Statistics, then the `RocksDBSpillBackend` destructor drops the member
  reference. Statistics is destroyed last — correct order.
- No null-deref possible on the `getBackendStats()` path: the `if (!statistics)` guard
  is belt-and-suspenders and can never trigger in practice (see constructor sequence).
- The `if (const auto status = rocksdb::DB::Open(…); !status.ok())` throw path is the
  only failure point. On that path, `db` is never set (it remains nullptr), `rawDb`
  is leaked **only if Open() partially writes rawDb then returns failure** — but this is
  the same pre-existing pattern in the codebase (not introduced by PR2). PR2 does not
  worsen this.

**One note (pre-existing, not PR2):** `rawDb` is declared before the throw; if `Open`
returns non-ok *and* sets `rawDb` to a non-null partial handle, that handle leaks. The
RocksDB contract says `rawDb` is only valid when `Open` returns OK — so in practice no
leak — but the pattern is fragile. This is pre-existing and out of PR2 scope.

### 2. Stats correctness

**PASS with documentation note (MEDIUM above).**

- Ticker names: `FLUSH_WRITE_BYTES`, `COMPACT_WRITE_BYTES`, `BYTES_WRITTEN`,
  `BLOCK_CACHE_HIT`, `BLOCK_CACHE_MISS` — all confirmed present in RocksDB's
  `statistics.h` (section-2-done.md cites the header lines).
- `GetIntProperty("rocksdb.total-sst-files-size", &sstSize)`: canonical API, confirmed
  in `db.h` line 1491–1492 and 1514 per section-2-done.md. Return value (bool) is
  checked; sstSize is only written to `stats.sstFootprintBytes` on success.
- Write-amp formula: correctly uses `bytesFlushed` as the denominator base (L0 entry
  bytes), not `bytesWritten` (WAL-path bytes). Minimum value when flushes occur is 1.0.
  The 0.0 sentinel when bytesFlushed==0 is semantically sound ("no flush, no meaningful
  write-amp measurement yet") and consistent with section-2-done.md's rationale.
- Divide-by-zero: fully guarded via the `if (bytesFlushed > 0)` branch.
- The struct comment says `max(1, bytesFlushed)` but the code does an explicit branch —
  these are equivalent but the comment is misleading (see MEDIUM issue).

### 3. const-correctness

**PASS** (see INFO item above for full analysis).

`getBackendStats() const` calls:
- `statistics->getTickerCount(...)`: `Statistics::getTickerCount` is declared `virtual
  uint64_t getTickerCount(uint32_t tickerType) const` in `statistics.h` — const-correct.
- `db->GetIntProperty(...)`: called via `unique_ptr::operator->() const` which returns
  `rocksdb::DB*` (non-const pointer to the object). `GetIntProperty` on `rocksdb::DB` is
  `virtual bool GetIntProperty(const Slice& property, uint64_t* value)` — not marked
  const in the public API. This is therefore called on a mutable pointer obtained from a
  `const unique_ptr`. This is well-formed C++ (const-ness of the smart pointer does not
  transitively const-qualify the pointee). No const_cast anywhere.

### 4. Base virtual design

**PASS.**

- `BackendStats` is a plain aggregate defined entirely in `SpillBackend.hpp`. The header
  includes only `<cstdint>`, `<optional>`, `<span>`, `<vector>`, `<Identifiers/Identifiers.hpp>`,
  `<SliceStore/Slice.hpp>` — all already present before PR2. No RocksDB include leaks
  into the base header.
- `[[nodiscard]] virtual std::optional<BackendStats> getBackendStats() const { return std::nullopt; }`
  is non-pure with a defaulted body. `NullSpillBackend` and `InMemorySpillBackend` are
  `final` classes that do **not** override this virtual — they silently inherit the default
  returning nullopt. This is ABI-consistent because both subclasses are in the same module.
- Test (c) exercises the virtual dispatch path via `SpillBackend*` base pointer — confirms
  the default override is correctly inherited.
- `RocksDBSpillBackend::getBackendStats()` is declared with `override` — the compiler
  will catch any signature mismatch with the base.

### 5. logMetrics integration

**PASS.**

- `logMetrics()` is `const`; `backend` is `std::unique_ptr<SpillBackend>` (member of
  the store). Inside `const logMetrics()`, `backend` is `const unique_ptr<SpillBackend>&`.
  `backend->getBackendStats()` calls through `unique_ptr::operator->() const` returning
  `SpillBackend*` (non-const), then dispatches `getBackendStats() const` — well-formed
  (the method is `const`-qualified on the interface).
- No `dynamic_cast` — the store reaches the backend purely through the virtual interface.
  RocksDB knowledge stays private to the backend's TU. This is correct.
- The `NES_INFO` line is emitted inside `if (backendStats.has_value())` — the line is
  only produced for RocksDB backends; Null/InMemory produce no extra output.
- The log tag `"RocksDB stats:"` is stable, unique, and grep-parseable. The format
  `key=value` with fixed field order satisfies the harness contract from DESIGN-BRIEF.md
  §"Output contract".
- `write_amp={:.4f}` produces exactly 4 decimal places — the grep/sed pattern in
  section-2-done.md captures this correctly.

### 6. Performance / measurement skew for RQ2

**INFO — documented above.**

`kExceptDetailedTimers` adds ~2–5% hot-path overhead. This is unavoidable given PR2's
goal. The thesis should document that all E1 eval runs include statistics overhead.
`kExceptDetailedTimers` is the correct level: it avoids the mutex-lock timing paths of
`kAll` while retaining all required byte/count tickers.

### 7. Standard C++ / RAII / exception safety

**PASS.**

- All new memory is managed via `shared_ptr` (Statistics) and the pre-existing
  `unique_ptr<DB>`. No raw `new`/`delete`.
- `rocksdb::Status` returns are checked at all call sites (Put, Get, Write, Open — all
  existing code). `GetIntProperty` bool return is checked.
- `getBackendStats()` is `noexcept` in practice (no throwing operations inside); it would
  be worth marking it `noexcept` explicitly since none of `getTickerCount`, `GetIntProperty`
  throw. This is a low-priority style note only.
- No UB: no uninitialized reads, no dangling pointers, no type-punning beyond the
  pre-existing `reinterpret_cast` in `put()`/`get()` (out of PR2 scope).

---

## Test Coverage

Three new tests registered in `RocksDBSpillBackendTest.cpp`:

| Test | Coverage |
|------|----------|
| `getBackendStatsReturnsValueAfterWrites` | Non-nullopt result; `writeAmplification >= 0`; `bytesWritten > 0`; conditional flush invariants |
| `bytesWrittenIncreasesWithMorePuts` | Monotonic accumulation of `BYTES_WRITTEN` ticker |
| `nullAndInMemoryBackendsReturnNulloptStats` | Virtual default via base pointer; both Null and InMemory return nullopt |

Coverage is adequate for the PR scope. One additional test that would strengthen
confidence: flush-then-read to assert `bytesFlushed > 0` and `writeAmplification >= 1.0`
deterministically. However, forcing a flush in a unit test requires `DB::Flush()` which
is not yet in the test fixture, and the existing test already handles this with a
conditional assertion. Acceptable for thesis eval purposes.

---

## Summary

| Check | Result |
|-------|--------|
| Statistics lifetime / ownership | PASS |
| Stats API names (tickers, GetIntProperty) | PASS |
| Divide-by-zero guard | PASS |
| Write-amp formula correctness | PASS (MEDIUM doc note) |
| const-correctness throughout | PASS |
| Base virtual / ABI | PASS |
| Include hygiene (no RocksDB leak to base) | PASS |
| logMetrics integration | PASS |
| Destruction order (statistics before db in header) | PASS (LOW doc note) |
| RAII / exception safety | PASS |
| Performance overhead (kExceptDetailedTimers) | ACCEPTABLE (INFO note) |
| Test adequacy | PASS |
| LOCKED constraints respected | YES (all 8) |

**Final verdict: APPROVE.**  
Address the MEDIUM documentation inconsistency (`max(1,…)` vs actual `0.0` sentinel
in the struct comment, and `BYTES_WRITTEN` field description) before merging to ensure
the thesis text and the header docstring agree. No code changes required.
