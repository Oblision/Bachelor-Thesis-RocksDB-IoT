# Phase 1 OOC Skeleton — Consolidated Plan (post live-tree verification)

Verified against HEAD cc754a94c9. Addendum mostly holds. Two evidence-based deviations from PHASE1_PLAN.md:

## Deviation 1 — SpillConfig home: `nes-query-compiler/interface/`, NOT `nes-configurations/`
Evidence: the direct precedent `SliceCacheConfiguration` lives in `nes-query-compiler/interface/SliceCacheConfiguration.hpp`,
is embedded in `QueryExecutionConfiguration` (same dir), AND is consumed by `DefaultTimeBasedSliceStore`
(nes-physical-operators) via a bare `<SliceCacheConfiguration.hpp>` include — resolved through the
`nes-query-compiler-interface` INTERFACE library (which links nes-configurations). Mirroring this exactly is the
lowest-risk, convention-correct choice and is what the addendum meant by "SpillConfig follows the same pattern."
Consequence: SpillConfig's unit test moves to `nes-query-compiler/tests/` (add_nes_compiler_test); `nes-configurations/tests/CMakeLists.txt` is NOT touched.

## Deviation 2 — Guard placement: early in `apply()`, NOT a make_unique wrap
Evidence: the construction site (`LowerToPhysicalWindowedAggregation.cpp:226`) now uses the 3-arg ctor
`DefaultTimeBasedSliceStore(size, slide, conf.sliceCacheConfiguration)` and the deduced concrete type is then used for
`->createSliceStoreRef(...)` (line 228) — a method that does NOT exist on `WindowSlicesStoreInterface`. So the plan's
"declare as unique_ptr<WindowSlicesStoreInterface>" is impossible. Instead the spill check is a fail-fast guard at the
TOP of `apply()`: `if (conf.spill.enabled.getValue()) throw NotImplemented(...)`. Semantically cleaner (refuse before
any lowering work) and robustly unit-testable. Construction site stays byte-for-byte unchanged.

## Confirmed (no drift)
- NotImplemented: ExceptionDefinitions.inc:109, code 9003. `throw NotImplemented("msg")`, via <ErrorHandling.hpp> (already included at LowerToPhysicalWindowedAggregation.cpp:56).
- SliceEnd = Timestamp (Slice.hpp:23). WorkerThreadId = NESStrongType<uint32_t,...> (Identifiers.hpp:31). No WorkerId type exists.
- WindowSlicesStoreInterface has 8 pure virtuals (Phase 4, not Phase 1).
- C++23. Header-only types under include/ need no CMake change.
- Option ctor: ScalarOption(name, defaultString, description[, validators]). getValue() returns T. Default is always a string.
- Embedding pattern: `Config member = {"yaml_key", "description"};` (BaseConfiguration 2-arg ctor).
- QueryExecutionConfiguration: insert spill member after line 76 (sliceCacheConfiguration); add &spill after &sliceCacheConfiguration at line 88; add `#include <SpillConfig.hpp>` after line 28.

## Final file map (10 files, ~300 lines)
NEW:
1. nes-query-compiler/interface/SpillConfig.hpp                      (mirror SliceCacheConfiguration)
2. nes-physical-operators/include/SliceStore/SpillBackend.hpp        (pure-virtual interface, keys SliceEnd/WorkerThreadId)
3. nes-physical-operators/include/SliceStore/NullSpillBackend.hpp    (no-op impl)
4. nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp           (defaults/CLI/YAML/embedding) [BaseUnitTest]
5. nes-physical-operators/tests/NullSpillBackendTest.cpp             (no-op + interface contract) [BaseUnitTest]
6. nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp (real apply() throw) [BaseUnitTest]
EDIT:
7. nes-query-compiler/interface/QueryExecutionConfiguration.hpp      (+include, +spill member, +&spill)
8. nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp (+early guard)
9. nes-query-compiler/tests/CMakeLists.txt                           (+2 add_nes_compiler_test)
10. nes-physical-operators/tests/CMakeLists.txt                      (+1 add_nes_physical_operator_test)

## Build/test gate (Docker)
- ninja targets: build the new test targets; their link pulls in nes-query-compiler / nes-physical-operators.
- ctest -R "SpillConfig|NullSpillBackend|SpillRouting" --output-on-failure
- regression: full unit suite stays 752/752.
