# E1-PR2 — RocksDB Internal Statistics: Write-Amp + On-Disk Footprint

**Status:** DONE (source-only; build + test run in Docker per LOCKED constraint #3)

---

## Files changed

| File | What changed |
|------|-------------|
| `nes-physical-operators/include/SliceStore/SpillBackend.hpp` | Added `BackendStats` struct + default-`nullopt` virtual `getBackendStats() const` |
| `nes-physical-operators/include/SliceStore/RocksDBSpillBackend.hpp` | Forward-declared `rocksdb::Statistics`; added `std::shared_ptr<rocksdb::Statistics> statistics` member; declared `getBackendStats() const override` |
| `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` | Added `#include <rocksdb/statistics.h>`; enabled `options.statistics = rocksdb::CreateDBStatistics()` at open site; implemented `getBackendStats()` |
| `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` | Extended `logMetrics()` to call `backend->getBackendStats()` and emit `RocksDB stats:` line |
| `nes-physical-operators/tests/RocksDBSpillBackendTest.cpp` | Added `#include` for Null/InMemory backends; added three new test cases (RED on baseline) |

**NOT touched:** `NullSpillBackend.hpp`, `InMemorySpillBackend.hpp`, `streamWindowPartition`, PR1's restore-sampling code, CMakeLists.txt (no new test target needed — `RocksDBSpillBackendTest` already registered).

---

## RocksDB tickers and properties used

| Ticker / Property | API | Meaning |
|---|---|---|
| `rocksdb::FLUSH_WRITE_BYTES` | `statistics->getTickerCount(FLUSH_WRITE_BYTES)` | Bytes written by memtable flushes to L0 SST files |
| `rocksdb::COMPACT_WRITE_BYTES` | `statistics->getTickerCount(COMPACT_WRITE_BYTES)` | Bytes written by compaction jobs |
| `rocksdb::BYTES_WRITTEN` | `statistics->getTickerCount(BYTES_WRITTEN)` | Uncompressed user bytes via DB::Put / DB::Write (includes key+value, WAL-path accounting) |
| `rocksdb::BLOCK_CACHE_HIT` | `statistics->getTickerCount(BLOCK_CACHE_HIT)` | All block cache hits (index + filter + data) |
| `rocksdb::BLOCK_CACHE_MISS` | `statistics->getTickerCount(BLOCK_CACHE_MISS)` | All block cache misses |
| `"rocksdb.total-sst-files-size"` | `db->GetIntProperty("rocksdb.total-sst-files-size", &sstSize)` | Total on-disk footprint of ALL SST files (live + obsolete) |

All tickers are confirmed present in the installed `rocksdb/statistics.h` (lines 174, 231-232, 41-42 of that header). `GetIntProperty` is confirmed in `rocksdb/db.h` line 1514.

Statistics collection is enabled at `StatsLevel::kExceptDetailedTimers` (the RocksDB default after `CreateDBStatistics()`), which collects all byte/count tickers without the lock-timing overhead of `kAll`.

---

## Write-amplification formula

```
writeAmplification = (bytesFlushed + bytesCompacted) / max(1, bytesFlushed)
```

**Rationale:** `bytesFlushed` (FLUSH_WRITE_BYTES) represents bytes that entered L0 in a first write pass from memtable. Every byte written by subsequent compaction passes (COMPACT_WRITE_BYTES) is amplification on top of that base. The denominator is `bytesFlushed` rather than `BYTES_WRITTEN` because `BYTES_WRITTEN` counts WAL-path user bytes (pre-compression, pre-compaction), which would not account for the memtable→SST conversion amplification.

**Guard:** If `bytesFlushed == 0` (no flush has occurred yet — run too short, or memtable not full), `writeAmplification` is reported as `0.0` to avoid a divide-by-zero and to signal "insufficient data" rather than a spurious value.

**Minimum value when flushes occur:** 1.0 (flush-only, no compaction yet), rising as compaction rewrites data across LSM levels.

This matches the microbenchmark's `parse_write_amp` logic (flush + compact write bytes relative to flush base).

---

## `RocksDB stats:` log-line grammar

Emitted by `SpillableTimeBasedSliceStore::logMetrics()` immediately after the existing `Spill metrics:` line, **only when the backend returns a value** (i.e., only for RocksDB; nullopt = no line emitted for Null/InMemory):

```
RocksDB stats: sst_footprint_bytes=<u64> write_amp=<f4dp> bytes_flushed=<u64> bytes_compacted=<u64> bytes_written=<u64> block_cache_hit=<u64> block_cache_miss=<u64>
```

Example:
```
RocksDB stats: sst_footprint_bytes=67108864 write_amp=1.0000 bytes_flushed=33554432 bytes_compacted=0 bytes_written=45088768 block_cache_hit=142 block_cache_miss=58
```

**Grep pattern for harness:**
```bash
grep "RocksDB stats:" "$log_file" | sed 's/.*sst_footprint_bytes=\([^ ]*\) write_amp=\([^ ]*\) bytes_flushed=\([^ ]*\) bytes_compacted=\([^ ]*\) bytes_written=\([^ ]*\) block_cache_hit=\([^ ]*\) block_cache_miss=\([^ ]*\)/\1\t\2\t\3\t\4\t\5\t\6\t\7/'
```

The `{:.4f}` format for `write_amp` gives 4 decimal places (e.g. `1.0000`, `2.3750`), which is stable and parseable by both `awk` and Python's `float()`.

---

## Base virtual design

`SpillBackend` (the abstract base) gains:
- `struct BackendStats` — plain aggregate with zero-initialized defaults; defined in the base header so both the store and the backends see it without a RocksDB include.
- `virtual std::optional<BackendStats> getBackendStats() const { return std::nullopt; }` — non-pure with a default body so existing subclasses (`NullSpillBackend`, `InMemorySpillBackend`) compile unchanged. The `[[nodiscard]]` attribute ensures callers don't silently discard the value.

`RocksDBSpillBackend` overrides with a real implementation that reads tickers from its `statistics` member.

The store calls `backend->getBackendStats()` through the `SpillBackend&` reference — **no dynamic_cast**, no RocksDB-specific knowledge in the store. This satisfies LOCKED constraint #1 (no JIT edits) and keeps the store's RocksDB dependency fully private.

The forward-declaration `class rocksdb::Statistics;` in `RocksDBSpillBackend.hpp` keeps the public backend header RocksDB-include-free (the `~RocksDBSpillBackend()` destructor is already defined out-of-line for the same reason with `rocksdb::DB`). The `std::shared_ptr<rocksdb::Statistics>` member is valid with a forward declaration because `shared_ptr` only needs the complete type at destruction point, which is the `.cpp` where `<rocksdb/statistics.h>` is included.

---

## Test names and RED-on-baseline rationale

All three tests are in `RocksDBSpillBackendTest.cpp` using the existing `RocksDBSpillBackendTest` fixture. No new CMake registration needed.

| Test | RED reason | GREEN assertion |
|------|-----------|-----------------|
| `getBackendStatsReturnsValueAfterWrites` | `SpillBackend::getBackendStats()` does not exist on baseline → compile error | Returns non-nullopt; `bytesWritten > 0`; `writeAmplification >= 0.0`; `sstFootprintBytes >= 0`; if flush occurred: `writeAmplification >= 1.0` and `sstFootprintBytes > 0` |
| `bytesWrittenIncreasesWithMorePuts` | Same compile error | `bytesWritten` after second batch > `bytesWritten` after first batch |
| `nullAndInMemoryBackendsReturnNulloptStats` | Same compile error | `NullSpillBackend` and `InMemorySpillBackend` (through `SpillBackend*`) return `nullopt` |

The first two tests open a real `RocksDBSpillBackend` in a per-test temp dir (same pattern as all existing tests via `dbPath`). The third test does **not** open RocksDB — it exercises only the base-class default.

---

## Reviewer-check risks

1. **`statistics` member not initialized before `db`:** The constructor initializes `statistics` before calling `DB::Open`, so `getBackendStats()` never sees a null pointer. The `if (!statistics)` guard is a belt-and-suspenders check; it can never fire in normal usage.

2. **`const` correctness in `logMetrics()`:** `logMetrics()` is `const`; `backend` is `unique_ptr<SpillBackend>`. Calling `backend->getBackendStats()` on a `const unique_ptr` is well-formed because `unique_ptr::operator->()` on a const instance returns a non-const raw pointer (const-ness of the unique_ptr does not propagate to the pointee). `getBackendStats()` is declared `const` on the interface, so the call is legal.

3. **Forward declaration of `rocksdb::Statistics` in the header:** `shared_ptr<rocksdb::Statistics>` with a forward declaration is valid because `shared_ptr` stores the deleter type-erased at construction time (in the `.cpp` where the full type is visible). No destructor or `reset()` call happens in the header.

4. **`GetIntProperty` vs `GetProperty`:** We use `GetIntProperty` (returns `bool`, writes `uint64_t`) rather than `GetProperty` (returns string that needs `stoull`). This is the canonical API for `rocksdb.total-sst-files-size`; confirmed in `db.h` line 1491-1492 (the property is listed as a valid `GetIntProperty` argument) and line 1514 (the signature).

5. **Statistics overhead:** `CreateDBStatistics()` with default `kExceptDetailedTimers` level adds atomic increments on the hot path (Put/Get). This is the standard RocksDB instrumentation approach and is acceptable per LOCKED constraint #6 (we do not hold evictionMutex during RocksDB I/O).

6. **Test brittleness on `bytesWritten` exact count:** The test asserts `> 0` (not an exact byte count) because `BYTES_WRITTEN` counts WAL-path bytes including RocksDB's own key encoding overhead. Asserting positivity is robust; asserting equality would be fragile.
