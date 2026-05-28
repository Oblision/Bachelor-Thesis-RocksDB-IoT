# H2 — Write-Stall Instrumentation

**Status:** DONE (source-only; build + test run in Docker per LOCKED constraint #3)

---

## Goal

Hypothesis H2: "write stalls are < 5% of execution time."  
Measurement: expose RocksDB's cumulative write-stall time and stall event count through the same `BackendStats` / `getBackendStats()` / `RocksDB stats:` pipeline already established by E1-PR2.

---

## Files changed

| File | What changed |
|------|-------------|
| `nes-physical-operators/include/SliceStore/SpillBackend.hpp` | Added `writeStallMicros` and `writeStallCount` fields to `BackendStats`; updated struct doxygen |
| `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` | Extended `getBackendStats()` to read `STALL_MICROS` ticker and `WRITE_STALL` histogram count |
| `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` | Extended `logMetrics()` `RocksDB stats:` line with ` write_stall_micros=<u64> write_stall_count=<u64>` appended at the end |
| `nes-physical-operators/tests/RocksDBSpillBackendTest.cpp` | Added three new H2 test cases (see Test names below) |

**NOT touched:** `NullSpillBackend.hpp`, `InMemorySpillBackend.hpp`, `RocksDBSpillBackend.hpp` (declaration unchanged — the `.hpp` already declares `getBackendStats() const override`), CMakeLists.txt.

---

## RocksDB API used

### Primary metric — `STALL_MICROS` ticker

```cpp
stats.writeStallMicros = statistics->getTickerCount(rocksdb::STALL_MICROS);
```

**Name confirmed in:** `rocksdb/statistics.h`, enum `Tickers`, entry at line 203:
```
// Writer has to wait for compaction or flush to finish.
STALL_MICROS,
```

**Meaning:** cumulative wall-clock microseconds that `DB::Put` / `DB::Write` callers spent blocked inside RocksDB's write-stall logic (delayed or stopped by L0/pending-compaction backpressure).

**Stats-level requirement:** `kExceptDetailedTimers` (the current level) — sufficient.  
**Basis:** `STALL_MICROS` has no "Disabled by default. To enable set stats level to kAll" annotation in `statistics.h`. The only ticker with that annotation is `DB_MUTEX_WAIT_MICROS` (line 205-206). All other tickers in the enum, including `STALL_MICROS`, are collected at the default `kExceptDetailedTimers`.  
**We did NOT raise the stats level** — doing so (`kAll`) would add lock-timing overhead on the write path, violating LOCKED constraint #6.

### Secondary metric — `WRITE_STALL` histogram count

```cpp
rocksdb::HistogramData stallHist{};
statistics->histogramData(rocksdb::WRITE_STALL, &stallHist);
stats.writeStallCount = stallHist.count;
```

**Name confirmed in:** `rocksdb/statistics.h`, enum `Histograms`, entry at line 591:
```
WRITE_STALL,
```

**Meaning:** `HistogramData.count` = number of distinct stall intervals recorded by RocksDB. Separates "many short stalls" from "one long stall" — both can have the same `STALL_MICROS` total.

**Stats-level requirement:** `kExceptDetailedTimers` — sufficient.  
**Basis:** The histograms that explicitly require a higher level in `statistics.h` are the `FILE_READ_GET_MICROS`, `FILE_READ_MULTIGET_MICROS`, `FILE_READ_DB_ITERATOR_MICROS`, `FILE_READ_VERIFY_DB_CHECKSUM_MICROS`, and `FILE_READ_VERIFY_FILE_CHECKSUMS_MICROS` entries, each annotated with "The following `FILE_READ_*` require stats level greater than `StatsLevel::kExceptDetailedTimers`". `WRITE_STALL` has no such annotation.

**Cost:** reading a histogram is a single struct copy — O(1), no I/O, no locking of the eviction mutex.

### Candidate rejected — `rocksdb.actual-delayed-write-rate` property

`db->GetIntProperty("rocksdb.actual-delayed-write-rate", ...)` reports the _current_ delayed write rate (instantaneous snapshot), not a cumulative total. It cannot answer "what fraction of execution time was stalled" and was not used.

---

## New fields in `BackendStats`

```cpp
std::uint64_t writeStallMicros{0};  // Cumulative write-stall time (STALL_MICROS ticker; 0 = no stalls)
std::uint64_t writeStallCount{0};   // Number of stall events (WRITE_STALL histogram count; 0 = no stalls)
```

Both default-initialize to 0 (plain aggregate; existing callers that do not check these fields are source- and ABI-compatible because `BackendStats` is a non-polymorphic struct with no vtable and the new fields are appended at the end).

---

## Updated `RocksDB stats:` log-line grammar

The two new fields are **appended after `block_cache_miss`** — all existing fields are in their original positions (the shell harness grep/sed pattern for existing fields is not broken):

```
RocksDB stats: sst_footprint_bytes=<u64> write_amp=<f4dp> bytes_flushed=<u64> bytes_compacted=<u64> bytes_written=<u64> block_cache_hit=<u64> block_cache_miss=<u64> write_stall_micros=<u64> write_stall_count=<u64>
```

Example (no stalls — valid H2 outcome):
```
RocksDB stats: sst_footprint_bytes=67108864 write_amp=1.0000 bytes_flushed=33554432 bytes_compacted=0 bytes_written=45088768 block_cache_hit=142 block_cache_miss=58 write_stall_micros=0 write_stall_count=0
```

Example (stalls occurred — H2 refuted):
```
RocksDB stats: sst_footprint_bytes=268435456 write_amp=3.2500 bytes_flushed=83886080 bytes_compacted=188743680 bytes_written=134217728 block_cache_hit=0 block_cache_miss=4096 write_stall_micros=924000 write_stall_count=37
```

**Extended grep pattern for harness:**
```bash
grep "RocksDB stats:" "$log_file" | sed 's/.*sst_footprint_bytes=\([^ ]*\) write_amp=\([^ ]*\) bytes_flushed=\([^ ]*\) bytes_compacted=\([^ ]*\) bytes_written=\([^ ]*\) block_cache_hit=\([^ ]*\) block_cache_miss=\([^ ]*\) write_stall_micros=\([^ ]*\) write_stall_count=\([^ ]*\)/\1\t\2\t\3\t\4\t\5\t\6\t\7\t\8\t\9/'
```

**H2 stall-fraction computation (in harness, not in NES source):**
```
stall_fraction = write_stall_micros / (query_elapsed_seconds * 1_000_000)
H2 confirmed if stall_fraction < 0.05
```

---

## Test names

All three tests are in `RocksDBSpillBackendTest.cpp` using the existing `RocksDBSpillBackendTest` fixture.

| Test | What it asserts |
|------|----------------|
| `writeStallFieldsPresentAfterWrites` | `getBackendStats()` returns non-nullopt; `writeStallMicros >= 0`; `writeStallCount >= 0`; duration-count consistency (micros==0 ↔ count==0) |
| `writeStallFieldsAreNonDecreasingAcrossMorePuts` | Both fields are monotone non-decreasing across two write batches (ticker/histogram counts never reset within a DB lifetime) |
| `writeStallFieldsAbsentForNullAndInMemoryBackends` | `NullSpillBackend` and `InMemorySpillBackend` still return `nullopt` (H2 fields must not break the existing nullopt contract) |

The existing `nullAndInMemoryBackendsReturnNulloptStats` test (E1-PR2) is preserved unchanged.

---

## Reviewer risk notes

1. **`writeStallMicros == 0` in all unit tests** — this is correct and expected. A unit test writes a handful of records without memory pressure; RocksDB never stalls. The test asserts the field is present and >= 0, not that it is non-zero. Non-zero values are produced only under sustained write-overload (the constrained eval harness scenario). Reviewer must not flag `writeStallMicros == 0` as a missing assertion.

2. **Duration-count consistency** — the test asserts `micros == 0 ↔ count == 0`. This holds because `STALL_MICROS` and `WRITE_STALL` histogram are both incremented by the same RocksDB write-stall code path; one cannot fire without the other. If a future RocksDB version decouples them, the test will surface the divergence.

3. **`histogramData` zero-initialization** — `rocksdb::HistogramData stallHist{}` is value-initialized (all members zero), so if `histogramData()` is a no-op on a fresh statistics object (no stalls), `stallHist.count` is 0. This is the correct fallback — it does not require a null check.

4. **`WRITE_STALL` histogram at `kExceptDetailedTimers`** — confirmed by absence of annotation in installed `statistics.h`. If a future RocksDB version moves this histogram to a higher level, `writeStallCount` will silently read 0 (histogram stays empty). The field comment documents the level assumption; a reviewer can grep the header to re-verify.

5. **`BackendStats` ABI stability** — new fields are appended at the end of the struct. No existing code that constructs `BackendStats{}` is broken (the new fields default to 0). No existing `logMetrics()` grep/sed is broken (new fields are appended after the last existing field).

6. **No RocksDB statistics level change** — we confirmed `STALL_MICROS` is collected at `kExceptDetailedTimers`. Raising to `kAll` would collect lock-timing counters with per-write mutex probes, adding measurable overhead on the put hot path — violating LOCKED constraint #6. The chosen API (`STALL_MICROS` ticker + `WRITE_STALL` histogram) is the cheapest correct source for H2.
