# PR Review: Phase 1 OOC Spill Skeleton

**Verdict: APPROVE WITH NITS**

Date: 2026-05-20
Reviewer: Claude Code (Sonnet 4.6)
Build status at review time: green; 13/13 new unit tests pass.

No CRITICAL or HIGH issues found. Three MEDIUM items and several nits. All locked decisions were respected and are not flagged.

---

## Summary of Changes Reviewed

| File | Role |
|------|------|
| `SpillConfig.hpp` | New config class wired into `QueryExecutionConfiguration` |
| `SpillBackend.hpp` | New pure-virtual interface |
| `NullSpillBackend.hpp` | No-op implementation |
| `QueryExecutionConfiguration.hpp` | +include, +member, +getOptions entry |
| `LowerToPhysicalWindowedAggregation.cpp` | +fail-fast guard at top of `apply()` |
| `SpillConfigTests.cpp` | 6 unit tests for config |
| `NullSpillBackendTest.cpp` | 4 unit tests for NullSpillBackend |
| `LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp` | 3 unit tests for the guard |
| Both `CMakeLists.txt` | 3 new test targets |

---

## CRITICAL — None

---

## HIGH — None

---

## MEDIUM

### M1 — `SpillBackend.hpp:43-46` — Rule-of-Five is incomplete for a polymorphic base

**File:** `nes-physical-operators/include/SliceStore/SpillBackend.hpp`, lines 43-46

The class explicitly defines all five special members:

```cpp
SpillBackend() = default;
SpillBackend(const SpillBackend&) = default;
SpillBackend& operator=(const SpillBackend&) = default;
SpillBackend(SpillBackend&&) = default;
SpillBackend& operator=(SpillBackend&&) = default;
virtual ~SpillBackend() = default;
```

This is syntactically fine, but the copy/move assignment operators on a polymorphic base are almost always a slicing hazard or a dead letter. The standard guidance for a pure-virtual interface with a virtual destructor is the **Rule of Zero** on the base: delete or `= delete` the copy/move operators so that accidental object-slicing fails loudly at the call site rather than silently producing a truncated copy.

`NullSpillBackend` is `final`, so its overrides cannot be sliced. However, future concrete backends that are **not** `final` (e.g. the RocksDB backend in Phase 3) will inherit the defaulted copy, enabling silent slicing if someone passes a backend by value through the base interface.

**Fix:**

```cpp
class SpillBackend
{
public:
    SpillBackend() = default;
    SpillBackend(const SpillBackend&) = delete;
    SpillBackend& operator=(const SpillBackend&) = delete;
    SpillBackend(SpillBackend&&) = delete;
    SpillBackend& operator=(SpillBackend&&) = delete;
    virtual ~SpillBackend() = default;
    ...
};
```

Move semantics are not useful on a polymorphic base consumed via `unique_ptr<SpillBackend>` (the move happens at the pointer level). Deleting them is consistent with `WindowSlicesStoreInterface` and most other NES virtual bases.

---

### M2 — `SpillBackend.hpp:54` — `get` return type copies an entire `SpillRecord`; consider `std::optional<SpillRecord>&&` or a callback

**File:** `nes-physical-operators/include/SliceStore/SpillBackend.hpp`, line 54

```cpp
virtual std::optional<SpillRecord> get(SliceEnd sliceEnd, WorkerThreadId workerThreadId) = 0;
```

`SpillRecord` is `std::vector<std::byte>`, which means a successful `get` returns a heap allocation by value. For a RocksDB backend, the data is already owned by a `std::string` or `rocksdb::PinnableSlice`; materializing a full copy into a `vector<byte>` doubles peak memory for every slice retrieval.

This is Phase 1 scaffolding and the performance impact is zero today, but the signature is load-bearing: it will be the public contract that Phase 3 must implement. Changing it later is a breaking refactor across all call sites.

Suggested alternatives in order of invasiveness:
1. Keep it for now but add a `// TODO Phase 3: consider move-only or pinned-slice return to avoid double-buffering` comment on the declaration so Phase 3 does not accidentally cement the copy-out path.
2. Return `std::optional<SpillRecord>` but document the ownership transfer contract explicitly ("the returned vector is the sole copy; callers must not alias it").

At minimum, document the performance intent so Phase 3 can make an informed choice. No code change is strictly required for Phase 1 approval, but the contract ambiguity is real.

---

### M3 — `SpillConfigTests.cpp:49-51` — `std::ofstream` write error is not checked

**File:** `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp`, lines 44-54

```cpp
std::ofstream out(path);
out << content;
out.close();
```

If the temp directory is full or the path is not writable, `out` will be in a failed state and `out.close()` will silently succeed. The YAML-parse test then opens an empty or absent file and the error surfaces as a confusing YAML parse failure rather than a clear fixture failure.

**Fix:**

```cpp
std::ofstream out(path);
EXPECT_TRUE(out.is_open()) << "Could not open temp file: " << path;
out << content;
out.close();
EXPECT_TRUE(out.good()) << "Write failed for temp file: " << path;
```

Using `EXPECT_TRUE` (rather than `ASSERT_THROW`) keeps cleanup running even on failure.

---

## LOW

### L1 — `SpillBackend.hpp:57` — `erase` contract is underspecified

**File:** `nes-physical-operators/include/SliceStore/SpillBackend.hpp`, line 57

```cpp
/// Remove every per-thread record for the slice ending at `sliceEnd`.
virtual void erase(SliceEnd sliceEnd) = 0;
```

The comment says "every per-thread record", which implies a range delete keyed only on `sliceEnd`. Two questions that arise in Phase 3:

1. Is `erase` idempotent? (safe to call if no records exist for that `sliceEnd`)
2. What is the expected behaviour if `erase` is called concurrently with a `put` on the same key?

The NullSpillBackend is trivially correct for both, but the RocksDB backend must know the answer. Document the contract on the declaration:

```cpp
/// Remove every per-thread record for the slice ending at `sliceEnd`.
/// Idempotent: safe to call even if no records exist for `sliceEnd`.
/// Not thread-safe with concurrent puts on the same key unless the
/// concrete implementation explicitly documents otherwise.
virtual void erase(SliceEnd sliceEnd) = 0;
```

---

### L2 — `SpillConfig.hpp:44` — hardcoded `/tmp/nes-spill` default path

**File:** `nes-query-compiler/interface/SpillConfig.hpp`, line 44

```cpp
StringOption rocksdbPath = {"rocksdb_path", "/tmp/nes-spill", "Filesystem path for the RocksDB spill backend."};
```

`/tmp` is world-writable on Linux but may be `noexec` or tmpfs-backed (so it disappears across reboots), and on macOS it is `$TMPDIR`-redirected (`/private/var/folders/...`). For a production default this path is acceptable as a "you need to configure this" placeholder, but the description should say so explicitly to avoid users running on a partition that mounts `/tmp` with size restrictions.

**Fix (doc-only):** Change the description string to:

```
"Filesystem path for the RocksDB spill backend. The default is a placeholder; set this to a persistent directory before enabling spilling."
```

No code change required.

---

### L3 — `NullSpillBackend.hpp:15` — `#pragma once` in a `.hpp` that will be included transitively via `SpillBackend.hpp`

**File:** `nes-physical-operators/include/SliceStore/NullSpillBackend.hpp`, line 15

This is fine — `#pragma once` is the project-wide convention (consistent with all other headers reviewed). No change required. Noted only for completeness.

---

## NITS

### N1 — `SpillConfig.hpp:37` — `SpillConfig() = default;` is redundant when a user-provided constructor is already declared

**File:** `nes-query-compiler/interface/SpillConfig.hpp`, line 37

The presence of the `SpillConfig(const std::string&, const std::string&)` constructor suppresses the implicit default constructor. The `= default` re-declares it explicitly. This exactly mirrors `SliceCacheConfiguration`, so it is a deliberate stylistic choice — fine as-is, but worth noting that the default constructor serves only in-place member initialization scenarios (which is exactly how it is used in `QueryExecutionConfiguration`). No change needed.

---

### N2 — `SpillBackend.hpp:28` — `SpillRecord` alias should carry a comment tying it to the `get`/`put` contract

**File:** `nes-physical-operators/include/SliceStore/SpillBackend.hpp`, line 28

```cpp
using SpillRecord = std::vector<std::byte>;
```

A one-line comment stating that this is the owned byte representation of a serialized slice (vs. the non-owning `std::span<const std::byte>` used in `put`) would help readers understand why both types appear in the same interface:

```cpp
/// Owned byte representation of a deserialized spilled slice
/// (compare the non-owning std::span<const std::byte> used in put()).
using SpillRecord = std::vector<std::byte>;
```

---

### N3 — `LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp:33-41` — `configWithSpill` helper returns by value with an unnecessary branch

**File:** `nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp`, lines 33-41

```cpp
QueryExecutionConfiguration configWithSpill(const bool enabled)
{
    QueryExecutionConfiguration conf;
    if (enabled)
    {
        conf.overwriteConfigWithCommandLineInput({{"spill.enabled", "true"}});
    }
    return conf;
}
```

The helper is only ever called with `enabled=true` (the `spillDisabledByDefault` test constructs a `QueryExecutionConfiguration` directly). The `if (enabled)` branch for `false` is dead code in the current test file. This is a minor readability issue — either remove the `false` branch and rename to `configWithSpillEnabled()`, or add a test that exercises the `false` path through this helper. No impact on correctness.

---

### N4 — `SpillConfigTests.cpp:47-48` — temp file path has a collision window within the same test-suite run

The path is:
```
spill-config-<random_seed>-<test_name>.yaml
```

`random_seed()` returns the same value for all tests in a single run. Two tests with the same `current_test_info()->name()` (impossible here since test names are unique within a fixture, but possible if the fixture is reused in a second translation unit) would collide. The path is unique today. Noted only as a forward-compatibility nit.

---

## What Is Done Well

- **SpillBackend virtual destructor**: present and `= default`, preventing the classic UB from deleting via a base pointer.
- **`std::span<const std::byte>` for `put`**: correct non-owning, bounds-safe parameter — avoids the temptation of raw pointer + length.
- **`NullSpillBackend` marked `final`**: prevents unintentional further derivation from a no-op.
- **Fail-fast guard placement**: the `NotImplemented` throw as the very first statement of `apply()` is the correct choice — it fires before any operator-shape preconditions and before any resource acquisition, leaving nothing to clean up.
- **`ASSERT_EXCEPTION_ERRORCODE` used instead of bare `EXPECT_THROW`**: correctly validates both the exception type and the NES error code, matching project conventions.
- **Test D in `SpillConfigTests` (`embeddedDefaultsInQueryExecutionConfiguration`)**: this is the load-bearing test that validates the config wire rather than the config class in isolation — good thinking.
- **`static_assert` in `NullSpillBackendTest::satisfiesInterfaceContract`**: catches hierarchy breakage at compile time.
- **Temp file cleanup in `TearDown`**: uses `std::error_code`-based `filesystem::remove`, so cleanup failures do not themselves throw.
- **File sizes**: all new files are well within the 800-line limit (largest is the `.cpp` test at 151 lines).
- **No magic numbers in new code**: config defaults are string literals in the option declarations (consistent with `SliceCacheConfiguration`), not anonymous integer constants.

---

## Verdict

**APPROVE WITH NITS**

MEDIUM items M1 (Rule-of-Five on SpillBackend) and M3 (unchecked ofstream write) are addressable in a quick follow-up before Phase 2 begins. M2 (get return type) needs a comment now to flag the Phase 3 decision point. LOW and NIT items are informational. No CRITICAL or HIGH issues were found.
