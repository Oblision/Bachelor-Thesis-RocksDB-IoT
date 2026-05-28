# BUILD.md — Explorer 2 (Build System & Config Plumbing) — Increment C `emit_lag`

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. READ-ONLY exploration.
Task: add an event-time `emit_lag` knob, plumb `config → lowering → operator handler → SpillableTimeBasedSliceStore`, default 0.

All paths are absolute under the repo root
`/Users/oblision/Desktop/Desktop/Uni_TI_BS/Bachelor Arbeit/Bachelor-Thesis/nebulastream-Out-of-Core-Stream-Processing/`
(abbreviated `<repo>/` below). Line numbers are at the pinned commit.

---

## 1. Where the spill config lives today — and where `emit_lag` goes

**File:** `<repo>/nes-query-compiler/interface/SpillConfig.hpp`

`class SpillConfig final : public BaseConfiguration` (line 34). Header-only, inherits `BaseConfiguration`,
exposes `getOptions()`. Current fields (lines 41–54):

```cpp
BoolOption   enabled         = {"enabled",          "false",        "Enable the spillable (out-of-core) window-slice store."};
StringOption rocksdbPath      = {"rocksdb_path",     "/tmp/nes-spill","Filesystem path for the RocksDB spill backend ..."};
UIntOption   softThresholdMB  = {"soft_threshold_mb","256",          "Soft memory watermark in MB; background spill above this."};
UIntOption   hardThresholdMB  = {"hard_threshold_mb","384",          "Hard memory watermark in MB; synchronous spill above this."};
StringOption compression      = {"compression",      "lz4",          "Compression for spilled slices [lz4|zstd|none]."};
```

`getOptions()` (lines 57–60) returns `{&enabled, &rocksdbPath, &softThresholdMB, &hardThresholdMB, &compression}`.

**Idiomatic type for `emit_lag`:** a `UIntOption` (= `ScalarOption<uint64_t>`, confirmed in
`<repo>/nes-configurations/include/Configurations/ScalarOption.hpp:185`). This matches `Timestamp::Underlying`
which is `uint64_t` (`<repo>/nes-common/include/Time/Timestamp.hpp:27`). The window size/slide are themselves
passed as raw `uint64_t` from `windowType->getSize().getTime()` into the store ctor (see §3), so the existing
convention for event-time durations at this seam is *raw `uint64_t` in the window's own time unit (ms)*. A
`Timestamp`/`Duration` type is NOT used in configs anywhere; `UIntOption` is correct and matches LOCKED #5.

**Exactly where to add it** — add a field next to `compression` (before the `private:` at line 56):

```cpp
/// Event-time emit lag L (same time unit as the window definition / watermark, e.g. ms). Results emit on a
/// watermark of globalWatermark - emit_lag, so completed windows in [globalWatermark - L, globalWatermark)
/// are RETAINED and become spillable. Default 0 ⇒ today's exact behavior (emit wm == global wm).
UIntOption emitLag = {"emit_lag", "0", "Event-time emit/retention lag L (ms); retain+spill completed windows behind the watermark. 0 = no lag."};
```

and extend `getOptions()` (line 59):

```cpp
return {&enabled, &rocksdbPath, &softThresholdMB, &hardThresholdMB, &compression, &emitLag};
```

No validator is strictly required (matches `compression`/thresholds, which use none here), but a
`std::make_shared<NumberValidation>()` could be added mirroring `QueryExecutionConfiguration`'s `UIntOption`s
(see §2). Default `"0"` satisfies LOCKED #6 (regression safety net).

---

## 2. The config chain (CLI key registration)

**File:** `<repo>/nes-query-compiler/interface/QueryExecutionConfiguration.hpp`

`SpillConfig` is embedded as a nested sub-config:

```cpp
SpillConfig spill = {"spill", "Configuration for the out-of-core (spillable) window-slice store"};   // line 79
```

and registered in `getOptions()` at line 92 (`&spill` last in the list).

**CLI key derivation — there is NO separate per-key parser/registry.** Keys are derived structurally from the
nested option declarations by `BaseConfiguration`'s `overwriteConfigWithCommandLineInput` /
`overwriteConfigWithYAMLFileInput`. The full dotted path is the concatenation of the nesting yaml-keys:

```
WorkerConfiguration.defaultQueryExecution  ("default_query_execution")
  → QueryExecutionConfiguration.spill      ("spill")
    → SpillConfig.<field>                   ("enabled" / "soft_threshold_mb" / "emit_lag" / ...)
```

Embedding chain confirmed:
- `<repo>/nes-runtime/interface/Configuration/WorkerConfiguration.hpp:41`
  `QueryExecutionConfiguration defaultQueryExecution = {"default_query_execution", ...};`
- existing keys today: `--worker.default_query_execution.spill.enabled`,
  `...spill.soft_threshold_mb`, `...spill.compression`, etc. (the same prefix the EVAL harness already uses).

**What to add for the CLI key:** *nothing beyond the `SpillConfig.hpp` field in §1.* Once `emitLag` (yaml-key
`emit_lag`) is in `SpillConfig::getOptions()`, the key
`--worker.default_query_execution.spill.emit_lag=<ms>` is parsed automatically, and the nested YAML form

```yaml
spill:
  emit_lag: "5000"
```

works automatically. This is exactly how `SpillConfigTests.cpp` Tests E/F already prove `spill.enabled` /
`spill.soft_threshold_mb` flow through (lines 132–154). No code in `QueryExecutionConfiguration.hpp` changes
unless you choose to also surface it on `QueryExecutionConfiguration` directly (NOT needed — the brief mirrors
`compression`/thresholds, which live ONLY on `SpillConfig`).

---

## 3. The lowering — read `emit_lag`, thread it into the store

**File:** `<repo>/nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`

The spill-enabled factory branch from Increment A is at lines 226–249. It reads `conf.spill.*` (the member is
`conf`, type `QueryExecutionConfiguration`, declared in
`<repo>/nes-query-compiler/private/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.hpp:32`):

```cpp
const bool spillEnabled = conf.spill.enabled.getValue();                                  // line 226
std::unique_ptr<DefaultTimeBasedSliceStore> sliceAndWindowStore;
if (spillEnabled)
{
    auto backend = SpillableTimeBasedSliceStore::makeRocksDBBackend(
        conf.spill.rocksdbPath.getValue() + "/agg-" + std::to_string(handlerId.getRawValue()),
        conf.spill.compression.getValue());                                               // lines 234-235
    sliceAndWindowStore = std::make_unique<SpillableTimeBasedSliceStore>(
        windowType->getSize().getTime(),         // windowSize  (raw uint64 ms)           // line 237
        windowType->getSlide().getTime(),        // windowSlide (raw uint64 ms)           // line 238
        conf.sliceCacheConfiguration,                                                     // line 239
        std::move(backend),                                                               // line 240
        entriesPerPage,                                                                   // line 241
        pageSize,                                                                         // line 242
        conf.spill.softThresholdMB.getValue() * 1024ULL * 1024ULL,                        // line 243
        conf.spill.hardThresholdMB.getValue()  * 1024ULL * 1024ULL);                      // line 244
    NES_INFO("Lowering windowed aggregation handlerId={} with out-of-core spill enabled ...", ...);
}
else { /* DefaultTimeBasedSliceStore, lines 252-253 */ }
```

**Where `emit_lag` threads through:** read `conf.spill.emitLag.getValue()` and pass it as a new ctor argument
to `SpillableTimeBasedSliceStore` (see §4 for the recommended ctor change). Concretely add a final arg to the
`std::make_unique<SpillableTimeBasedSliceStore>(...)` call at lines 236–244:

```cpp
        conf.spill.hardThresholdMB.getValue() * 1024ULL * 1024ULL,
        conf.spill.emitLag.getValue());          // NEW: event-time L in ms (default 0)
```

Because `windowType->getSize().getTime()` and `getSlide().getTime()` are passed here as raw `uint64_t` ms, the
`emit_lag` value (also raw ms) is unit-consistent at this seam — no conversion needed. The `else` (in-memory)
branch is UNCHANGED, preserving non-spill behavior. The `AggregationOperatorHandler` ctor at lines 275–276
takes `spillEnabled` but is NOT on the lag path (the lag lives in the store per OPEN #1 leading candidate); if
the architects instead pick the handler seam (OPEN #1 alt), `emit_lag` would also need to reach
`AggregationOperatorHandler` here — see §4 note.

---

## 4. Store construction — getting `emit_lag` into `SpillableTimeBasedSliceStore`

**Header:** `<repo>/nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
**Impl:**   `<repo>/nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`

Current ctor signature (hpp lines 95–103):

```cpp
SpillableTimeBasedSliceStore(
    uint64_t windowSize,
    uint64_t windowSlide,
    SliceCacheConfiguration sliceCacheConfiguration,
    std::unique_ptr<SpillBackend> backend,
    uint64_t entriesPerPage,
    uint64_t pageSize,
    uint64_t softThresholdBytes,
    uint64_t hardThresholdBytes);
```

Current ctor definition (cpp lines 96–121): member-init list initializes
`backend, entriesPerPage, pageSize, softThresholdBytes, hardThresholdBytes`; body has PRECONDITIONs +
`NES_DEBUG`. The only caller is the lowering (§3, line 236). The store is the leading seam for applying L
(OPEN #1): it already overrides `getTriggerableWindowSlices(Timestamp globalWatermark)` (hpp:119, cpp:264–280)
and already learns `lastSeenWatermark` there.

**Recommended ctor change** — add a trailing `uint64_t emitLag = 0` parameter and a
`const uint64_t emitLag;` member (default keeps every existing test/caller compiling and L=0-identical per
LOCKED #6). Suggested member placement: alongside the threshold members at hpp lines 209–214:

```cpp
const uint64_t entriesPerPage;
const uint64_t pageSize;
const uint64_t softThresholdBytes;
const uint64_t hardThresholdBytes;
const uint64_t emitLag;          // NEW: event-time emit/retention lag L (ms); 0 = no lag
```

The actual *mechanism* (drive emit with `globalWatermark - emitLag`, drive quiescence/cold-tracking with the
true `globalWatermark`) is implemented in `getTriggerableWindowSlices` (cpp:264–280) — Explorer-1/CORE territory,
but the *plumbing* deliverable is: ctor param + member, read in that override. The base store call at cpp:267
(`DefaultTimeBasedSliceStore::getTriggerableWindowSlices(globalWatermark)`) is the seam where a lagged
`globalWatermark - emitLag` would be substituted for emit while `lastSeenWatermark` keeps the true value
(cpp:271–275). NOTE: `Timestamp::operator-(Underlying)` exists (Timestamp.hpp:37) so `globalWatermark - emitLag`
compiles directly; guard underflow when `globalWatermark.getRawValue() < emitLag` (early-stream) — clamp to 0.

**Handler seam alternative (OPEN #1 alt, higher blast radius):** if instead the lag is computed in
`WindowBasedOperatorHandler::checkAndTriggerWindows`
(`<repo>/nes-physical-operators/src/WindowBasedOperatorHandler.cpp:73–89`, the lagged wm would be applied at
line 87 `getTriggerableWindowSlices(newGlobalWatermark)`), then `emit_lag` must be plumbed into the *handler*
ctor instead/also: `AggregationOperatorHandler` ctor
(`<repo>/nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:44–56`, hpp ctor ends at
`include/Aggregation/AggregationOperatorHandler.hpp:65`, with `bool spillEnabled;` member at hpp:82) — the
lowering already passes `spillEnabled` there at LowerToPhysical...cpp:276, so an `emitLag` arg would slot in
the same way. The brief's leading candidate (store override) avoids touching the join-shared handler, so the
**store ctor** is the recommended single plumbing target.

---

## 5. CMake & targets

### Ownership (which CMakeLists owns what)
| Artifact | Source file | Owning CMakeLists | Library target |
|---|---|---|---|
| `SpillConfig.hpp` (+ `QueryExecutionConfiguration.hpp`) | `nes-query-compiler/interface/` | `<repo>/nes-query-compiler/CMakeLists.txt:18-20` | `nes-query-compiler-interface` (INTERFACE lib; `target_include_directories(... INTERFACE interface)`) |
| Lowering rule | `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` | `<repo>/nes-query-compiler/CMakeLists.txt:23` (`add_library(nes-query-compiler ...)`, sources via `get_source`) | `nes-query-compiler` |
| Spillable store | `nes-physical-operators/{include,src}/SliceStore/SpillableTimeBasedSliceStore.{hpp,cpp}` | `<repo>/nes-physical-operators/CMakeLists.txt:21` (`add_library(nes-physical-operators ...)`) | `nes-physical-operators` (links `RocksDB::rocksdb` PRIVATE, line 26) |
| Handler | `nes-physical-operators/src/{WindowBasedOperatorHandler,Aggregation/AggregationOperatorHandler}.cpp` | same as above | `nes-physical-operators` |

`SpillConfig.hpp` / `QueryExecutionConfiguration.hpp` are reached by lower modules via the
`nes-query-compiler-interface` INTERFACE library (idiom confirmed). `nes-query-compiler` links
`nes-physical-operators` PUBLIC (`nes-query-compiler/CMakeLists.txt:24`), so the lowering sees the store header.

### Rebuild fan-out (what recompiles when each changes)
- **`SpillConfig.hpp`** (header in `interface/`): rebuilds *every* TU that includes it transitively via
  `nes-query-compiler-interface` — at minimum the lowering, `QueryExecutionConfiguration` consumers, and the
  two query-compiler test binaries (`SpillConfigTests`, `LowerToPhysicalWindowedAggregationSpillRoutingTest`).
  Wide blast radius (it is pulled in through `QueryExecutionConfiguration.hpp` → `WorkerConfiguration.hpp`).
  Per nes_cpp_idioms: always run the FULL `ninja`, never `ninja | tail` without `set -o pipefail`.
- **`LowerToPhysicalWindowedAggregation.cpp`**: recompiles only that one TU → relinks `nes-query-compiler` and
  anything linking it (incl. its tests).
- **`SpillableTimeBasedSliceStore.hpp`**: recompiles every TU in `nes-physical-operators` that includes it
  (the store cpp, the lowering, `SpillableTimeBasedSliceStoreTest`, `SpillableTimeBasedSliceStoreConcurrencyTest`)
  → relinks `nes-physical-operators` + `nes-query-compiler` + their tests.
- **`SpillableTimeBasedSliceStore.cpp`**: one TU → relink `nes-physical-operators` + dependents.

### Test-registration macros (quote for the architects)

`add_nes_compiler_test` — `<repo>/nes-query-compiler/tests/CMakeLists.txt:13-18`:
```cmake
function(add_nes_compiler_test)
    add_nes_unit_test(${ARGN})
    set(TARGET_NAME ${ARGV0})
    target_link_libraries(${TARGET_NAME} nes-query-compiler nes-logical-operators nes-test-util)
    target_include_directories(${TARGET_NAME} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../private)
endfunction()
```
Example registration (line 21): `add_nes_compiler_test(SpillConfigTests UnitTests/SpillConfigTests.cpp)`.
To add a config-plumbing test: add one line, e.g.
`add_nes_compiler_test(SpillConfigEmitLagTests UnitTests/SpillConfigEmitLagTests.cpp)` (or extend the existing
`SpillConfigTests.cpp` — preferred, the file already covers defaults/CLI/YAML at the same nesting).

`add_nes_physical_operator_test` — `<repo>/nes-physical-operators/tests/CMakeLists.txt:13-17`:
```cmake
function(add_nes_physical_operator_test)
    add_nes_test(${ARGN})
    set(TARGET_NAME ${ARGV0})
    target_link_libraries(${TARGET_NAME} nes-data-types nes-physical-operators nes-memory-test-utils nes-test-util)
endfunction()
```
Example registration (line 28):
`add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)`.

(Both ultimately wrap `add_nes_unit_test`/`add_nes_test`; fixture is `NES::Testing::BaseUnitTest` via
`#include <BaseUnitTest.hpp>` from `nes-test-util`.)

---

## 6. Existing spill tests (file + CMake line)

| Test target | Source file (under repo) | CMake registration |
|---|---|---|
| `SpillConfigTests` | `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp` | `nes-query-compiler/tests/CMakeLists.txt:21` |
| `LowerToPhysicalWindowedAggregationSpillRoutingTest` | `nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp` | `nes-query-compiler/tests/CMakeLists.txt:22` |
| `SpillableTimeBasedSliceStoreTest` | `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` | `nes-physical-operators/tests/CMakeLists.txt:28` |
| `SpillableTimeBasedSliceStoreConcurrencyTest` (the concurrency/stress test) | `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` | `nes-physical-operators/tests/CMakeLists.txt:29` |

Supporting spill-stack tests (same `add_nes_physical_operator_test` macro, same CMakeLists):
`NullSpillBackendTest` (line 23), `InMemorySpillBackendTest` (24), `SliceSerializerTest` (25),
`RocksDBSpillBackendTest` (26), `SpillableAggregationSliceTest` (27).

**Where the new `emit_lag` tests land:**
- Config plumbing (CLI/YAML key, default 0): extend `SpillConfigTests.cpp` (add `emit_lag` assertions to the
  `defaults`, `commandLineOverride`, `yamlRoundTrip`, and `embedded*` cases — minimal new code, no new target).
- Mechanism + differential (L=0 vs L>0; OPEN #5 headline gate): add to
  `SpillableTimeBasedSliceStoreTest.cpp` (it already constructs the store directly and uses the
  `setLastSeenWatermarkForTest` / `setBufferProvider` / `forceSpill` hooks — hpp:150–162). The new ctor `emitLag`
  arg defaults to 0 so existing cases compile unchanged.

`SpillConfigTests.cpp` test pattern reference: defaults at lines 77–85, CLI override 88–100, YAML round-trip
103–120, nested-in-QEC defaults 124–129, nested CLI override 132–139, nested YAML override 142–154.

---

## 7. Build / test commands for the affected targets (fast RED→fix loops)

Build/test ONLY in Docker image `nebulastream/nes-development:local-rocksdb` (base `:local` lacks RocksDB,
fails at `find_package(RocksDB)`), build dir `cmake-build-debug`, repo root mounted at `/workspace`. NEVER pipe
`ninja` without `set -o pipefail`.

**Configure already exists** (`cmake-build-debug/build.ninja` present); all four test targets are valid ninja
targets (`SpillConfigTests`, `LowerToPhysicalWindowedAggregationSpillRoutingTest`,
`SpillableTimeBasedSliceStoreTest`, `SpillableTimeBasedSliceStoreConcurrencyTest`).

Single-target builds (fast inner loop) — run from repo root:
```bash
docker run --rm --workdir /workspace -v "$(pwd):/workspace" nebulastream/nes-development:local-rocksdb \
  bash -c 'set -o pipefail; cd cmake-build-debug && ninja SpillConfigTests SpillableTimeBasedSliceStoreTest'
```
(Add `LowerToPhysicalWindowedAggregationSpillRoutingTest SpillableTimeBasedSliceStoreConcurrencyTest` when those
change.)

Targeted ctest (regex filter, fast RED loop):
```bash
docker run --rm --workdir /workspace -v "$(pwd):/workspace" nebulastream/nes-development:local-rocksdb \
  bash -c 'cd cmake-build-debug && ctest -j8 --output-on-failure --timeout 120 -R "Spill|EmitLag|LowerToPhysicalWindowedAggregationSpillRouting"'
```

Full regression gate (LOCKED #9, must stay ≥ 820, exclude heavy labels):
```bash
docker run --rm --workdir /workspace -v "$(pwd):/workspace" nebulastream/nes-development:local-rocksdb \
  bash -c 'set -o pipefail; cd cmake-build-debug && ninja'
docker run --rm --workdir /workspace -v "$(pwd):/workspace" nebulastream/nes-development:local-rocksdb \
  bash -c 'cd cmake-build-debug && ctest -j8 --output-on-failure -LE "Systest|DockerCompose" --timeout 120'
```

Concurrency test must be validated UNDER LOAD (nes_cpp_idioms): the full `-j8` suite, or 8 background copies ×
`--gtest_repeat`; never hard-assert background-thread liveness.

---

## Exact wiring checklist for `emit_lag` (ordered, every file + location to touch)

1. **`<repo>/nes-query-compiler/interface/SpillConfig.hpp`** — add field after line 54
   (`StringOption compression ...`):
   `UIntOption emitLag = {"emit_lag", "0", "<desc>"};` and append `&emitLag` to `getOptions()` (line 59).
   *(This alone wires the CLI key `--worker.default_query_execution.spill.emit_lag` and the nested YAML
   `spill.emit_lag` — no other config file changes; verified by the auto-derivation in §2.)*

2. **`<repo>/nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`** —
   (a) add trailing ctor param `uint64_t emitLag = 0` to the ctor declaration (lines 95–103);
   (b) add member `const uint64_t emitLag;` after `hardThresholdBytes` (line 214).

3. **`<repo>/nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`** —
   (a) add `const uint64_t emitLag` to the ctor definition (lines 96–104);
   (b) initialize `, emitLag(emitLag)` in the member-init list (after line 110);
   (c) apply L in `getTriggerableWindowSlices` (lines 264–280): call the base with the lagged watermark
       `globalWatermark - emitLag` (clamp underflow to `Timestamp(0)`) at line 267, while continuing to learn the
       TRUE `globalWatermark` into `lastSeenWatermark` at lines 271–275. *(Mechanism = CORE/Explorer-1; the
       plumbing requirement is that `emitLag` is a readable member here.)*
   (d) terminate path `getAllNonTriggeredSlices` (lines 282–290) must flush the WHOLE retained backlog
       regardless of `emitLag` (LOCKED #8) — verify it does NOT subtract L.

4. **`<repo>/nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`** —
   in the `spillEnabled` branch (lines 236–244), pass `conf.spill.emitLag.getValue()` as the new trailing arg to
   `std::make_unique<SpillableTimeBasedSliceStore>(...)` (after line 244). The `else` in-memory branch
   (lines 252–253) is UNCHANGED.

   *(OPEN #1 alt only — if architects pick the handler seam instead of the store override: also add an `emitLag`
   param to `AggregationOperatorHandler` ctor at
   `<repo>/nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp:65` +
   `src/Aggregation/AggregationOperatorHandler.cpp:44-56`, pass it from LowerToPhysical...cpp:275-276, and apply
   the lag in `WindowBasedOperatorHandler::checkAndTriggerWindows`,
   `src/WindowBasedOperatorHandler.cpp:87`. The store-override route in steps 2-4 is the recommended single
   plumbing target — smaller blast radius, does not touch the join-shared handler.)*

5. **Tests — config plumbing:** `<repo>/nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp` — add
   `emit_lag` assertions to `defaults` (line 77, expect `0`), `commandLineOverride` (88), `yamlRoundTrip` (103),
   and `embedded*` (124/132/142). No new CMake line needed (extends existing `SpillConfigTests` target,
   registered at `nes-query-compiler/tests/CMakeLists.txt:21`).

6. **Tests — mechanism + differential (OPEN #5):**
   `<repo>/nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` — add cases proving L=0 ≡ baseline
   and L>0 retains/spills (cold/spillable set non-empty), using the existing test hooks
   (`setLastSeenWatermarkForTest`, `setBufferProvider`, `forceSpill`, `spillCountForTest`). No new CMake line
   (extends `SpillableTimeBasedSliceStoreTest`, registered at
   `nes-physical-operators/tests/CMakeLists.txt:28`). If a brand-new test file is preferred, register it with
   `add_nes_physical_operator_test(<Name> <Name>.cpp)` (macro at lines 13–17) /
   `add_nes_compiler_test(<Name> UnitTests/<Name>.cpp)` (macro at lines 13–18).

7. **Build & gate (Docker, `:local-rocksdb`):** single-target `ninja SpillConfigTests
   SpillableTimeBasedSliceStoreTest` (+ routing/concurrency when touched) → targeted
   `ctest -R "Spill|EmitLag|LowerToPhysicalWindowedAggregationSpillRouting"` → full `ninja` + full
   `ctest -LE "Systest|DockerCompose"` (must stay ≥ 820). Always `set -o pipefail` before `ninja`.

### Summary: minimal config-plumbing surface
Only **2 production files** carry the config plumbing end-to-end with default-0 safety:
`SpillConfig.hpp` (the knob + auto CLI/YAML key) and `SpillableTimeBasedSliceStore.{hpp,cpp}` (ctor param +
member), wired by **1 line** in `LowerToPhysicalWindowedAggregation.cpp`. The default `"0"` / `= 0` everywhere
guarantees LOCKED #6 (every existing query and test behaves identically). The lag *mechanism* itself
(getTriggerableWindowSlices substitution + terminate-flush) is CORE/Explorer-1 work that consumes the plumbed
member.
