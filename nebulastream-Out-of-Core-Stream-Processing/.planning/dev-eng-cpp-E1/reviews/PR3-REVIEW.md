# PR3 Review — PercentileTaskLatencyListener

**Reviewer:** cpp-code-reviewer (Claude Sonnet 4.6)  
**Date:** 2026-05-25  
**Verdict:** APPROVE (no CRITICAL or HIGH issues)

Files reviewed:
- `nes-query-engine/include/PercentileTaskLatencyListener.hpp` (new)
- `nes-query-engine/PercentileTaskLatencyListener.cpp` (new)
- `nes-query-engine/tests/PercentileTaskLatencyListenerTest.cpp` (new, 12 cases)
- `nes-query-engine/CMakeLists.txt` (wiring)
- `nes-query-engine/tests/CMakeLists.txt` (wiring)
- `nes-single-node-worker/src/SingleNodeWorker.cpp` (registration)

LOCKED constraints per DESIGN-BRIEF.md verified and NOT flagged.

---

## Summary

The implementation is correct, thread-safe, and meets every requirement stated in E1-PR3. No data
races, no memory unsafety, no ODR issues. The findings below are two MEDIUM items and one LOW item
that do not block approval but should be addressed before the thesis demo run.

---

## CRITICAL — None

---

## HIGH — None

---

## MEDIUM

### M1 — Multi-query accumulation: listener is not reset between queries

**File:** `nes-query-engine/include/PercentileTaskLatencyListener.hpp:96-97`  
**File:** `nes-single-node-worker/src/SingleNodeWorker.cpp` (registration site)

The single `PercentileTaskLatencyListener` instance is registered once in the
`SingleNodeWorker` constructor and shared across the entire engine lifetime. `durationsUs` and
`pendingStarts` are never cleared after `logAndEmit()` fires. If the engine runs multiple queries
sequentially (the standard systest flow does this — start query, stop, start next), durations from
earlier queries accumulate into later queries' stats and emit totals that span all past runs.

`QueryStop` / `QueryFail` call `logAndEmit()` but do not clear `durationsUs`. The next query's
`TaskExecutionComplete` events push into the same already-grown vector.

**Impact:** p50/p95/p99 values and `n` in the emitted line for query N will include all tasks from
queries 1..N-1 as well. For a single-query eval harness run this is harmless, but for any
multi-query systest the metric will be wrong.

**Fix:** at the end of `logAndEmit()` (still under the lock, or with a second lock-guard after the
snapshot copy), clear both accumulators:
```cpp
// After computing and emitting:
const std::lock_guard lock(mutex);
durationsUs.clear();
pendingStarts.clear();
```
Or alternatively reset in the `QueryStop`/`QueryFail` handler lambdas after calling `logAndEmit()`.

---

### M2 — taskIdCounter is per-engine-lifetime, not per-query: Start/Complete pairing is robust, but the comment "unique within a query" overstates the guarantee

**File:** `nes-query-engine/include/PercentileTaskLatencyListener.hpp:76` (comment)

`pool.taskIdCounter` is a single `std::atomic<size_t>` on the `ThreadPool` instance, which lives
for the engine's entire lifetime (not reset between queries). It increments monotonically across all
queries. The key used for `pendingStarts` is `(pipelineId, taskId)` where `taskId` is globally
unique across the engine lifetime. This means Start/Complete pairing is actually safer than the
comment implies — there is no cross-query `taskId` reuse within a single engine instance. The
comment in the header says "identifies an in-flight task uniquely within a query"; it should say
"within a query engine instance" to match the actual invariant.

**Impact:** documentation only, no code defect; no false matches possible in practice.

**Fix:** update the comment at `PercentileTaskLatencyListener.hpp:76`:
```cpp
/// Key: (pipelineId, taskId) — identifies an in-flight task uniquely within the engine lifetime.
/// taskId is assigned from a per-ThreadPool monotonic counter; it does not reset between queries.
```

---

## LOW

### L1 — Percentile formula discrepancy for n=2, p95/p99: `rank==0` clamp silently returns index 0 for high percentiles on very small samples

**File:** `nes-query-engine/PercentileTaskLatencyListener.cpp:109-115`

For n=2:
- P=95: `rank = floor(0.95 * 2) = 1` → `rank - 1 = 0` → `durations[0]` (the minimum). Returns the
  minimum value as p95, which the test at line 128 confirms and documents.
- P=99: same result.

This is mathematically correct for nearest-rank floor-based percentile and is explicitly called out
in the test. However, for very small sample counts the p95 and p99 will silently equal p50 (or even
the minimum). No UB, no crash — purely a statistical honesty note.

The emitted line already carries `n=<count>` so the harness can filter out low-n results. No code
change required; adding a brief warning to the class comment or a harness note would be sufficient:
"For n < ~20 the p95/p99 estimates collapse to lower-order values."

---

## Checklist answers (per review mandate)

### 1. Thread-safety (LOCKED #6)

CORRECT. A single `std::mutex mutex` (declared `mutable` so it can be acquired in the `const`
`logAndEmit()`) guards both `pendingStarts` and `durationsUs`. Every access — Start, Complete,
Expired, logAndEmit — holds `std::lock_guard lock(mutex)`. There is no TOCTOU: find+erase on
Complete is a single lock hold. The critical section is a hash-map lookup + optional `push_back`,
which is O(1) amortized. This is negligible vs task execution cost (which involves actual pipeline
processing of tuple buffers). No eviction-mutex involvement — fully off the spill path. LOCKED §6
is satisfied.

Caveat for thesis discussion: at very high task rates (tens of thousands of tasks/second across many
worker threads) the single mutex becomes a contention point. For the eval workloads described in the
proposal this is not a concern, but it is worth noting in the thesis that the observer slightly
perturbs the system under measurement. This was accepted in the DESIGN-BRIEF as "a single small
mutex" — the implementation matches that decision.

### 2. Event pairing correctness

CORRECT with one latent bug (M1 above).

- `(pipelineId, taskId)` key: `taskId` is assigned from a per-`ThreadPool` monotonic atomic counter
  (verified in `QueryEngine.cpp:461,487`). It never resets within a single engine run. Combined with
  `pipelineId`, the key is globally unique across all in-flight tasks. No Start overwrite is
  possible in practice (a given taskId is assigned to exactly one task, which then has exactly one
  Start/Complete/Expired event sequence).
- Unmatched Complete (no Start): silently skipped via the `else` branch at `.cpp:63-69`. Logged at
  DEBUG. No UB, no crash.
- Start with no Complete (leak in pendingStarts): `TaskExpired` erases the entry, preventing
  unbounded growth for expired tasks. However, if the pipeline lock expires without firing either
  `TaskExecutionComplete` or `TaskExpired` (a hypothetical engine bug), the entry leaks. This is
  not a PR3 defect — it is bounded by the number of concurrent in-flight tasks which the engine
  controls.
- taskId reuse across pipelines: `taskId` is assigned from a single counter independent of pipeline,
  so within one query the same integer will not appear on two different pipelines simultaneously.

### 3. Timestamp source

CORRECT. `EventBase::timestamp` is initialized to `ChronoClock::now()` in the `EventBase(WorkerThreadId,
QueryId)` constructor (`QueryEngineStatisticListener.hpp:34`). Both `TaskExecutionStart` and
`TaskExecutionComplete` delegate to that constructor, so the timestamp is set at the moment the
engine worker thread constructs the event — immediately before/after `pipeline->stage->execute()` in
`QueryEngine.cpp:524-526`. The listener performs no additional clock call. The duration is therefore
the time from just before `execute()` to just after it: genuine task execution time, not queue
or dispatch time. This is documented in the class comment at `.hpp:36-38`. No caveat needed beyond
what is already stated.

### 4. Percentile method

CORRECT and consistent with tests.

Formula: `idx = floor(P/100 * n) - 1` clamped to `[0, n-1]`.

Verification:
- n=100, P=50: `floor(0.50*100)=50` → `idx=49` → `durations[49]` = value 50. Test expects 50. PASS.
- n=100, P=95: `floor(0.95*100)=95` → `idx=94` → `durations[94]` = value 95. Test expects 95. PASS.
- n=100, P=99: `floor(0.99*100)=99` → `idx=98` → `durations[98]` = value 99. Test expects 99. PASS.
- n=0: early-return `LatencyStats{}` → all zero, no vector access. No UB.
- n=1: `floor(P/100*1)=0` for any P<100 → `rank==0` clamp → `idx=0` → `durations[0]`. PASS.
- `max`: always `durations[n-1]` (last element of sorted vector). Correct.

The `rank == 0 ? 0 : std::min(rank - 1, n - 1)` clamp handles the `rank=0` case without signed
underflow (correct since `rank` is `size_t`).

### 5. Registration

CORRECT for single-query eval harness use. One `PercentileTaskLatencyListener` is registered
unconditionally in the `SingleNodeWorker` constructor, before `NodeEngineBuilder::build()`. This
means the listener is live for the entire engine lifetime and receives events from all queries.

Lifetime: the listener is held via `shared_ptr` inside `CompositeStatisticListener::queryEngineListeners`.
`CompositeStatisticListener` is owned by `SingleNodeWorker` (`listener` member). The `NodeEngine`
receives a `copyPtr(listener)` — also a `shared_ptr`. The listener therefore outlives the engine.
No dangling pointer risk.

QueryFail emission: verified — `QueryFail` triggers `logAndEmit()` at `.cpp:83-86`. Partial state
(durations accumulated before the failure) is emitted. This is correct for the eval scenario.

Multi-query note: see M1 — state is not reset between queries, which matters for systest runs with
multiple sequential queries.

### 6. Emit line

CORRECT. `NES_INFO("Task latency us: p50={} p95={} p99={} max={} n={} (per-task)", ...)` at
`.cpp:148-153`. Tag `"Task latency us:"` is stable and matches the design brief's output contract.
`NES_INFO` is not gated by `ENGINE_LOG_LEVEL` (which only controls the `ENGINE_LOG_*` macros used
internally in the engine). The caveat suffix `(per-task)` is present. The line is a single
one-liner with `key=value` fields in fixed order — grep+sed parseable. Consistent with `Spill
metrics:` grammar family.

### 7. No JIT/hot-path edit; CMake/ODR

CONFIRMED. No changes to Nautilus, JIT, or codegen. No changes to spill/aggregation operators.

CMake wiring:
- `nes-query-engine/CMakeLists.txt:18`: `PercentileTaskLatencyListener.cpp` compiled into
  `nes-query-engine` lib — correct, the lib already has `PUBLIC include` so the header resolves
  for both `nes-query-engine` consumers and `nes-single-node-worker`.
- `nes-query-engine/tests/CMakeLists.txt:26`: test registered via `add_query_engine_test()` which
  links `nes-query-engine` — the header is on its include path via the `PRIVATE ..` directive.
- `nes-single-node-worker/src/SingleNodeWorker.cpp`: `#include <PercentileTaskLatencyListener.hpp>`
  resolves via `nes-query-engine`'s `PUBLIC include` directory. No ODR violation: one definition in
  `nes-query-engine` lib, one consumer linkage.
- Include guard: `#pragma once` at `.hpp:15`. Correct.

### 8. Standard C++: RAII, const, exception-safety, NES_* logging

- RAII: `std::lock_guard` used at every lock site. No manual `lock()`/`unlock()`.
- `const` correctness: `logAndEmit()` is `const`; `mutex` is `mutable` (required pattern for
  logical const + mutex). `onEvent()` is non-`const` (correct: it mutates `pendingStarts` and
  `durationsUs`). `computePercentiles()` is `static` and takes by value (immutable from caller's
  perspective — test `CallerUntouched` verifies this).
- Exception safety: `std::sort`, `std::unordered_map::insert_or_assign`, `std::vector::push_back`
  may throw `std::bad_alloc`. If they do, the `std::lock_guard` destructor releases the mutex
  correctly. `onEvent` would propagate the exception to the engine caller — this is acceptable for
  an OOM scenario.
- NES_* logging: `NES_TRACE` for per-event noise, `NES_DEBUG` for unusual paths, `NES_INFO` for
  the production-visible summary. Correct hierarchy.
- No raw `new`/`delete`, no C-style arrays, no `malloc`, no `typedef`.

---

## Test coverage

12 test cases covering: empty input, single sample, two-sample formula, 100-sample formula
verification, single task pair, 10 tasks, zero-task QueryStop, unmatched Complete, cross-pipeline
taskId collision, QueryFail, ignored events, by-value immutability. Coverage of the critical code
paths is adequate.

Gap: no test verifies that `durationsUs` is empty after `QueryStop` (because it is NOT reset — see
M1). If M1 is fixed, a test should assert the reset.

---

## Action items

| Priority | Item |
|----------|------|
| MEDIUM   | M1: Clear `durationsUs` and `pendingStarts` after `logAndEmit()` to prevent cross-query accumulation |
| LOW      | M2: Fix comment at `.hpp:76` to say "engine lifetime" not "within a query" |
| LOW      | L1: Add harness note that p95/p99 are unreliable for n < ~20 (no code change) |
