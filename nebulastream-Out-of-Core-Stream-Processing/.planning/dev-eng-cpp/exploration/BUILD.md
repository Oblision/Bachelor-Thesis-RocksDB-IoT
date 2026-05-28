# BUILD.md — Build System & Dependencies (Explorer 2)

READ-ONLY exploration. No code modified. Scope = **Increment A** (spike) per
`.planning/dev-eng-phase4b/DESIGN_BRIEF.md` "Recommended scope split".

Repo: `nebulastream-Out-of-Core-Stream-Processing`, branch `feat/ooc-phase1-spill-skeleton` @ `40de3f9a55`.
Builds run **only in Docker**: image `nebulastream/nes-development:local-rocksdb`, repo at `/workspace`,
build dir `cmake-build-debug`, `ninja` + `ctest -LE 'Systest|DockerCompose'`.

---

## TL;DR for the plan

- **Only TWO CMake files need editing for Increment A:**
  1. `nes-physical-operators/src/SliceStore/CMakeLists.txt` — add `SpillableTimeBasedSliceStore.cpp` to the `add_source_files` list.
  2. `nes-physical-operators/tests/CMakeLists.txt` — add `add_nes_physical_operator_test(...)` line(s) for the new unit test(s).
- **No linkage changes.** RocksDB was wired in Phase 3 (`find_package(RocksDB CONFIG REQUIRED)` + `RocksDB::rocksdb` PRIVATE).
  The new store uses the `SpillBackend` interface, not rocksdb directly → needs nothing beyond what is there.
- **No new header registration step.** Public headers are exposed via `target_include_directories(... include)`; a new
  `.hpp` under `include/SliceStore/` is automatically visible as `<SliceStore/SpillableTimeBasedSliceStore.hpp>`. No CMake edit for headers.
- **No vcpkg / toolchain / dependency-image rebuild** for Increment A (no new deps).
- **End-to-end integration test (the spike): two viable homes** — see §3. Prefer a **`.test` systest** for results-correctness
  (lives in `nes-systests/operator/aggregation/`, **carries the `Systest` label → EXCLUDED by the regression gate**, run it
  separately), OR an in-process C++ integration test if a non-Systest-labeled harness is chosen. The simplest gate-included
  proof of "spilling occurred" is a C++ unit/integration test in `nes-physical-operators/tests` driving the store directly.

---

## 1. Where new source files get registered

### Mechanism (`cmake/macros.cmake`)
Sources are collected via a **global property**, not listed on `add_library` directly:
- `add_source_files(<target> <files...>)` → pushes absolute paths onto global property `<target>_SOURCE_PROP`
  (lines 15–19, 21–33 of `cmake/macros.cmake`).
- The top-level `nes-physical-operators/CMakeLists.txt` then does
  `get_source(nes-physical-operators NES_PHYSICAL_OPERATORS_SOURCE_FILES)` and
  `add_library(nes-physical-operators ${NES_PHYSICAL_OPERATORS_SOURCE_FILES})`.

So **a new `.cpp` is registered purely by adding it to an `add_source_files(...)` list in the relevant subdir CMakeLists** —
nothing else.

### Current registration of the sibling files (`nes-physical-operators/src/SliceStore/CMakeLists.txt`)
```cmake
add_source_files(nes-physical-operators
        Slice.cpp
        DefaultTimeBasedSliceStore.cpp
        DefaultTimeBasedSliceStoreRef.cpp
        SliceSerializer.cpp
        RocksDBSpillBackend.cpp
)

add_subdirectory(SliceCache)
```
- Target: `nes-physical-operators`.
- `DefaultTimeBasedSliceStore.cpp`, `RocksDBSpillBackend.cpp`, `SliceSerializer.cpp` are **all already here** — confirms the
  new `SpillableTimeBasedSliceStore.cpp` slots in **identically** (same list, same target, paths relative to this dir).
- Note: `NullSpillBackend` / `InMemorySpillBackend` are **header-only** (no `.cpp` here) — that is why they are not in the list.
  `SpillableTimeBasedSliceStore` has a `.cpp`, so it goes in the list exactly like `DefaultTimeBasedSliceStore.cpp`.

**Edit needed:** add `SpillableTimeBasedSliceStore.cpp` after `RocksDBSpillBackend.cpp` in that block.

> The `nes-physical-operators/include/SliceStore/CMakeLists.txt` does **not** exist (headers are not separately registered).
> Header dir is `nes-physical-operators/include/SliceStore/` and is published wholesale via `target_include_directories`.

---

## 2. RocksDB linkage (confirm Phase 3, and whether the new store needs anything)

`nes-physical-operators/CMakeLists.txt` (verbatim, relevant lines):
```cmake
add_library(nes-physical-operators ${NES_PHYSICAL_OPERATORS_SOURCE_FILES})
find_package(OpenSSL REQUIRED)
# RocksDB backs the out-of-core spill store (SliceStore/RocksDBSpillBackend). Linked PRIVATE: the
# public headers forward-declare rocksdb::DB, so RocksDB is an implementation detail of this target.
find_package(RocksDB CONFIG REQUIRED)
target_link_libraries(nes-physical-operators PUBLIC nes-sources nes-sinks nes-nautilus nes-input-formatter-provider
        PRIVATE nes-inference-runtime OpenSSL::Crypto RocksDB::rocksdb)
target_include_directories(nes-physical-operators PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include/nebulastream/>)
```
- **Confirmed:** `find_package(RocksDB CONFIG REQUIRED)` + `RocksDB::rocksdb` linked **PRIVATE** were added in Phase 3.
- `RocksDBSpillBackend.hpp` (line 60) holds `std::unique_ptr<rocksdb::DB> db;` with `rocksdb::DB` **forward-declared** —
  so rocksdb stays an implementation detail; only `RocksDBSpillBackend.cpp` (inside this target) needs the rocksdb headers.
- **`SpillableTimeBasedSliceStore` needs NOTHING beyond what is there.** It owns a `SpillBackend*`/`unique_ptr<SpillBackend>`
  (interface in `include/SliceStore/SpillBackend.hpp`) and never touches `rocksdb::` symbols. Since its `.cpp` lives in the
  same target, even if it constructed a `RocksDBSpillBackend` directly it would still link fine (PRIVATE rocksdb is in-target).
  In the design, the backend is constructed at the **lowering** site (see §3 note below), so the store only sees the interface.

### Cross-target note (lowering rule lives in nes-query-compiler, not nes-physical-operators)
The Phase 1 guard + slice-store construction is in
`nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` (NOT in nes-physical-operators).
`nes-query-compiler/CMakeLists.txt` line 24:
```cmake
target_link_libraries(nes-query-compiler PUBLIC nes-nautilus nes-physical-operators nes-physical-operators-registry nes-runtime nes-input-formatter-provider)
```
- `nes-query-compiler` already links `nes-physical-operators` **PUBLIC** → the lowering rule can
  `#include <SliceStore/SpillableTimeBasedSliceStore.hpp>` and construct it with **no linkage change**.
- If the lowering rule constructs a `RocksDBSpillBackend` itself, it would need `RocksDB::rocksdb`, which is **PRIVATE** on
  nes-physical-operators (not transitively visible). **Recommendation for the architect (O2):** construct the
  `RocksDBSpillBackend` *inside* `SpillableTimeBasedSliceStore` (or a static factory in nes-physical-operators) from the
  `SpillConfig` fields, so rocksdb stays in-target and **no rocksdb linkage is added to nes-query-compiler**. The lowering
  rule then passes only `SpillConfig` values (path/compression/thresholds) across the seam. If instead the architect insists
  the backend is built in the lowering rule, that WOULD require adding `RocksDB::rocksdb` to nes-query-compiler — flagged.
- `SpillConfig` (`nes-query-compiler/interface/SpillConfig.hpp`) fields available to pass:
  `enabled`, `rocksdbPath` (default `/tmp/nes-spill`), `softThresholdMB` (256), `hardThresholdMB` (384), `compression` (lz4).

---

## 3. Test registration

### Unit-test pattern (`nes-physical-operators/tests/CMakeLists.txt`)
```cmake
function(add_nes_physical_operator_test)
    add_nes_test(${ARGN})
    set(TARGET_NAME ${ARGV0})
    target_link_libraries(${TARGET_NAME} nes-data-types nes-physical-operators nes-memory-test-utils nes-test-util)
endfunction()

add_nes_physical_operator_test(NullSpillBackendTest        NullSpillBackendTest.cpp)
add_nes_physical_operator_test(InMemorySpillBackendTest    InMemorySpillBackendTest.cpp)
add_nes_physical_operator_test(SliceSerializerTest         SliceSerializerTest.cpp)
add_nes_physical_operator_test(RocksDBSpillBackendTest     RocksDBSpillBackendTest.cpp)
add_nes_physical_operator_test(SpillableAggregationSliceTest SpillableAggregationSliceTest.cpp)
```
- `add_nes_test` (`cmake/NebulaStreamTest.cmake:21`) builds the executable, links `nes-test-util nes-logger-bindings`, and
  registers with `gtest_discover_tests(...)` — **it sets NO LABELS**.
- **Consequence (regression gate):** any test added via `add_nes_physical_operator_test` is **NOT** matched by
  `ctest -LE 'Systest|DockerCompose'` (no label) → it **runs in the regression gate**. Good for both the new store unit tests
  and a C++-level integration test.

**Edit needed (D2/D3/D4 unit tests):**
```cmake
add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)
```
(file in `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`). The existing
`SpillableAggregationSliceTest` is the exact precedent for a spill-component unit test here.

### Where end-to-end aggregation queries are tested
- **`AggregationOperatorHandler` is used in ZERO C++ tests** (grep over all `*/tests` — no match). There is **no C++ unit
  test harness that drives a windowed-aggregation query end-to-end** today.
- End-to-end windowed-aggregation correctness is exercised exclusively through the **systest framework** (`.test` files):
  e.g. `nes-systests/operator/aggregation/WindowAggregationDifferentDataTypes.test`, `...Keyed.test`,
  `WindowWithoutAggregation.test`, etc. (group tag `[Aggregation, WindowOperators, ...]`).
- These `.test` files are executed by ctest entries `systest_agg_<threads>_sliceCache_<bool>_{interpreter,compiler}` in
  `nes-systests/systest/CMakeLists.txt`, each ending with:
  ```cmake
  set_tests_properties(systest_agg_... PROPERTIES LABELS "Systest" PROCESSORS ...)
  ```
  → **These carry the `Systest` label and ARE EXCLUDED by `ctest -LE 'Systest|DockerCompose'`.**
- DockerCompose-labeled tests are separate (runtime-base image, distributed CLI/REPL, `systest-remote-test`) — irrelevant here.
- The systest **runner's own unit tests** (`nes-systests/systest/tests/`: `slt-parser-test`, `slt-e2e-test`) are registered
  with plain `add_nes_test` → **NOT labeled** → they DO run in the gate. (They test the parser/runner, not query results.)

### Recommended home + macro for the spike's integration test
The spike must prove **(a) correct aggregate results AND (b) that spilling occurred** (backend non-empty / resident bytes
capped) for one windowed-aggregation query under a small budget. Two routes:

- **Route 3a — systest `.test` (results correctness, full live path).** Add a `.test` file under
  `nes-systests/operator/aggregation/` (e.g. `WindowAggregationSpill.test`), running with `--worker...spill.enabled=true`
  + a tiny `soft_threshold_mb`. This exercises the *real* lowering→handler→store path. **BUT** it runs only via the
  `systest_agg_*` ctest entries, which are **Systest-labeled → excluded from the regression gate**; it must be run
  explicitly (e.g. `ctest -L Systest -R systest_agg` or invoking the `systest` binary directly). It also cannot easily
  assert "spilling occurred" (only result correctness via expected output) unless the query is sized so it would OOM
  without spill. CMake change: the existing `systest_agg_*` invocations already pick up all `[Aggregation]`-group `.test`
  files, but they do NOT pass `spill.enabled=true`; proving spill needs **either** a new dedicated ctest entry that passes
  the spill worker flags (would be Systest-labeled), **or** the `.test`'s own per-query config. This is heavier and
  outside the gate — treat as the **realism/validation** check, not the primary TDD gate.

- **Route 3b — C++ integration test in `nes-physical-operators/tests` (PRIMARY gate, recommended for Increment A).**
  Drive `SpillableTimeBasedSliceStore` directly with an `InMemorySpillBackend` (already built, header-only) or a
  `RocksDBSpillBackend` on a temp dir: insert enough slices/keys to exceed a tiny budget, force/observe spill (D4 budget
  math + D3 unspill-on-read), and assert results after unspill. Register with `add_nes_physical_operator_test(...)`
  → **no label → included in `ctest -LE 'Systest|DockerCompose'`**. This is the cheapest, gate-included proof of the
  lifetime/spill path for the single-threaded spike (matches the brief's "Integration (the spike, gate of value)" intent
  while staying inside the regression gate). It does NOT exercise the lowering rule / live query engine, so pair it with
  a manual Route-3a run for end-to-end confidence.

**Decision for the plan:** Increment A's TDD gate = **Route 3b** (C++ integration test in `nes-physical-operators/tests`,
unlabeled, in-gate). Use a **Route 3a `.test`** as an out-of-gate end-to-end smoke (Systest-labeled, run manually) if a
true live-query proof is wanted. Do **not** add a Systest-labeled ctest entry expecting the regression gate to run it.

---

## 4. Header placement

- New store header: `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
  (sibling of `DefaultTimeBasedSliceStore.hpp`, `RocksDBSpillBackend.hpp`, `SpillBackend.hpp` — all already in
  `include/SliceStore/`). Include form: `#include <SliceStore/SpillableTimeBasedSliceStore.hpp>`.
- **No CMake change for the header.** `target_include_directories(nes-physical-operators PUBLIC .../include)` publishes the
  whole tree; new headers are picked up automatically (no per-header registration anywhere in this module).
- **Un-final-ing `DefaultTimeBasedSliceStore` (D2) needs NO include changes.** `DefaultTimeBasedSliceStore.hpp` currently
  is `class DefaultTimeBasedSliceStore final : public WindowSlicesStoreInterface` (line 55) and already includes everything
  the subclass-relevant signatures use (`Slice.hpp`, `SliceStoreRef.hpp`, `DefaultTimeBasedSliceStoreRef.hpp`,
  `folly/Synchronized.h`, `AbstractBufferProvider.hpp`, `Identifiers.hpp`, `Timestamp.hpp`, `SliceCacheConfiguration.hpp`).
  The D2 edits are pure language changes inside the existing header: remove `final`; add `virtual` to `getSlicesOrCreate`,
  `getTriggerableWindowSlices`, `getSliceBySliceEnd`, `getAllNonTriggeredSlices`, `garbageCollectSlicesAndWindows`
  (all already at lines 61–67) — no new `#include` required.
- The new `SpillableTimeBasedSliceStore.hpp` will need to add `#include <SliceStore/DefaultTimeBasedSliceStore.hpp>` and
  `#include <SliceStore/SpillBackend.hpp>` itself (header content, not a CMake concern).

---

## 5. vcpkg / toolchain

- **No new dependency for Increment A.** The only third-party lib the spill stack uses is RocksDB, already provisioned in
  the Phase-3 dependency image and wired via `find_package(RocksDB CONFIG REQUIRED)`. `InMemorySpillBackend` /
  `NullSpillBackend` are pure C++ (no deps). `folly::Synchronized` (used by the store) is already a dependency.
- **No `vcpkg.json` / toolchain edit, no dependency-image rebuild** is required.
- **What WOULD require a dependency-image rebuild (none of these are in Increment A scope):** adding a brand-new external
  library (e.g. switching compression to `zstd` if zstd is not already pulled by the rocksdb port, or adding a new vcpkg
  dependency). The `SpillConfig.compression` default is `lz4` which RocksDB provides; staying on lz4 needs no image change.
  Flag for the architect only if O2/O4 introduce a new lib — Increment A does not.

---

## 6. Build cost (inner-loop gates)

**Targets** (run inside Docker, build dir `cmake-build-debug`):
- Fastest compile check of the new store source: `ninja nes-physical-operators`
  (recompiles the changed `.cpp` + relinks the lib; pulls in dependents only if headers changed — un-final-ing
  `DefaultTimeBasedSliceStore.hpp` is a header change → expect rebuild of `nes-physical-operators` translation units that
  include it, plus `nes-query-compiler` since it includes `DefaultTimeBasedSliceStore.hpp` in the lowering rule).
- Build just the new unit test: `ninja SpillableTimeBasedSliceStoreTest`
  (builds the test exe + its deps `nes-physical-operators nes-data-types nes-memory-test-utils nes-test-util`).
- Full build (only when wiring touches lowering/handler broadly): `ninja` (whole project).
- For the spike, the fast inner loop is: `ninja SpillableTimeBasedSliceStoreTest` then run it.

**ctest regex for the new tests** (gtest_discover_tests registers ctest entries named per `TEST(Suite, Case)`, prefixed by
the suite — same as existing spill tests like `RocksDBSpillBackendTest.*`):
- New store unit/integration test: `ctest -R 'SpillableTimeBasedSliceStore'`
- All spill-related unit tests at once:
  `ctest -R 'Spill|SliceSerializer|RocksDB' -LE 'Systest|DockerCompose'`
- Regression gate (must stay green, baseline 797 per brief): `ctest -LE 'Systest|DockerCompose'`
- Out-of-gate live end-to-end (if a Route-3a `.test` is added): `ctest -L Systest -R systest_agg` (Docker, heavy).

---

## Exact CMake/registration edits needed (for the plan)

1. **`nes-physical-operators/src/SliceStore/CMakeLists.txt`** — register the new store source.
   In the existing `add_source_files(nes-physical-operators ...)` block, add one line after `RocksDBSpillBackend.cpp`:
   ```cmake
   add_source_files(nes-physical-operators
           Slice.cpp
           DefaultTimeBasedSliceStore.cpp
           DefaultTimeBasedSliceStoreRef.cpp
           SliceSerializer.cpp
           RocksDBSpillBackend.cpp
           SpillableTimeBasedSliceStore.cpp   # <-- ADD
   )
   ```

2. **`nes-physical-operators/tests/CMakeLists.txt`** — register the new unit test (and, if Route 3b, the integration test).
   Add after the `SpillableAggregationSliceTest` line:
   ```cmake
   add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)
   ```
   (Unlabeled → included in `ctest -LE 'Systest|DockerCompose'`.) If the end-to-end spike is done as a separate C++ file,
   add a second analogous line, e.g.:
   ```cmake
   add_nes_physical_operator_test(SpillableAggregationE2ETest SpillableAggregationE2ETest.cpp)
   ```

3. **No edit** to `nes-physical-operators/CMakeLists.txt` (RocksDB + include dir already correct from Phase 3).

4. **No edit** to `nes-query-compiler/CMakeLists.txt` **iff** the `RocksDBSpillBackend` is constructed inside
   nes-physical-operators (recommended). It already links `nes-physical-operators` PUBLIC, so the lowering rule can include
   and construct `SpillableTimeBasedSliceStore` with no change. **Only if** the architect builds the rocksdb backend in the
   lowering rule itself would `RocksDB::rocksdb` need adding to nes-query-compiler's `target_link_libraries` — avoid this.

5. **No header-registration edit anywhere** for `include/SliceStore/SpillableTimeBasedSliceStore.hpp` (auto-published).

6. **No vcpkg / toolchain / dependency-image change.**

### Optional (out-of-gate, only if a live end-to-end `.test` is wanted)
7. **`nes-systests/operator/aggregation/WindowAggregationSpill.test`** (new `.test`, group `[Aggregation]`) +, to actually
   pass `spill.enabled`, a dedicated ctest entry in `nes-systests/systest/CMakeLists.txt` mirroring the `systest_agg_*`
   block but adding `--worker...spill.enabled=true --worker...spill.soft_threshold_mb=<small>`. This entry **will be
   Systest-labeled** (per the existing pattern) → excluded from the regression gate; run manually. Not required for the
   Increment A TDD gate.
