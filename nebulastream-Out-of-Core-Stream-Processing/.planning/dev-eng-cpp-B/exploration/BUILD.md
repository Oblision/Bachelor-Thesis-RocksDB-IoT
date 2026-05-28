# Explorer 2 — Build, Sanitizers & Concurrency Test Infrastructure (Phase 4b Increment B)

READ-ONLY exploration. Repo @ `feat/ooc-phase1-spill-skeleton` `6a6ec7301b`.
Build/test only in Docker `nebulastream/nes-development:local-rocksdb`, dir `cmake-build-debug`, ninja + ctest.

Headline finding for O-B4: **A ThreadSanitizer build is NOT practically runnable with the user's
current `local-rocksdb` image.** TSan is wired through vcpkg custom triplets, meaning ALL dependencies
(rocksdb, folly, abseil, llvm, ...) must be rebuilt with `-fsanitize=thread`. The user's image is baked
with `VCPKG_SANITIZER=none`, and in the docker path CMake *overwrites* `-DUSE_SANITIZER` from that env
var — so `-DUSE_SANITIZER=thread` is silently ignored. Getting TSan requires building a brand-new tsan
dependency image from scratch (multi-hour, downloads tsan-instrumented LLVM + rebuilds every dep, incl.
the project-local rocksdb dep which has no published remote tsan image). This materially shapes B's
concurrency validation: **plan for multi-threaded stress tests as the primary in-gate signal**, treat
TSan as an optional/escalation path, not the default gate.

---

## 1. ThreadSanitizer feasibility (O-B4)

### How the sanitizer is wired
- `cmake/Sanitizers.cmake:13` — `set(USE_SANITIZER "none" CACHE STRING ...)`, options: `none|thread|address|undefined`.
  - `:22-25` thread → `add_compile_options(-fsanitize=thread)` + `add_link_options(-fsanitize=thread)`.
- **CRITICAL override** — `cmake/ImportDependencies.cmake:70`: inside the docker dev image
  (`NES_PREBUILT_VCPKG_ROOT` set → `DOCKER_DEV_IMAGE` ON at `:31`), CMake does
  `SET(SANITIZER_OPTION $ENV{VCPKG_SANITIZER})` **unconditionally**, clobbering the `USE_SANITIZER` cache.
  So in the user's image, `-DUSE_SANITIZER=thread` on the cmake line is ignored; the effective sanitizer
  is whatever the image was baked with.
- Verified the user's image env (docker inspect `nebulastream/nes-development:local-rocksdb`):
  `VCPKG_SANITIZER=none`, `VCPKG_STDLIB=libstdcxx`, `NES_PREBUILT_VCPKG_ROOT=/vcpkg`,
  `VCPKG_DEPENDENCY_HASH=e2e95dcd…`. → sanitizer is **none**, cannot be flipped at configure time.
- Existing `cmake-build-debug/CMakeCache.txt`: `USE_SANITIZER:STRING=none`. (Reusing this dir for TSan is
  impossible regardless — it's a `none` configure.)

### Why TSan needs sanitizer-instrumented deps (not just the project)
- `docker/dependency/Dependency.dockerfile`:
  - `:13 ARG SANITIZER="none"`, `:17` downloads `nes-llvm-…-${SANITIZER}-${STDLIB}.tar.zstd` (a separate
    *tsan-instrumented* LLVM binary), `:83` installs deps with triplet `${ARCH}-linux-${SANITIZER}-${STDLIB}`,
    `:110 ENV VCPKG_SANITIZER=${SANITIZER}` baked into the final image.
- `vcpkg/custom-triplets/bits/sanitizers/tsan.cmake:13-14` — every vcpkg port is compiled with
  `VCPKG_CXX_FLAGS=-fsanitize=thread` / `VCPKG_C_FLAGS=-fsanitize=thread` (LLVM port special-cased to
  `-DLLVM_USE_SANITIZER="Thread"`). i.e. **rocksdb, folly, abseil, etc. are recompiled under TSan.**
- Mixing a `none`-built dep set with a TSan-built project is unsupported (link/runtime breakage; partial
  instrumentation → floods of false positives). Confirmed indirectly by the suppressions below.

### The libstdcxx wrinkle (extra cost / noise)
- The image uses `libstdcxx`, which is NOT sanitizer-instrumented. `.github/.env/tsan_suppressions.txt`
  exists *specifically* to silence the resulting false races in `shared_ptr`/`weak_ptr`/`future`/`promise`
  refcounting (`race:_Sp_counted_base`, `race:__shared_count`, `race:weak_ptr`, …, TODO #808 "until we
  introduce a sanitized standard library"). So even a correct TSan build over libstdcxx needs
  `TSAN_OPTIONS=suppressions=…` or it drowns in stdlib noise.

### Cost estimate to get a runnable TSan build
- **No reuse of `cmake-build-debug`** — needs a fresh configure dir (it's a `none` configure).
- **No prebuilt remote tsan image with rocksdb.** rocksdb is a *project-local* default dep
  (`vcpkg/vcpkg.json:53`, added by this branch's commit f245b9c8d8). Remote dev images
  (`get_dev_images.yml` matrix does include `thread`) are upstream and predate rocksdb, so they won't have
  the matching `VCPKG_DEPENDENCY_HASH`. → user must build the tsan dep image **locally from scratch**:
  `scripts/install-local-docker-environment.sh -l --libstdcxx --thread --name rocksdb-tsan`
  This downloads tsan LLVM and rebuilds the **entire vcpkg dep set** under `-fsanitize=thread`
  (rocksdb/folly/abseil/grpc/llvm-mlir/…). Estimate: **multi-hour (≈2–6h+) one-time** build, several GB.
- After that, the project itself is a **full TSan rebuild of nes-physical-operators + transitive libs**
  (TSan instrumentation is whole-program; a Debug TSan build is meaningfully slower to compile and ~2–10×
  slower at runtime — hence the BaseUnitTest timeout-doubling below).
- **You cannot build "just the spill tests" under TSan cheaply** — the test links `nes-physical-operators`,
  `nes-memory`, `nes-nautilus`, `nes-data-types`, etc., all of which must be TSan-instrumented. So the dep
  image + a from-scratch instrumented build of that whole library subtree is unavoidable. Once configured,
  you *can* limit the build/run to the spill test target (below), but the first configure pays full freight.

### Recommended TSan command sequence (only if the user opts into the multi-hour image build)
```bash
# 0. ONE-TIME: build a thread-sanitizer dependency+dev image that also has rocksdb (slow, hours)
scripts/install-local-docker-environment.sh -l --libstdcxx --thread --name rocksdb-tsan
#    → produces nebulastream/nes-development:local-rocksdb-tsan (VCPKG_SANITIZER=thread)

# 1. configure a SEPARATE build dir inside that image (do NOT reuse cmake-build-debug)
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb-tsan \
  cmake -GNinja -B cmake-build-tsan -DUSE_SANITIZER=thread -DCMAKE_BUILD_TYPE=Debug -DNES_LOG_LEVEL=DEBUG
#    (USE_SANITIZER=thread now MATCHES the image's VCPKG_SANITIZER, so it sticks)

# 2. build ONLY the spill test target(s)
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb-tsan \
  cmake --build cmake-build-tsan --target SpillableTimeBasedSliceStoreTest -j -- -k 0

# 3. run under TSan with the repo's suppressions
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb-tsan bash -c '
    source .github/.env/test-thread.env   # sets TSAN_OPTIONS=suppressions=$PWD/.github/.env/tsan_suppressions.txt
    ctest --test-dir cmake-build-tsan -R "Spill" --output-on-failure'
```
- TSan runtime config pattern is established: `.github/.env/test-thread.env` exports
  `TSAN_OPTIONS="suppressions=$PWD/.github/.env/tsan_suppressions.txt"`. (NOTE: `.github/.env/` is
  permission-blocked from direct reads in this sandbox; contents retrieved via `git show HEAD:…`.)
- `endless-mode.yml:49-68` is the canonical CI TSan recipe: image `nes-ci:…-libstdcxx-thread`,
  `cmake -DUSE_SANITIZER=thread`, `source .github/.env/test-thread.env`, run.
- The codebase already self-adjusts under TSan: `nes-common/tests/Util/include/BaseUnitTest.hpp:97-102`
  uses `__has_feature(thread_sanitizer)` to double the per-test wait timeout (5→10 min). New stress tests
  should keep iteration counts modest so they don't blow ctest timeouts under TSan slowdown.
- Also `CMakeLists.txt:66-71`: when `SANITIZER_OPTION==thread`, `NES_LOG_WITH_STACKTRACE` is force-disabled
  ("prohibitively slow under TSAN") — expect different logging under a TSan build.

### Bottom line (O-B4)
TSan is *architecturally supported and CI-exercised*, but **not runnable in the user's current setup
without a multi-hour from-scratch instrumented dep-image build (no remote tsan+rocksdb image exists)**.
Recommend: primary gate = deterministic + multi-threaded **stress tests** (no sanitizer); TSan = optional,
escalate if a race is suspected and the user accepts the image-build cost. Be explicit in SYNTHESIS that
in-gate concurrency coverage is stress-based, not TSan-proven, unless the user builds the tsan image.

---

## 2. Existing concurrency tests to mirror

Threaded tests found (grep `std::thread|jthread`): `nes-input-formatters/tests/UnitTests/ConcurrentSynchronizationTest.cpp`,
`nes-common/tests/UnitTests/Util/NonBlockingMonotonicSeqQueueTest.cpp`, `…/ChunkCollectorTest.cpp`,
`nes-query-engine/tests/{TaskQueueTest,DelayedTaskSubmitterTest,CallbackTest}.cpp`,
`nes-memory/tests/UnpooledBufferTests.cpp`, `nes-executable/tests/BackpressureChannelTest.cpp`.

### BEST TEMPLATE → `nes-input-formatters/tests/UnitTests/ConcurrentSynchronizationTest.cpp`
This is the cleanest multi-thread stress harness in the tree and maps directly onto B's needs:
- Fixture is a plain `::testing::Test` (NOT BaseUnitTest) — fine; BaseUnitTest's value here is just the
  wait-thread timeout, which the stress test can manage itself. (Spill tests currently use BaseUnitTest;
  either works.)
- `TestThreadPool<NUM_THREADS>` template (`:50`): fixed `std::array<std::jthread, NUM_THREADS> threads`
  (`:106`), spawns N `std::jthread`s in the ctor (`:79-86`), each running `threadFunction` (`:112`).
- Coordination: `std::latch completionLatch{NUM_THREADS}` (`:109`); workers `count_down()` at end (`:180`),
  driver `waitForCompletion()` → `completionLatch.wait()` (`:90`).
- Work distribution: shared `std::atomic<size_t> currentSequenceNumber` with `fetch_add(1)` claim loop
  (`:122-123`) — clean lock-free work-stealing pattern, reusable for "N workers each grab a key/timestamp".
- **Race-freedom assertion = deterministic invariant, not "no crash":** thread-local checksums
  (`std::array<…> threadLocalCheckSum`, `:107`) summed and compared to an analytically-known total
  (`:92-100`, `ASSERT_EQ(checkSum, upperBound)` at `:195`). This is the right model for B: assert the
  **final aggregate equals the single-threaded reference** (correctness under contention), plus no UAF.
- Multi-iteration driver: `TEST_F …multiThreadedExhaustiveTest` (`:199-209`) loops `numIterations` with
  `numThreads=16`. For Increment B, raise `numIterations` (heisenbugs need repetition) and keep per-iter
  work bounded.

Mirror this for B-concurrency: N worker `jthread`s doing build (insert into resident filling slices) +
a trigger thread (unspill on read) + an eviction thread (spill cold), with:
- An atomic op counter / `std::latch` for completion.
- A deterministic post-condition: spilled-then-unspilled aggregates == reference; assert no slice was
  spilled while a worker held its `HashMap*` (the async-probe-drain invariant) and no double-spill.

Secondary references: `NonBlockingMonotonicSeqQueueTest.cpp` (atomic seq structure under threads),
`nes-memory/tests/UnpooledBufferTests.cpp` (BufferManager/unpooled-buffer contention — relevant since
varsized spill allocates unpooled buffers via `getUnpooledBuffer`).

---

## 3. Build cost / inner loop

### Library target structure
- All SliceStore + serializer sources compile into ONE library `nes-physical-operators`
  (`nes-physical-operators/src/SliceStore/CMakeLists.txt`: `add_source_files(nes-physical-operators …
  DefaultTimeBasedSliceStore.cpp, SliceSerializer.cpp, SpillableTimeBasedSliceStore.cpp, …)`).
  Var-sized work (`SpillableAggregationSlice.cpp` includes `SliceSerializer.hpp`) is in
  `nes-physical-operators/src/Aggregation/`. ⇒ a code change to any of these = rebuild
  `nes-physical-operators` (one lib) + relink dependents.

### Blast radius of touching headers
- `DefaultTimeBasedSliceStore.hpp` (O-B1 option b/c "make maps protected") is included by only **6 TUs**:
  `nes-physical-operators/{src/SliceStore/DefaultTimeBasedSliceStore.cpp, DefaultTimeBasedSliceStoreRef.cpp,
  include/SliceStore/SpillableTimeBasedSliceStore.hpp}` and `nes-query-compiler/src/LoweringRules/
  LowerToPhysical/{LowerToPhysicalHashJoin,LowerToPhysicalWindowedAggregation,LowerToPhysicalNLJoin}.cpp`.
  → MODERATE: a header edit recompiles those 6 + transitive includers of the Spillable header. Keeps the
  805-baseline regression cost manageable but is wider than a `.cpp`-only change. (Option (a),
  self-tracking in the subclass, touches 0 base headers — narrowest blast radius for O-B1.)
- `SliceSerializer.hpp` included by 3 TUs only: `SliceSerializer.cpp`, `SpillableAggregationSlice.cpp`,
  `SliceSerializerTest.cpp`. → var-sized serializer changes are cheap to rebuild.

### Fast inner loop (in the existing `none` debug image)
```bash
# build just the changed lib (fast)
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb \
  cmake --build cmake-build-debug --target nes-physical-operators -j

# build just the spill test exes you care about
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb \
  cmake --build cmake-build-debug \
    --target SpillableTimeBasedSliceStoreTest SliceSerializerTest SpillableAggregationSliceTest -j

# run ONLY the spill tests (regex over registered gtest cases)
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" \
  nebulastream/nes-development:local-rocksdb \
  ctest --test-dir cmake-build-debug -R "Spill|SliceSerializer" --output-on-failure -j
```
- ctest `-R` regex for the spill suite: **`-R "Spill|SliceSerializer"`** matches the existing targets
  (`SpillableTimeBasedSliceStoreTest`, `SpillableAggregationSliceTest`, `InMemorySpillBackendTest`,
  `NullSpillBackendTest`, `RocksDBSpillBackendTest`, `SliceSerializerTest`). Tests are registered via
  `gtest_discover_tests` (`cmake/NebulaStreamTest.cmake:34`) so each gtest case name is individually
  selectable (`-R SpillableTimeBasedSliceStoreTest.<case>`).
- Full regression gate (per brief): `ctest --test-dir cmake-build-debug -LE 'Systest|DockerCompose'`
  (or `--output-on-failure -j`). Baseline = 805/805.
- Targets registered & confirmed present in `cmake-build-debug/build.ninja`: all 6 spill test exes above.

---

## 4. Varsized test infra

### How SliceSerializerTest builds FIXED-size entries today
`nes-physical-operators/tests/SliceSerializerTest.cpp`:
- `KEY_SIZE=8, VALUE_SIZE=8, PAGE_SIZE=256` (`:59-62`). `entrySize() = sizeof(ChainedHashMapEntry)+16` (`:66`).
- `populate(map,count)` (`:69-81`): for each i, `map.insertEntry(hash, bufferManager.get())`, then
  `memcpy` a `uint64_t key` and `uint64_t value` straight into the entry payload at
  `entry + sizeof(ChainedHashMapEntry)`. **Purely inline fixed bytes — no var-sized space touched.**
- Round-trip = `SliceSerializer::serialize(map, entrySize(), PAGE_SIZE, NUMBER_OF_BUCKETS)` →
  `deserialize(record, *bufferManager)` → compare `sortedEntries` (`:105-117`).
- Comparison helper `sortedEntries` (`:85-103`) walks chains and copies the flat `payloadSize` bytes.

### Why the current serializer is fixed-size-only (the B-varsized gap)
`nes-physical-operators/src/SliceStore/SliceSerializer.cpp`:
- `serialize` (`:57`) writes header (MAGIC,VERSION,entrySize,pageSize,numberOfBuckets,numberOfEntries)
  then per entry: `entry->hash` + a flat `payloadSize = entrySize - sizeof(ChainedHashMapEntry)` byte copy
  (`:93-96`). **It copies the in-entry bytes verbatim, never following pointers.**
- For var-sized fields, those in-entry bytes are an `int8_t*` **pointer into the map's `varSizedSpace`** —
  so a serialized record would persist a raw heap pointer (garbage after unspill). This is the Phase-2
  fixed-size guard the brief calls out.

### How var-sized data is actually represented (what the serializer must chase)
- `ChainedHashMap` (`nes-nautilus/include/.../ChainedHashMap.hpp`): two areas — fixed entry/storage space,
  plus `std::vector<TupleBuffer> varSizedSpace` (`:106`), unpooled. Allocator
  `allocateSpaceForVarSized(bufferProvider, neededSize)` (`:81`, impl `ChainedHashMap.cpp:132-148`) grabs
  an **unpooled** buffer (`getUnpooledBuffer(neededSize * NUMBER_OF_PRE_ALLOCATED_VAR_SIZED_ITEMS)`,
  pre-allocs 100) and bump-allocates a subspan.
- Var-sized layout in `varSizedSpace` (from `ChainedEntryMemoryProvider.cpp:123-147 storeVarSized`):
  `[uint32_t size][size bytes of payload]`; the entry field stores `int8_t*` to the start of that record
  (`*memoryAddressInEntry = spaceForVarSizedData.data()`). `sizeOfIndex = sizeof(uint32_t)` (`:136`).
- Read side: `VariableSizedData` (`nes-nautilus/include/Nautilus/DataTypes/VariableSizedData.hpp`) holds
  `nautilus::val<int8_t*> ptrToVarSized` (`:58`), `getSize()` (`:40`), `getContent()` (`:41`).
- `DataType::Type::VARSIZED` is the type tag gating this path (`writeVarVal`,
  `ChainedEntryMemoryProvider.cpp:166`).

### What a var-sized round-trip test would need
1. **Construct an entry with a var-sized field WITHOUT Nautilus.** Mirror `populate`, but for the var-sized
   value: call `map.allocateSpaceForVarSized(bufferManager.get(), sizeof(uint32_t)+blobLen)`, write
   `[uint32_t len][blob bytes]`, and store the returned `data()` pointer into the entry's value slot
   (matching `storeVarSized` semantics). The entry "value" is then an `int8_t*` (8 bytes), so `entrySize`
   would be `sizeof(ChainedHashMapEntry)+KEY_SIZE+sizeof(void*)`.
2. **Serializer extension under test (B-varsized work):** serialize must, for var-sized fields, follow the
   in-entry pointer, read the `[uint32_t size]` prefix, append the blob bytes to the record, and store an
   offset/index instead of the pointer. Deserialize must re-`allocateSpaceForVarSized` and patch the entry
   pointer to the new location. (Serializer currently has NO field-type awareness — it only knows a flat
   `entrySize`; B must give it the field layout / which offsets are var-sized.)
3. **Parity assertion:** extend `sortedEntries` to dereference var-sized pointers and compare the *blob
   contents*, not the raw (always-differing) pointer bytes. Existing `sortedEntries` (`:85-103`) compares
   raw payload — that will spuriously fail for pointers, so a var-sized-aware comparator is required.

Cross-references for var-sized construction patterns / existing tests:
`nes-nautilus/tests/UnitTests/VariableSizedDataTest.cpp`, `…/ChainedHashMapTest.cpp` (has var-sized cases),
`nes-nautilus/tests/UnitTests/PagedVectorTest.cpp` (var-sized in paged storage),
`nes-nautilus/src/Nautilus/Interface/HashMap/ChainedHashMap/ChainedEntryMemoryProvider.cpp` (canonical
store/load of var-sized in a ChainedHashMap entry — the spec the serializer must round-trip).

---

## 5. New files / CMake registration

Pattern is dead simple — one line per test in `nes-physical-operators/tests/CMakeLists.txt`.
- The helper `add_nes_physical_operator_test(NAME SRC…)` is defined at the TOP of that file
  (`nes-physical-operators/tests/CMakeLists.txt:13-17`): it calls `add_nes_test` (which makes the exe,
  links `nes-test-util`+`nes-logger-bindings`, and registers via `gtest_discover_tests`,
  `cmake/NebulaStreamTest.cmake:21-36`), then links `nes-data-types nes-physical-operators
  nes-memory-test-utils nes-test-util`.
- Existing registrations `:19-28` (e.g. `add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest
  SpillableTimeBasedSliceStoreTest.cpp)`).

To add new tests for Increment B, append to `nes-physical-operators/tests/CMakeLists.txt` after line 28:
```cmake
add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest SpillableTimeBasedSliceStoreConcurrencyTest.cpp)
# (varsized round-trip can EXTEND the existing SliceSerializerTest.cpp instead of a new file — cheaper,
#  and keeps the serializer test suite in one place; only add a new file if it grows too large)
```
- New test file: place under `nes-physical-operators/tests/`, namespace `NES::Testing`, fixture deriving
  `BaseUnitTest` (mirror `SpillableTimeBasedSliceStoreTest.cpp:51`) OR a plain `::testing::Test` if you
  want the lighter ConcurrentSynchronizationTest harness style. Use `BufferManager::create()` in `SetUp`.
- No ctest label edits needed — `gtest_discover_tests` auto-registers; the new exe name becomes the
  `-R` selector and the brief's `-LE 'Systest|DockerCompose'` gate auto-includes it.

---

## Recommended concurrency-validation plan + exact commands

**Strategy (honest about coverage):** TSan is supported but not runnable without a multi-hour from-scratch
tsan+rocksdb image build that doesn't exist remotely. Make in-gate concurrency validation = **stress
tests**, optionally escalate to TSan.

1. **Deterministic single-thread budget/correctness tests** (B-evict) — extend
   `SpillableTimeBasedSliceStoreTest.cpp` (already single-threaded, has the fixture & helpers).
2. **Multi-threaded stress test** (B-concurrency) — NEW
   `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp`, mirroring
   `ConcurrentSynchronizationTest.cpp`: `std::array<std::jthread,N>`, `std::latch`, atomic work claim,
   build+trigger+evict threads, deterministic post-condition (aggregates == single-thread reference) +
   invariants (no spill of a held `HashMap*`, no double-spill/unspill). High `numIterations` for heisenbugs.
   Keep per-iteration work small so it survives ctest timeouts (esp. if ever run under TSan, which doubles
   the BaseUnitTest wait window).
3. **Var-sized round-trip** (B-varsized) — extend `SliceSerializerTest.cpp` with var-sized populate +
   var-sized-aware comparator (section 4).
4. **Out-of-gate** systest query under a tiny memory budget (the brief's fallback) — separate from these
   unit gates; runs via the systest framework (`-LE` excludes it from the unit gate).
5. **TSan = OPTIONAL/escalation.** Only if a race is suspected and the user accepts the image cost; commands
   in section 1. Document in SYNTHESIS that the default gate is stress-based, NOT TSan-proven.

Fast inner loop (existing `none` image):
```bash
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" nebulastream/nes-development:local-rocksdb \
  cmake --build cmake-build-debug --target nes-physical-operators \
        SpillableTimeBasedSliceStoreTest SliceSerializerTest SpillableTimeBasedSliceStoreConcurrencyTest -j
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" nebulastream/nes-development:local-rocksdb \
  ctest --test-dir cmake-build-debug -R "Spill|SliceSerializer" --output-on-failure -j
# regression gate (805 baseline):
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" nebulastream/nes-development:local-rocksdb \
  ctest --test-dir cmake-build-debug -LE 'Systest|DockerCompose' --output-on-failure -j
```

## Build/test edits needed (for the impl phase — NOT done here, READ-ONLY)
- `nes-physical-operators/tests/CMakeLists.txt`: +1 line registering the new concurrency test
  (`add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest …)`). Var-sized: prefer
  extending `SliceSerializerTest.cpp` (no CMake edit) over a new file.
- NO sanitizer/CMake/preset infra changes required for the stress-test path — it builds in the existing
  `none` image with the existing `cmake-build-debug`.
- IF TSan is pursued: NO repo edits needed (infra already exists — `Sanitizers.cmake`, custom triplets,
  `test-thread.env`, suppressions); the cost is entirely the out-of-tree tsan dep-image build + a fresh
  `cmake-build-tsan` configure dir. There is no top-level `CMakePresets.json` to add a preset to (none
  exists; the project configures via raw `-D` flags), so a TSan "preset" is not the idiom here.
