# LEARNINGS — Phase 2 step 2c0 (handler-owned chunk registry)

## Decisions (validated)
- **Option 2 (handler-owned registry) was the right call.** Making the emit buffer trivially destructible made guardrails G1/G2/G5 (explicit `~T()` + scope guard + single-shot) unnecessary — the buffer owns no heap, so a dropped-undrained buffer cannot leak. The registry + per-chunk drain-ack + `stop()` sweep is leak-total by construction, not by argument.
- **At P=1 only the accumulator moves**; the `inputs` vector in the registry entry is intentionally empty (reserved for 2d partition reads). This kept the change byte-identical (combine/lower loops, `hashMaps[]` slice-ownership, and all emit metadata untouched) and let the refactor land as a pure regression-gated step before partitioning.

## Patterns (reusable)
- **Cross-async-gap ownership for placement-new'd emit objects:** own the heap in the operator handler (mutex-guarded registry keyed by the chunk's (sequenceNumber, chunkNumber)); the buffer carries only raw pointers + the key; the consumer (probe) releases by key on drain; `stop()` sweeps the remainder. Mirrors `EmittedHJWindowTrigger`'s trivially-destructible shape.
- **Probe→handler call in traced code:** `executionCtx.getGlobalOperatorHandler(operatorHandlerId)` returns a `nautilus::val<OperatorHandler*>`; pass it as an arg into the `nautilus::invoke` host lambda alongside the struct ref, then `dynamic_cast` to the concrete handler host-side (same idiom as `AggregationBuildPhysicalOperator::setup`).
- **`static_assert(std::is_trivially_destructible_v<T>)`** next to any struct placement-new'd into a recycle-only TupleBuffer — turns a future silent-leak regression into a build failure.
- **Designated initializers** for aggregate registry-entry structs so a field reorder can't silently swap owned members.

## Pitfalls (confirmed)
- **Recycle-only (unpooled) TupleBuffers never run placement-new'd content destructors** (`TupleBufferImpl.cpp:208` → `UnpooledChunksManager.cpp`); undrained emitted buffers are *dropped* on normal engine teardown (`QueryEngine.cpp:481-485,633-637`). A hand-cleanup of one member (`finalHashMap.reset()`) is "accidentally total" only while that member is the sole non-trivial one. Already captured in `nes_cpp_idioms`.
- **ASAN/LSAN is not a usable leak gate** against the `nebulastream/nes-development:local-rocksdb` image: its vcpkg triplet is `none` (sanitizer is baked per-triplet at image build), and upstream CI runs the address-sanitizer row with `ASAN_OPTIONS=detect_leaks=0`. Use a **counter-based** registry-balance assertion (`registered == released`, registry empty) — deterministic, runs in the normal `-LE 'Systest|DockerCompose'` gate.

## Process
- The pre-implementation lifetime *totality audit* (run before any code) caught a RED that would otherwise have been a silent OOM regression on exactly the constrained-device runs the thesis measures. Auditing the teardown paths first, then designing ownership around them, was far cheaper than debugging a leak later.
- Splitting old 2c into 2c0 (ownership, P=1, byte-identical) + 2c1 (partition emit) isolated the load-bearing safety change from the feature logic — clean regression gate, easy review.

## Result
Commits `082dfa2696` (feat) + `ff8ee88163` (review fixes). Regression 844/844 (835 + 9 new). cpp-review APPROVE-WITH-NITS, all Mediums cleared. Manual push (held for review).
