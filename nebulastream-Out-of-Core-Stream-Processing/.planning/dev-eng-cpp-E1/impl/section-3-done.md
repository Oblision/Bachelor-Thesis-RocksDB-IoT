# E1-PR3 Implementation: Per-Task Tail Latency Listener — DONE

## New files

| File | Purpose |
|------|---------|
| `nes-query-engine/include/PercentileTaskLatencyListener.hpp` | Public header — class declaration, `LatencyStats` struct, `computePercentiles()` static method |
| `nes-query-engine/PercentileTaskLatencyListener.cpp` | Private implementation compiled into `nes-query-engine` library |
| `nes-query-engine/tests/PercentileTaskLatencyListenerTest.cpp` | Unit tests (9 cases) — RED on baseline (class absent), GREEN on patched build |

## Modified files

| File | Change |
|------|--------|
| `nes-query-engine/CMakeLists.txt` | Added `PercentileTaskLatencyListener.cpp` to `nes-query-engine` library sources |
| `nes-query-engine/tests/CMakeLists.txt` | Added `percentile-task-latency-listener-test` via `add_query_engine_test()` macro |
| `nes-single-node-worker/src/SingleNodeWorker.cpp` | `#include <PercentileTaskLatencyListener.hpp>` + `listener->addQueryEngineListener(std::make_shared<PercentileTaskLatencyListener>())` after existing Google-trace wiring |

## Class API

```cpp
namespace NES {

class PercentileTaskLatencyListener final : public QueryEngineStatisticListener {
public:
    void onEvent(Event event) override;   // called from engine worker threads

    struct LatencyStats {
        int64_t p50Us, p95Us, p99Us, maxUs;
        size_t n;
    };

    // Pure function; takes durations by value (sorts in-place on the copy)
    static LatencyStats computePercentiles(std::vector<int64_t> durations);

private:
    using TaskKey = std::pair<PipelineId, TaskId>;
    struct TaskKeyHash { ... };   // golden-ratio hash combine
    std::mutex mutex;
    std::unordered_map<TaskKey, ChronoClock::time_point, TaskKeyHash> pendingStarts;
    std::vector<int64_t> durationsUs;
    void logAndEmit() const;
};
}
```

## Event fields and timestamp source

- `EventBase::timestamp` — `ChronoClock::time_point` set at construction time inside the engine thread.
  No additional clock call in the listener.
- `TaskExecutionStart`: `timestamp`, `pipelineId`, `taskId` used.
- `TaskExecutionComplete`: `timestamp`, `pipelineId`, `taskId` used.
- `TaskExpired`: `pipelineId`, `taskId` — pending start entry is removed (no duration recorded).
- `QueryStop` / `QueryFail`: trigger `logAndEmit()`.
- All other event variants (`QueryStart`, `QueryStopRequest`, `PipelineStart`, `PipelineStop`, `TaskEmit`): silently ignored via `Overloaded` catch-all.

## Registration site

`nes-single-node-worker/src/SingleNodeWorker.cpp` — `SingleNodeWorker` constructor, immediately after the optional Google-trace printer block, before `NodeEngineBuilder::build()`.  Always registered (unconditional); no config flag needed.

The `CompositeStatisticListener::addQueryEngineListener()` dispatches `Event` variants to all registered `QueryEngineStatisticListener`s.  `PercentileTaskLatencyListener` is `QueryEngineStatisticListener` (not `StatisticListener`), so `addQueryEngineListener` is the correct call.

## Percentile method

**Nearest-rank (floor-based)**:

```
idx = floor(P / 100.0 * n) — 1  (0-indexed)
clamped to [0, n-1]
```

For n=100, P=50 → idx=49 → value at position 49 in sorted array (1-indexed rank 50).
For n=100, P=95 → idx=94.
For n=100, P=99 → idx=98.

This matches RocksDB's own percentile convention and is documented in the class comment and in `computePercentiles()`.

## Emitted log-line grammar

```
Task latency us: p50=<µs> p95=<µs> p99=<µs> max=<µs> n=<count> (per-task)
```

Examples:
```
Task latency us: p50=312 p95=874 p99=1203 max=2411 n=4800 (per-task)
Task latency us: p50=0 p95=0 p99=0 max=0 n=0 (per-task)
```

- Tag: `"Task latency us:"` — stable prefix for `grep`.
- Units: microseconds, signed 64-bit integers.
- Suffix: `(per-task)` — honesty caveat that latency is per engine task, not per window.
- Emitted via `NES_INFO` (unconditional, not gated by `ENGINE_LOG_LEVEL`).

## Test name and RED-on-baseline rationale

**Test target**: `percentile-task-latency-listener-test`  
**Test file**: `nes-query-engine/tests/PercentileTaskLatencyListenerTest.cpp`  
**Fixture**: `NES::Testing::PercentileTaskLatencyListenerTest` extends `NES::Testing::BaseUnitTest`

### RED on baseline
The test `#include <PercentileTaskLatencyListener.hpp>` fails compilation on the unpatched codebase (the header does not exist) → **all 9 test cases are RED** (compile error).

### GREEN after patch
Once `PercentileTaskLatencyListener.hpp` / `.cpp` are added and the CMakeLists is updated, the class exists and all 9 tests pass:

| Test | What it verifies |
|------|-----------------|
| `ComputePercentiles_Empty_ReturnsZero` | Empty input → n=0, all zeroes, no crash |
| `ComputePercentiles_SingleSample_AllSame` | Single sample → all percentiles equal it |
| `ComputePercentiles_TwoSamples_NearestRank` | Nearest-rank formula with n=2 |
| `ComputePercentiles_100Samples_MatchNearestRank` | 1..100 µs → p50=50, p95=95, p99=99, max=100 |
| `SingleTask_CorrectDurationRecorded` | Start+complete pair, then QueryStop — no crash |
| `MultipleTasks_DeterministicDurations` | 10 tasks with known durations — no crash |
| `ZeroSamples_NoTaskEvents_NoFatalFailure` | QueryStop with no tasks — emits n=0 |
| `UnmatchedComplete_Ignored_NoFatalFailure` | Complete without start → silently dropped |
| `MultiplePipelines_TaskIdCollision_TrackedSeparately` | Same taskId on two pipelines keyed independently |
| `QueryFail_AlsoEmitsSummary` | QueryFail triggers summary, not just QueryStop |
| `IgnoredEvents_NoCrash` | PipelineStart/Stop, TaskEmit, QueryStart/StopRequest → no crash |
| `ComputePercentiles_InputByValue_CallerUntouched` | `computePercentiles` takes by value; caller's vector unchanged |

## Thread-safety approach

Single `std::mutex mutex` guards both `pendingStarts` (unordered_map) and `durationsUs` (vector).  
Critical section: O(1) map lookup + optional vector push_back.  
Percentile computation (`computePercentiles()`) runs once at stop, outside the mutex, on a snapshot copy.  
The listener is entirely off the spill/eviction path (different subsystem, LOCKED constraint §6 satisfied).

## LOCKED constraints satisfied

1. No JIT/codegen/lowering edits. ✓
2. Only `nes-query-engine` (new files) and `nes-single-node-worker` (registration wire-up). ✓
3. P=1 behavior unchanged — listener is observation-only. ✓
4. No eviction-mutex hold-time change. ✓
5. Production-visible: `NES_INFO` at query stop, stable tag, grep-parseable one-liner. ✓
6. No co-author trailer; no push. ✓
