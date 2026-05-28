# BUILD.md — Step 2c0 build/test surface & leak-detection verdict

READ-ONLY exploration. No code modified.

Repo: `nebulastream-Out-of-Core-Stream-Processing`, branch `feat/ooc-phase1-spill-skeleton` @ `05c8520dbb`.
Builds/tests run **only in Docker**: image `nebulastream/nes-development:local-rocksdb`, repo mounted at
`/workspace`, build dir `cmake-build-debug`, configured triplet **`arm64-linux-none-local`** (no sanitizer —
see §1). `ninja` is run **UNPIPED**. Regression gate: `ctest -j8 -LE 'Systest|DockerCompose' --timeout 200`.
Baseline (PREFLIGHT.md:8): **835 passing, 0 failed (1 disabled), ~87 s**.

Scope of 2c0 (DESIGN-BRIEF.md §5): handler-owned registry ownership refactor of the EXISTING single-chunk
aggregation emit, P=1, behavior-identical, plus a NEW leak-detection unit test (§7 leak gate). Files touched
by the refactor: `AggregationOperatorHandler.{hpp,cpp}`, `AggregationProbePhysicalOperator.cpp`,
`WindowBasedOperatorHandler.cpp` — all already compiled into the `nes-physical-operators` library.

---

## TL;DR for the plan

- **ASAN is wired but NOT usable in the local image, and the project's gate does NOT do leak detection.**
  ASAN/UBSAN/TSAN exist as a CMake config (`cmake/Sanitizers.cmake`) and per-sanitizer **vcpkg triplets**
  (`vcpkg/custom-triplets/arm64-linux-address-*.cmake`), but the sanitizer is **baked into the Docker image's
  prebuilt vcpkg SDK at image-build time** (`VCPKG_TARGET_TRIPLET = arm64-linux-${SANITIZER}-${STDLIB}`,
  `cmake/ImportDependencies.cmake:126`). The `local-rocksdb` image and the existing `cmake-build-debug` are the
  **`none`** triplet (`cmake-build-debug/CMakeCache.txt`: `USE_SANITIZER=none`,
  `VCPKG_TARGET_TRIPLET=arm64-linux-none-local`). You cannot flip `-DUSE_SANITIZER=address` against this image —
  the deps were not built for the address triplet. **Decisively:** even upstream CI's address-sanitizer job runs
  with **`ASAN_OPTIONS="detect_leaks=0"`** (`.github/.env/test-address.env`) — i.e. **LSAN leak detection is OFF
  in the gate**. So the project does not rely on a sanitizer to catch leaks.
- **➡ Primary mechanism = the portable counter-based fallback.** Instrument the handler's `chunkRegistry`
  (added by 2c0, L8/G3) with a free counter + a `size()` accessor; in a normal gtest assert
  `frees == chunksEmitted` and `registry empty` at teardown across the three teardown variants. This is feasible
  **and** is the only mechanism that runs in the `none` (default) image and inside the `-LE 'Systest|DockerCompose'`
  gate. ASAN-as-use-after-free coverage is a *bonus* available only on an address-triplet image (out-of-gate).
- **No new source file for the refactor** — the registry lives in the existing
  `AggregationOperatorHandler.{hpp,cpp}`. Only **ONE new test file** + **ONE line** in
  `nes-physical-operators/tests/CMakeLists.txt`.
- **No vcpkg / toolchain / dependency-image change.** Pure C++ refactor + a gtest. Confirmed in §6.

---

## 1. Leak / memory-safety detection in this project (THE KEY UNKNOWN)

### 1a. Is ASAN/LSAN available? — Wired, but image-gated and leak-detection-OFF

**Sanitizer build config exists** (`cmake/Sanitizers.cmake:13-41`):
```cmake
set(USE_SANITIZER "none" CACHE STRING "...Thread, Address, Undefined, None (default)")
if (DEFINED ENV{VCPKG_SANITIZER}) SET(SANITIZER_OPTION $ENV{VCPKG_SANITIZER}) ...
elseif (SANITIZER_OPTION STREQUAL "address")
    add_compile_options(-fsanitize=address)
    add_link_options(-fsanitize=address)
```
So `-DUSE_SANITIZER=address` (or env `VCPKG_SANITIZER=address`) turns on `-fsanitize=address` on the NES code.

**But the sanitizer is part of the vcpkg triplet**, so the whole dependency SDK must be built for it
(`cmake/ImportDependencies.cmake:126`):
```cmake
SET(VCPKG_TARGET_TRIPLET "${NES_ARCH}-linux-${SANITIZER_OPTION}-${VCPKG_STDLIB}")
```
In the Docker dev image the triplet is **frozen at image build** (`docker/dependency/Dependency.dockerfile:70,83,
108-110`): `ENV VCPKG_SANITIZER=${SANITIZER}` (default `ARG SANITIZER="none"`), and
`cmake/ImportDependencies.cmake:70` forces `SANITIZER_OPTION = $ENV{VCPKG_SANITIZER}` when
`NES_PREBUILT_VCPKG_ROOT` is set (it is, in the dev image). **You cannot override it from the cmake line in that
image** — the prebuilt deps only exist for the image's one triplet.

**The existing build dir is the `none` triplet** (`cmake-build-debug/CMakeCache.txt`):
```
CMAKE_BUILD_TYPE:STRING=Debug
USE_SANITIZER:STRING=none
VCPKG_TARGET_TRIPLET:STRING=arm64-linux-none-local
```
→ The `local-rocksdb` image used for 2c0 is a **non-sanitized** image. ASAN/LSAN is **not available** here.

**How CI uses sanitizers** (for context, not reproducible locally): a *separate per-sanitizer image* per matrix
row — `nebulastream/nes-ci:<tag>-${stdlib}-${sanitizer}` (`.github/workflows/build_and_test.yml:62-70`), with
`source .github/.env/test-${sanitizer}.env` before build & ctest (`:73, :89`). The address matrix row exists
(`.github/ci/pr_matrix.yaml:33`, `nightly_matrix.yaml:25`), but its env (`.github/.env/test-address.env`) is:
```
export ASAN_OPTIONS="detect_leaks=0"
```
**LSAN is explicitly disabled** — ASAN there catches use-after-free / heap-buffer-overflow / double-free, **not
leaks**. (`test-none.env` is identical; `test-undefined.env` only sets `ADDITIONAL_CTEST_ARGS`.) The ctest line
in every sanitizer row is the same gate: `ctest ... -LE "Systest|DockerCompose" ...`
(`build_and_test.yml:89`).

**Verdict (1a):** There is **no leak-asserting sanitizer build in this project's gate**. ASAN is available only
as a separate image (and even then with leaks off). A 2c0 leak test that depends on LSAN would (a) not run in the
`local-rocksdb` image at all, and (b) not be exercised by the regression gate even on the address image. **Do not
make the leak gate depend on LSAN.**

### 1b. Portable counter-based fallback (RECOMMENDED PRIMARY) — feasible, confirmed

The refactor (DESIGN-BRIEF L8 / G3 = Option 2) adds a handler-owned registry on `AggregationOperatorHandler`:
a mutex-guarded `map<(SequenceNumber, ChunkNumber), ChunkMaps>` where `ChunkMaps` holds
`vector<unique_ptr<HashMap>> inputs + unique_ptr<HashMap> finalAccumulator`. Inserted at emit (in
`triggerSlices`, `AggregationOperatorHandler.cpp:144` region), released per-chunk on probe drain-ack (the swap of
`AggregationProbePhysicalOperator.cpp:160-169`), swept at teardown in
`WindowBasedOperatorHandler::stop` (`WindowBasedOperatorHandler.cpp:50`, currently an empty body — confirmed).

Because the registry lives **on the handler and the test owns the handler**, the test can observe state directly,
with no sanitizer. Recommended concrete instrumentation (all test-visible, debug-cheap, on the registry owner):

1. **`size()` / `empty()` accessor** on the registry (or a `registrySize()` getter on the handler) — assert
   `== 0` after a full drain and after `stop()`.
2. **A monotonically-incrementing free counter** bumped exactly where a `ChunkMaps` entry is erased (one bump per
   chunk released, whether via drain-ack or the `stop()` sweep). Pair with an **emit counter** bumped where a
   chunk is inserted. Assert `frees == chunksEmitted` at teardown. (`std::atomic<uint64_t>` members or a
   `folly::Synchronized` field, mirroring `rollingAverageNumberOfKeys` already on the handler,
   `AggregationOperatorHandler.hpp:79`.)
3. **(optional, strongest)** a static/`std::atomic` live-instance counter inside the `HashMap` subtype used (or a
   test-local allocation tally on the `BufferManager`) to assert **zero live `HashMap`s** and that pages returned
   to the pool. This corroborates that erasing a `ChunkMaps` actually destroyed the maps — and it does:
   `~ChainedHashMap()` (`nes-nautilus/.../ChainedHashMap.cpp:111-113`) calls `clear()` (`:236-258`) which clears
   `storageSpace` → pages (TupleBuffers) returned to the pool. So `unique_ptr<HashMap>` destruction on registry
   erase is a real, observable reclamation, not a no-op.

This is feasible for all three §7 teardown variants because each is reachable from a C++ test driving the handler:
- **(a) clean end-of-stream / full drain:** drive emit → drain-ack → assert per-chunk free and empty registry.
- **(b) `stop()` mid-flush:** populate the registry (emit without draining all chunks), call
  `handler.stop(...)`, assert the sweep emptied it and `frees == chunksEmitted`.
- **(c) worker/engine shutdown with chunks queued:** same as (b) at the unit level — undrained entries remain in
  the registry and must be reclaimed by `stop()`; assert `empty()` and the counter after `stop()`. (The
  buffer being dropped undrained is harmless by construction post-refactor — it carries only raw pointers — so the
  registry + `stop()` sweep is the totality proof; this is exactly what the gate must assert.)

**Verdict (1b):** the counter/`size()`-based gate is **the recommended primary mechanism**, runs in the default
`none` image, and is included by `-LE 'Systest|DockerCompose'`. ASAN (separate image, leaks-off) can be run
manually as a use-after-free corroboration but is **not the leak gate**.

> Note for the planner: the leak test must observe the registry, so the registry's `size()`/counter accessors
> should be **public (or test-friended)** on `AggregationOperatorHandler`. There is **no** C++ test today that
> drives `AggregationOperatorHandler` end-to-end (the only handler-level coverage is the slice/store unit tests);
> the 2c0 test is the first. Driving a full emit→probe→drain through the real query engine from a unit test is
> heavy — the pragmatic gate drives the handler's emit/registry seam directly (insert via `triggerSlices` path or
> a thin test seam) and asserts the registry invariant, which is precisely what proves totality.

---

## 2. Test registration recipe

### Macro + location
- **Macro:** `add_nes_physical_operator_test(<TargetName> <Sources...>)` defined at
  `nes-physical-operators/CMakeLists.txt:13-17`:
  ```cmake
  function(add_nes_physical_operator_test)
      add_nes_test(${ARGN})
      set(TARGET_NAME ${ARGV0})
      target_link_libraries(${TARGET_NAME} nes-data-types nes-physical-operators nes-memory-test-utils nes-test-util)
  endfunction()
  ```
  It wraps `add_nes_test` (`cmake/NebulaStreamTest.cmake:21-36`), which builds the exe, links
  `nes-test-util nes-logger-bindings`, and registers via `gtest_discover_tests(...)` — **sets NO LABELS**.
- **CMakeLists to edit:** `nes-physical-operators/tests/CMakeLists.txt` (lines 19-29 are the existing
  `add_nes_physical_operator_test(...)` block).
- **Test enable gate:** the whole `tests/` subdir is added via `add_tests_if_enabled(tests)`
  (`nes-physical-operators/CMakeLists.txt:33` → `cmake/macros.cmake:92-97`), guarded by
  `NES_ENABLES_TESTS` (default **ON**, `CMakeLists.txt:25`). Already ON in `cmake-build-debug`.

### Example to mirror
`SpillableAggregationSliceTest` is the closest precedent (same module, exercises the aggregation slice/HashMap
lifecycle, uses `BaseUnitTest` + a `BufferManager`):
- Registration: `nes-physical-operators/tests/CMakeLists.txt:27`
  ```cmake
  add_nes_physical_operator_test(SpillableAggregationSliceTest SpillableAggregationSliceTest.cpp)
  ```
- Fixture shape: `nes-physical-operators/tests/SpillableAggregationSliceTest.cpp:46-116` — `class X :
  public BaseUnitTest`, `SetUpTestSuite()` sets up logging, `SetUp()` calls `BaseUnitTest::SetUp()` +
  `bufferManager = BufferManager::create();`, helpers build/inspect `ChainedHashMap`s directly. The new leak test
  should mirror this fixture and additionally construct an `AggregationOperatorHandler` to observe its registry.

### The edit (one line)
Add after line 27 (or 29) of `nes-physical-operators/tests/CMakeLists.txt`:
```cmake
add_nes_physical_operator_test(AggregationOperatorHandlerLeakTest AggregationOperatorHandlerLeakTest.cpp)
```
New file: `nes-physical-operators/tests/AggregationOperatorHandlerLeakTest.cpp` (sibling of the existing tests).
(Name is a suggestion; any `*Test` name works.)

### ctest label & gate inclusion
- `gtest_discover_tests` (`cmake/NebulaStreamTest.cmake:34`) sets **no `LABELS`** → the new test carries **no
  label**. The `Systest`/`DockerCompose` labels are applied **only** to systest/docker entries
  (`nes-systests/systest/CMakeLists.txt:87,95,...`; `nes-frontend/apps/CMakeLists.txt:83,95`;
  `CMakeLists.txt:245`).
- **Therefore the new test IS included by** `ctest -LE 'Systest|DockerCompose'` (the regression gate). ✅
- ctest entry names are the gtest `Suite.Case` names (PRE_TEST discovery), e.g.
  `AggregationOperatorHandlerLeakTest.cleanDrainNoLeak` — so `ctest -R 'AggregationOperatorHandlerLeak'` selects
  the whole suite (same pattern as the existing `RocksDBSpillBackendTest.*` / `SpillableAggregationSliceTest.*`).

---

## 3. Module / target wiring (confirm no new files for the refactor)

- **All three refactored files compile into the single `nes-physical-operators` library** via `add_source_files`:
  - `AggregationOperatorHandler.cpp` — `nes-physical-operators/src/Aggregation/CMakeLists.txt:18`
  - `AggregationProbePhysicalOperator.cpp` — `nes-physical-operators/src/Aggregation/CMakeLists.txt:19`
  - `WindowBasedOperatorHandler.cpp` — `nes-physical-operators/src/CMakeLists.txt:26`
  Sources are gathered into a global property and linked once in `nes-physical-operators/CMakeLists.txt:18-21`.
- **The registry lives in the existing `AggregationOperatorHandler.{hpp,cpp}`** (confirmed: the struct
  `EmittedAggregationWindow` and the handler class are both in `include/Aggregation/AggregationOperatorHandler.hpp`
  lines 39-83; emit/placement-new in `src/Aggregation/AggregationOperatorHandler.cpp:144`; probe cleanup lambda in
  `src/Aggregation/AggregationProbePhysicalOperator.cpp:160-169`; the empty `stop()` in
  `src/WindowBasedOperatorHandler.cpp:50`). **No new source/header file is required for the refactor** — only edits
  to existing files.
- **No CMake edit for the refactor's source/headers.** Headers under `include/` are published wholesale via
  `target_include_directories(nes-physical-operators PUBLIC .../include)` (`nes-physical-operators/CMakeLists.txt:27`);
  editing existing `.cpp`/`.hpp` needs no registration change. The `.cpp` files are already in their
  `add_source_files` lists.
- **Only NEW file in 2c0 = the one test file** (§2).

---

## 4. Exact build / test commands

All inside the container; build dir mounted at `/workspace/cmake-build-debug`. `ninja` is **UNPIPED** (no
`| tail`/`| head` — pipefail does not cross the container boundary). Pattern (from
`.planning/eval-phase/EXPLORE-INFRA.md:40` and `E0-SMOKE-RESULTS.md:70-74`):
`docker run --rm --workdir /workspace -v "$PWD:/workspace" nebulastream/nes-development:local-rocksdb <cmd>`.

**(a) Build just the affected handler/library target** (recompiles the changed `.cpp`s + relinks the lib; the
header change to `AggregationOperatorHandler.hpp` will rebuild this lib's TUs that include it, and dependents that
include it — primarily within `nes-physical-operators`):
```
ninja -C /workspace/cmake-build-debug nes-physical-operators
```

**(b) Build + run just the new leak test** (builds the test exe + its deps, then runs only that suite):
```
ninja -C /workspace/cmake-build-debug AggregationOperatorHandlerLeakTest
ctest --test-dir /workspace/cmake-build-debug -R 'AggregationOperatorHandlerLeak' --output-on-failure
```
(Adjust the `-R` regex to the chosen suite name.)

**(c) Full regression gate** (must stay green; baseline 835 passing):
```
ninja -C /workspace/cmake-build-debug
ctest --test-dir /workspace/cmake-build-debug -j8 -LE 'Systest|DockerCompose' --timeout 200 --output-on-failure
```

Optional grouped spill/aggregation check while iterating:
```
ctest --test-dir /workspace/cmake-build-debug -R 'Aggregation|Spill|SliceSerializer' -LE 'Systest|DockerCompose' --output-on-failure
```

> Reconfigure is NOT needed for editing existing sources; it IS needed once after adding the new test target to
> `tests/CMakeLists.txt` (Ninja re-runs CMake automatically on the next `ninja` because the CMakeLists changed).

---

## 5. vcpkg / toolchain implications — NONE (confirmed)

- 2c0 is a **pure C++ refactor of existing code + one gtest**. It adds **no external library**: the registry uses
  `std::map` + `std::mutex`/`folly::Synchronized` (already a dependency, used on the handler today,
  `AggregationOperatorHandler.hpp:79`) + `std::unique_ptr`. The test uses gtest + `BaseUnitTest` +
  `BufferManager` + `ChainedHashMap` — all already linked by `add_nes_physical_operator_test`.
- **No `vcpkg.json` edit, no triplet change, no dependency-image rebuild.** RocksDB/OpenSSL linkage on
  `nes-physical-operators` (`CMakeLists.txt:22-26`) is untouched and irrelevant to this step (the handler/probe do
  not touch `rocksdb::`).
- **Caveat that does NOT apply here:** switching to a sanitizer build (address) would require a *different
  dependency image / triplet* (§1a) — but the recommended leak gate is counter-based and runs on the existing
  `none` image, so **no toolchain change is needed**.

---

## Citations (file:line)

- Sanitizer config: `cmake/Sanitizers.cmake:13-41`; triplet embeds sanitizer `cmake/ImportDependencies.cmake:126`;
  prebuilt-image forces it `:70`; image bakes it `docker/dependency/Dependency.dockerfile:83,108-110`;
  overlay triplets `vcpkg/custom-triplets/arm64-linux-address-{libcxx,local}.cmake`.
- Current build is `none`: `cmake-build-debug/CMakeCache.txt` (`USE_SANITIZER=none`,
  `VCPKG_TARGET_TRIPLET=arm64-linux-none-local`).
- CI sanitizer images + env + gate: `.github/workflows/build_and_test.yml:62-70,73,89`;
  `.github/.env/test-address.env` (`ASAN_OPTIONS="detect_leaks=0"`); matrix rows
  `.github/ci/pr_matrix.yaml:33`, `.github/ci/nightly_matrix.yaml:25,147`.
- Test macro: `nes-physical-operators/CMakeLists.txt:13-17`; `cmake/NebulaStreamTest.cmake:21-36` (no LABELS);
  enable gate `cmake/macros.cmake:92-97` + `CMakeLists.txt:25`.
- Test list + example: `nes-physical-operators/tests/CMakeLists.txt:13-29`;
  example fixture `nes-physical-operators/tests/SpillableAggregationSliceTest.cpp:46-116`.
- Systest/Docker labels (proves gate exclusion is scoped): `nes-systests/systest/CMakeLists.txt:87,95`;
  `CMakeLists.txt:245`; `nes-frontend/apps/CMakeLists.txt:83,95`.
- Refactor targets: `nes-physical-operators/src/Aggregation/CMakeLists.txt:18-19`;
  `nes-physical-operators/src/CMakeLists.txt:26`; lib link `nes-physical-operators/CMakeLists.txt:18-29`.
- Registry seams: emit/placement-new `src/Aggregation/AggregationOperatorHandler.cpp:144` (buffer alloc `:120`);
  probe cleanup lambda `src/Aggregation/AggregationProbePhysicalOperator.cpp:160-169` (currently
  `finalHashMap.reset()`); empty `stop()` `src/WindowBasedOperatorHandler.cpp:50`;
  handler struct/class `include/Aggregation/AggregationOperatorHandler.hpp:39-83`.
- Page reclamation on map destruction: `~ChainedHashMap()` `nes-nautilus/.../ChainedHashMap.cpp:111-113` → `clear()`
  `:236-258`.
- Build/test invocation + Docker mount: `.planning/dev-eng-cpp-phase2/2c0/PREFLIGHT.md:5-8`;
  `.planning/eval-phase/EXPLORE-INFRA.md:40-45`; `.planning/eval-phase/E0-SMOKE-RESULTS.md:70-89`.
