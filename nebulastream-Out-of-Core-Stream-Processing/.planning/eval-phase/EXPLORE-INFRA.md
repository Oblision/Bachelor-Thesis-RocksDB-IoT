# EXPLORE-INFRA — NebulaStream systest + measurement infra for the OOC spill eval

Repo: `nebulastream-Out-of-Core-Stream-Processing`, branch `feat/ooc-phase1-spill-skeleton`.
Build/test ONLY in Docker image `nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug`.
All file paths below are repo-relative. Read-only investigation; this is the only file written.

Legend per item: **VERDICT = exists / partially exists / GAP (must build)**.

---

## 1. Systest framework

### 1a. ctest label, runner binary, discovery, exact commands — VERDICT: exists

- **ctest label** = `"Systest"`. Set in `nes-systests/systest/CMakeLists.txt:87,95,108,114,137,...` via
  `set_tests_properties(<name> PROPERTIES LABELS "Systest" PROCESSORS N)`. The whole `nes-systests` tree is
  wired in by `add_tests_if_enabled(systest)` (`nes-systests/CMakeLists.txt:13`).
- **Runner binary** = `systest`, built from `nes-systests/systest/src/SystestStarter.cpp`
  (`nes-systests/systest/CMakeLists.txt:56`). It links `nes-systest-lib` (the SLT engine,
  `CMakeLists.txt:42-43`) which embeds a single-node worker (`nes-single-node-worker-lib`) — i.e. systest runs
  the engine **in-process** (embedded backend), not over gRPC, unless `-r/--remote` is passed.
  Already built locally: `cmake-build-debug/nes-systests/systest/systest` (886 MB, present).
- **CLI parsing**: `SystestStarter.cpp:70-117` (argparse). Key flags:
  - `-t/--testLocation <file|dir>` — a `.test` file, a directory to discover, or `path/to/file.test:N` to run
    test query #N only (`:N`, comma list, or `start-end` range — `SystestStarter.cpp:200-232`).
  - `-g/--groups`, `-e/--exclude-groups` — subgroup selection (the in-file `# groups:` tag).
  - `-n/--numberConcurrentQueries` (default 6), `--sequential` (=`-n 1`).
  - `-b` — **benchmark mode**: forces `-n 1`, writes `BenchmarkResults.json`, skips differential/error tests
    (`SystestStarter.cpp:112-115,154-172`).
  - `--show-query-performance` — prints per-query time + throughput to console (`SystestStarter.cpp:116`).
  - `--data <dir>` — input CSV root (ExternalData testdata dir).
  - `--workingDir <dir>` — where source/result/Benchmark files land.
  - `-w/--workerConfig <yaml>`, `-q/--queryCompilerConfig <yaml>`, `--optimizer key=val`.
  - `--` (remaining) — **everything after `--` is parsed as `SingleNodeWorkerConfiguration`**
    (`SystestStarter.cpp:108-111, 443-461`), e.g. `-- --worker.query_engine.number_of_worker_threads=4`.
- **Exact commands (Docker):**

  Build the runner:
  ```bash
  docker run --workdir $(pwd) -v $(pwd):$(pwd) nebulastream/nes-development:local-rocksdb \
    cmake --build cmake-build-debug -j --target systest
  ```
  Run ONE test file (or one query in it via `:N`):
  ```bash
  docker run --workdir $(pwd) -v $(pwd):$(pwd) nebulastream/nes-development:local-rocksdb \
    cmake-build-debug/nes-systests/systest/systest \
      -t nes-systests/operator/aggregation/WindowAggregationDifferentDataTypes.test:1 \
      --workingDir cmake-build-debug/systest-run \
      -- --worker.default_query_execution.execution_mode=INTERPRETER
  ```
  Run a GROUP:
  ```bash
  ... systest -g Aggregation --exclude-groups large --workingDir cmake-build-debug/systest-run \
      -- --worker.query_engine.number_of_worker_threads=4
  ```
  Run via ctest (the CMake-registered suites, e.g. agg/join matrix):
  ```bash
  docker run --workdir $(pwd) -v $(pwd):$(pwd) nebulastream/nes-development:local-rocksdb \
    ctest --test-dir cmake-build-debug -L Systest -j
  # single registered test:
  ctest --test-dir cmake-build-debug -R systest_agg_4_sliceCache_true_compiler -V
  ```
  (README.md:84-113 documents the Docker `docker run --workdir/-v` pattern and the `systest -t ...:1 -- --worker....` form.)

### 1b. `.test` (SLT) file format — VERDICT: exists

Example: `nes-systests/operator/aggregation/WindowAggregationDifferentDataTypes.test`. Structure:
- Header comments: `# name:`, `# description:`, `# groups: [Aggregation, WindowOperators, ...]` (lines 1-3).
- `CREATE LOGICAL SOURCE name(col TYPE NOT NULL, ...);` — schema (line 6).
- `CREATE PHYSICAL SOURCE FOR name TYPE File;` then `ATTACH INLINE` + CSV rows (lines 7-16), OR
  `ATTACH FILE <path-under-testdata>` for file input (e.g. `benchmark/Nexmark.test:8` `ATTACH FILE large/nexmark/bid_653M.csv`), OR `TYPE Generator SET(...)` (no ATTACH; self-generates — see 1c).
- `CREATE SINK name(col TYPE, ...) TYPE File;` (lines 18-22). Sink file path is auto-assigned by the framework
  (`docs/development/systests.md:124`), so File/Generator sinks need no path.
- A SQL query, then a `----` separator, then the expected output rows (lines 24-32). Multiple query/`----`/expected
  blocks per file are allowed. `====` separates a **differential** pair (two queries expected to agree —
  `nes-systests/differential/AggregationEquiValence.test:24`).
- Windowed aggregation syntax: `SELECT start, end, COUNT(x) ... FROM stream WINDOW TUMBLING(ts, size 100 ms) INTO sink;`
  (`WindowAggregationDifferentDataTypes.test:25-28`). `start`/`end` are window-bound pseudo-columns.
  Sliding: `WINDOW SLIDING (ts, size 10 sec, advance by 5 sec)` (`AggregationEquiValence.test:28`).
- Parser/registration: `nes-systests/systest/src/SystestParser.cpp` (SLT parser); discovery walks the test tree from
  `TEST_DISCOVER_DIR` (`nes-systests/` parent) defined at `nes-systests/systest/CMakeLists.txt:47`.

### 1c. Supplying input data + making it large — VERDICT: exists

Three mechanisms (`docs/development/systests.md:48-79`):
1. `ATTACH INLINE` — CSV rows literally in the `.test` (small only).
2. `ATTACH FILE <path>` — CSV under `nes-systests/testdata/`. Large files are NOT in-repo: they are fetched via
   CMake **ExternalData** by hash from `bench.nebula.stream`/tubcloud, gated by `-DENABLE_LARGE_TESTS=ON`
   (`nes-systests/systest/CMakeLists.txt:22-38, 100-123`). The benchmark suite uses multi-GB CSVs
   (e.g. `benchmark/Nexmark_with_varsized.test:9` `bid_6GB.csv`, `benchmark/YahooStreamingBenchmark.test:8` `ysb_1k_data_474M.csv`).
3. **`TYPE Generator`** — self-generates data, no file, no network. **This is the recommended way to make
   state > memory deterministically.** Source: `nes-plugins/Sources/GeneratorSource/` (Generator.cpp, GeneratorFields.hpp).
   Options (`SOURCE.*`): `SEED`, `STOP_GENERATOR_WHEN_SEQUENCE_FINISHES` (`ONE`/`ALL`), `MAX_RUNTIME_MS`
   (avoid — flaky), and `GENERATOR_SCHEMA` = comma list of field generators. Field kinds
   (`GeneratorFields.hpp:34-41,160-186`): `SEQUENCE <type> <start> <end> <step>`, `NORMAL_DISTRIBUTION`, `WORDLIST`,
   `RANDOMSTR`. Example: `nes-systests/differential/AggregationEquiValence.test:6-10`.
   - **To grow keyed aggregation state:** make the GROUP-BY key a `SEQUENCE` over a wide range (many distinct keys ⇒
     many hash-map entries per window) and/or make the timestamp `SEQUENCE` span many window buckets (many concurrent
     slices). `STOP_GENERATOR_WHEN_SEQUENCE_FINISHES='ALL'` makes the source self-terminate when the longest
     sequence ends (gives a finite, repeatable run).

### 1d. Selecting the windowed-agg operator + passing spill config to a query — VERDICT: exists (config is global per run, not per-query)

- A query hits the spillable store **iff** it contains a `WINDOW TUMBLING(...)` (or SLIDING) aggregation
  AND spill is enabled in the worker config. The lowering rule
  `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp:226-249` reads
  `conf.spill.enabled` and, when true, builds a `SpillableTimeBasedSliceStore` with a RocksDB backend
  (`makeRocksDBBackend`, line 234) instead of the in-memory `DefaultTimeBasedSliceStore` (line 252).
- **Config injection path (dotted CLI key):** spill lives under the worker's *default query execution* config:
  `SingleNodeWorkerConfiguration.workerConfiguration` (`worker`, `SingleNodeWorkerConfiguration.hpp:59`) →
  `WorkerConfiguration.defaultQueryExecution` (`default_query_execution`, `WorkerConfiguration.hpp:41`) →
  `QueryExecutionConfiguration.spill` (`spill`, `QueryExecutionConfiguration.hpp:79`) → `SpillConfig` fields
  (`SpillConfig.hpp:41-54`). So the systest CLI keys (after `--`) are:
  ```
  --worker.default_query_execution.spill.enabled=true
  --worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill
  --worker.default_query_execution.spill.soft_threshold_mb=64
  --worker.default_query_execution.spill.hard_threshold_mb=96
  --worker.default_query_execution.spill.compression=lz4
  ```
  Same keys work in a YAML passed via `-w` (nesting `worker: default_query_execution: spill: enabled: true`),
  exactly as the topology YAML nests `worker: query_engine: number_of_worker_threads`
  (`nes-systests/configs/topologies/single-node.yaml`).
- **No existing `.test` or YAML enables spill** (grep for `spill` across `nes-systests/` + `configs/` = 0 hits).
  Closest existing config-injection precedent is the slice-cache toggle used throughout the systest CMake matrix:
  `--worker.default_query_execution.slice_cache.enable_slice_cache=true`
  (`nes-systests/systest/CMakeLists.txt:86,136,150`). Spill is set the same way; **it is per-run/global, not
  per-query** (you cannot enable spill for one query and not another in the same `systest` invocation).

---

## 2. Memory-limit enforcement

Two independent ceilings — the eval needs BOTH.

### 2a. Engine-internal buffer-pool budget (the "soft" ceiling NES sees) — VERDICT: exists

- Total buffer-pool memory = `number_of_buffers_in_global_buffer_manager` × buffer size.
- `number_of_buffers_in_global_buffer_manager` default **32768** (`WorkerConfiguration.hpp:46-50`).
- Buffer size = `default_query_execution.operator_buffer_size`, default **4096 B**
  (`QueryExecutionConfiguration.hpp:36,71-75`). Confirmed by the instantiation
  `BufferManager::create(workerConfiguration.defaultQueryExecution.operatorBufferSize.getValue(),
  workerConfiguration.numberOfBuffersInGlobalBufferManager.getValue())`
  (`nes-runtime/src/Runtime/NodeEngineBuilder.cpp:38-40`). (`BufferManager::DEFAULT_BUFFER_SIZE = 8*1024` at
  `nes-memory/include/Runtime/BufferManager.hpp:66` is the *library* default and is overridden by the worker's 4096.)
- **Default pool ≈ 32768 × 4096 ≈ 128 MiB.** Tune via CLI:
  `-- --worker.number_of_buffers_in_global_buffer_manager=20000` (the join/agg suites use 20000;
  the large suite uses 2_000_000 — `nes-systests/systest/CMakeLists.txt:107,136`).
- IMPORTANT for the eval: the spill store's resident-byte accounting (`residentBytesOf`/`residentBytes`,
  `SpillableTimeBasedSliceStore.hpp:141-148`) is computed from the slice hash-map page footprint, INDEPENDENT of
  the BufferManager pool. The spill thresholds (`soft/hard_threshold_mb`) are watermarks on *that* store-local byte
  count, NOT on the global buffer pool. So the buffer pool and the spill thresholds are two separate budgets.

### 2b. Real OS/Docker memory ceiling (proves no-OOM) — VERDICT: partially exists (mechanism exists in Docker, harness does NOT use it)

- The systest CMake/ctest harness does **not** set any `--memory`/cgroup limit anywhere
  (grep `--memory`/`cgroup` across repo = 0 functional hits). The CI "isolated container" story in
  `nes-systests/README.md:39-68` is forward-looking (TODO #250) and not a memory-ceiling mechanism.
- To impose a hard ceiling, add Docker flags to the existing `docker run` pattern:
  ```bash
  docker run --memory=512m --memory-swap=512m --workdir $(pwd) -v $(pwd):$(pwd) \
    nebulastream/nes-development:local-rocksdb \
    cmake-build-debug/nes-systests/systest/systest -t <test> -- <spill flags>
  ```
  `--memory-swap` equal to `--memory` disables swap so the cgroup OOM-killer fires deterministically when RSS
  exceeds the limit (this is what proves "no-OOM with spill on" vs "OOM with spill off").
- **Distinction for the eval design:** set the Docker `--memory` ceiling as the *real* limit; set the engine buffer
  pool (`number_of_buffers_in_global_buffer_manager` × `operator_buffer_size`) and the spill `soft/hard_threshold_mb`
  *below* that ceiling (e.g. ceiling 512 MB, hard 256 MB, soft 192 MB), so the spill governor engages well before
  the cgroup kills the process. RQ1/no-OOM = "spill ON survives a ceiling that spill OFF (in-memory store) gets
  OOM-killed under."

---

## 3. Observability / metrics (CRITICAL)

### 3a. Throughput — VERDICT: partially exists (whole-query tuples/sec from FILE inputs only)

- Benchmark mode (`-b`) writes `BenchmarkResults.json` to `--workingDir` with per-query
  `{query name, time(seconds), bytesPerSecond, tuplesPerSecond}` (`nes-systests/systest/src/SystestRunner.cpp:423-429`;
  output path `nes-systests/systest/src/SystestExecutor.cpp:382`). `--show-query-performance` also prints
  `in <elapsed> (<B/s> / <Tup/s>)` to console (`SystestState.cpp:415-427`, `SystestRunner.cpp:512-513`).
- **Granularity = whole query, wall-clock.** `time` = query `stop − running` from the engine's query summary
  (`SystestState.cpp:381-389`, `coalesceQueryMetrics()`), so elapsed time is correct for ANY source incl. Generator.
- **BUT `tuplesProcessed`/`bytesProcessed` are derived by counting `\n` and `file_size()` of the INPUT CSV files**
  (`SystestRunner.cpp:486-508`). **A `Generator` source has no input file ⇒ tuplesProcessed/bytesProcessed = 0/NAN ⇒
  tuplesPerSecond is NAN.** So with Generator you get reliable *elapsed time* but not auto tuples/sec.
  - Implication: for RQ2 throughput either (a) drive the spilling query from a large `ATTACH FILE` CSV so the
    auto-counter works, or (b) compute throughput yourself = (known generated tuple count) / time.
- **Existing benchmark harness:** only `scripts/benchmarking/benchmark.py` — a *conbench uploader* that reads
  `BenchmarkResults.json` (`benchmark.py:33-75,134-139`); it imposes no memory limit and adds no metrics.
  There is no separate `nes-benchmark` microbench target for the slice store. `nes-common/benchmarks` exists but is
  unrelated low-level micro-bench scaffolding.

### 3b. Latency (p95/p99 tail) — VERDICT: **GAP (must build)**

- **No percentile / histogram / quantile / tail-latency code anywhere in the repo**
  (grep across all `*.cpp`/`*.hpp` excluding build dir = 0 hits). Throughput is the only timing metric exposed.
- There IS a per-task event stream that is the natural raw source for latency: `QueryEngineStatisticListener`
  (`nes-query-engine/include/QueryEngineStatisticListener.hpp:154-161`) receives `TaskExecutionStart`/
  `TaskExecutionComplete`/`TaskEmit` events, each carrying `timestamp` (ChronoClock) + `numberOfTuples`
  (lines 30-66). The worker wires a `StatisticListener` (`nes-runtime/.../NodeEngineBuilder.cpp:43`,
  `nes-runtime/include/Listeners/StatisticListener.hpp`). However: these are **per-task**, not per-window/per-tuple;
  there is **no aggregator that turns them into p95/p99**, and the systest runner does not subscribe to them for
  metrics. Building tail latency = (i) implement a `QueryEngineStatisticListener` that timestamps task
  start→complete (or emit→sink), (ii) collect the durations, (iii) compute percentiles. This is an instrumentation
  task we must build.

### 3c. Spill/restore latency + counts (production) — VERDICT: **GAP (must build)**; only DEBUG logs + test hooks exist today

- The only spill counters are **test hooks**, not production metrics: `spillCountForTest()`,
  `createdSlicesSizeForTest()`, `setLastSeenWatermarkForTest()`, `spillColdSlicesUnderHardForTest()`
  (`SpillableTimeBasedSliceStore.hpp:150-162`). `spillCount` is an internal atomic
  (`SpillableTimeBasedSliceStore.hpp:252`) with no production accessor/emitter.
- Spill events ARE logged but only at **NES_DEBUG / NES_TRACE / NES_WARNING**, and carry **no duration and no
  structured byte counts** (`SpillableTimeBasedSliceStore.cpp` lines: 173 hard-threshold-crossed DEBUG,
  180 soft-crossed TRACE, 242/319 "Unspilling slice {} on read" DEBUG, 459 "Spilling slice {} ({} bytes resident)"
  DEBUG, 553 "Spilled cold slice {}" DEBUG, 587/607 spill-fail WARNING, 745 spiller-started DEBUG). These are
  parseable for **counts** of spill/unspill events (if you run a Debug build with `-d`/LOG_DEBUG and grep the log),
  but they are **not** wall-clock-timed (no `chrono` around the I/O) and DEBUG logs are stripped in Release/Benchmark
  build types (README.md:154-158).
- The `SpillBackend` interface (`nes-physical-operators/include/SliceStore/SpillBackend.hpp:40-65`,
  `put`/`get`/`erase`) has **no timing and no byte-count counters**. The RocksDB impl
  (`RocksDBSpillBackend.cpp`) likewise exposes none.
- There is a generic `StatisticListener` facility (3b) but the spill store does **not** emit any event into it.
- **Conclusion:** RQ3 (spill/restore latency when state > memory) requires NEW production instrumentation: add
  timers around `spill()` (put) and `unspill()` (get) + monotonic counters (spill count, unspill count, bytes
  spilled/restored), and a way to emit them — either (a) NES_INFO structured one-liners that survive Release and are
  CSV-parseable, (b) a production accessor on the store that the embedded systest backend reads at query stop, or
  (c) feed `StatisticListener`. RQ2 tail latency requires the listener-based percentile collector from 3b.

### 3d. How run output is captured — VERDICT: exists

- Per-query pass/fail + result rows: compared against the in-file expected block; mismatches dumped into named
  result dirs under `cmake-build-debug/nes-systests/systest/` (e.g. `MoreResultsThanExpected`,
  `LessResultsThanExpected`, present in the build tree).
- Sink output CSVs + per-query source/result files land in `--workingDir` (default
  `cmake-build-debug/nes-systests/working-dir`, `SystestConfiguration.hpp:59`).
- Benchmark numbers: `--workingDir/BenchmarkResults.json` (`SystestExecutor.cpp:382`).
- Console: total + per-query timing/throughput (`SystestStarter.cpp:514-529`, `SystestRunner.cpp:512`).
- Engine logs: `systest.log` at LOG_DEBUG when `-d` is passed (`SystestStarter.cpp:174-180`) — this is where the
  spill DEBUG lines (3c) appear.

---

## 4. Spill config surface for experiments

Every knob the eval can vary (CLI dotted key after `--`, or YAML under `worker:`). Defaults from `SpillConfig.hpp`
+ `WorkerConfiguration.hpp` + `QueryExecutionConfiguration.hpp` + `QueryEngineConfiguration.hpp`:

| Knob | CLI key (after `--`) | Default | Source |
|---|---|---|---|
| spill master switch | `--worker.default_query_execution.spill.enabled` | `false` | SpillConfig.hpp:41 |
| RocksDB dir | `--worker.default_query_execution.spill.rocksdb_path` | `/tmp/nes-spill` | SpillConfig.hpp:45 |
| soft watermark (MB) | `--worker.default_query_execution.spill.soft_threshold_mb` | `256` | SpillConfig.hpp:48 |
| hard watermark (MB) | `--worker.default_query_execution.spill.hard_threshold_mb` | `384` | SpillConfig.hpp:51 |
| compression | `--worker.default_query_execution.spill.compression` | `lz4` (`lz4`\|`zstd`\|`none`) | SpillConfig.hpp:54; RocksDBSpillBackend.cpp:74-88 |
| buffer pool count | `--worker.number_of_buffers_in_global_buffer_manager` | `32768` | WorkerConfiguration.hpp:46 |
| buffer size (B) | `--worker.default_query_execution.operator_buffer_size` | `4096` | QueryExecutionConfiguration.hpp:36,71 |
| worker threads | `--worker.query_engine.number_of_worker_threads` | `4` | QueryEngineConfiguration.hpp:38 |
| execution mode | `--worker.default_query_execution.execution_mode` | `COMPILER` (\|`INTERPRETER`) | QueryExecutionConfiguration.hpp:46 |
| slice cache | `--worker.default_query_execution.slice_cache.enable_slice_cache` | (per CMake) | systest CMakeLists.txt:86 |

- Lowering consumes these at `LowerToPhysicalWindowedAggregation.cpp:226-249` (note: soft/hard MB are ×1024×1024 →
  bytes at lines 243-244; `rocksdb_path` is suffixed with `/agg-<handlerId>` at line 235 so multiple agg operators
  don't collide).
- Compression options confirmed exactly `lz4|zstd|none` (anything else throws `SpillStoreFailure`,
  RocksDBSpillBackend.cpp:88).

---

## Top instrumentation gaps that block the eval (ranked)

1. **Spill/restore latency + production counters (RQ3) — GAP.** No timers around `put`/`get` in
   `SpillBackend`/`RocksDBSpillBackend`; spill count is test-only (`spillCountForTest`); events only at DEBUG with
   no duration. MUST add: chrono timers + monotonic counters (spillCount, unspillCount, bytesSpilled,
   bytesRestored, total spill/unspill ns) in `SpillableTimeBasedSliceStore`/`RocksDBSpillBackend`, plus an emit
   path that survives Release (NES_INFO CSV line at query stop, or a production accessor read by the systest
   embedded backend, or feed `StatisticListener`).
2. **Tail latency p95/p99 (RQ2) — GAP.** No percentile/histogram code anywhere. MUST build a
   `QueryEngineStatisticListener` that records per-task (or per-emit-to-sink) durations from `TaskExecutionStart`→
   `TaskExecutionComplete` timestamps and computes percentiles. Decide the latency unit (per-task vs per-window) —
   per-window needs an extra hook because the engine only exposes per-task events.
3. **Throughput with Generator inputs (RQ2) — partial.** Auto `tuplesPerSecond` only works for FILE sources
   (counts `\n`). With Generator, tuples/sec is NAN; either drive from a large `ATTACH FILE` CSV or compute
   throughput = known-generated-tuples / measured-time externally.
4. **Memory-ceiling harness — partial.** Docker `--memory`/`--memory-swap` works but is NOT wired into ctest;
   the eval must add a wrapper script that runs `systest` under `docker run --memory=<X> --memory-swap=<X>` and
   records exit status (137 = OOM-kill) to prove no-OOM.

(Items 1-2 are true production-code instrumentation that must be ADDED; 3-4 are harness/scripting.)

---

## Smallest path to a first end-to-end memory-limited spilling systest

Goal: one tumbling-window aggregation query that actually spills, running under a hard Docker memory ceiling, with
crude-but-real metrics — minimal new code.

1. **Write a spilling `.test`** (e.g. `nes-systests/operator/aggregation/SpillSmoke.test`) using a `Generator`
   source that produces enough distinct keys × window buckets to exceed the spill thresholds. Skeleton:
   ```
   # name: aggregation/SpillSmoke.test
   # groups: [Aggregation, Spill]
   CREATE LOGICAL SOURCE s(key UINT64 NOT NULL, val UINT64 NOT NULL, ts UINT64 NOT NULL);
   CREATE PHYSICAL SOURCE FOR s TYPE Generator SET(
       1 AS `SOURCE`.SEED,
       'ALL' AS `SOURCE`.STOP_GENERATOR_WHEN_SEQUENCE_FINISHES,
       'SEQUENCE UINT64 0 1000000 1, SEQUENCE UINT64 0 100 1, SEQUENCE UINT64 0 100000000 100' AS `SOURCE`.GENERATOR_SCHEMA
   );
   CREATE SINK out(s.start UINT64 NOT NULL, s.end UINT64 NOT NULL, s.key UINT64 NOT NULL, s.sumVal UINT64 NOT NULL) TYPE File;
   SELECT start, end, key, SUM(val) AS sumVal FROM s GROUP BY key WINDOW TUMBLING(ts, size 1000 ms) INTO out;
   ----
   <fill expected after a first correct in-memory run, OR run without an expected block just to observe spilling>
   ```
   (Tune the key range / ts span / window size so resident state comfortably exceeds `hard_threshold_mb`. The
   timestamp `step 100` over range 0..1e8 with `size 1000 ms` ⇒ many concurrent slices; key range 0..1e6 ⇒ large
   per-window hash maps.)

2. **Run it with spill ON under a real ceiling** (Docker memory + sub-ceiling spill thresholds + small buffer pool):
   ```bash
   docker run --memory=512m --memory-swap=512m \
     --workdir $(pwd) -v $(pwd):$(pwd) \
     nebulastream/nes-development:local-rocksdb \
     cmake-build-debug/nes-systests/systest/systest \
       -t nes-systests/operator/aggregation/SpillSmoke.test \
       --workingDir cmake-build-debug/systest-run -d \
       -- --worker.default_query_execution.execution_mode=COMPILER \
          --worker.query_engine.number_of_worker_threads=4 \
          --worker.number_of_buffers_in_global_buffer_manager=20000 \
          --worker.default_query_execution.spill.enabled=true \
          --worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill \
          --worker.default_query_execution.spill.soft_threshold_mb=64 \
          --worker.default_query_execution.spill.hard_threshold_mb=96 \
          --worker.default_query_execution.spill.compression=lz4
   ```
   Crude metrics available with ZERO new code:
   - **Did it spill?** `-d` enables LOG_DEBUG → grep `systest.log` for `Spilling slice` / `Unspilling slice` /
     `Hard threshold crossed` (counts of spill/unspill events).
   - **No-OOM proof:** process exit ≠ 137 with spill ON; rerun with `spill.enabled=false` and expect 137 (cgroup
     OOM-kill) at the same ceiling.
   - **Elapsed time:** add `--show-query-performance` (works with Generator); throughput tuples/sec will be NAN
     (Generator has no input file) — compute it as generated-tuples / time, or switch step 3.

3. **(For RQ2 throughput numbers)** replace the Generator with an `ATTACH FILE large/...csv` source (needs
   `-DENABLE_LARGE_TESTS=ON` + ExternalData fetch, or a locally generated CSV under `nes-systests/testdata/`) so
   `BenchmarkResults.json` auto-fills `tuplesPerSecond`; run with `-b` (forces `-n 1`).

4. **(Later, for real RQ2/RQ3 numbers)** add the two production instrumentation pieces from the gap list:
   spill/unspill timers+counters (gap 1) and the percentile listener (gap 2). Until then, step 2 already gives a
   spilling query running end-to-end under a memory ceiling with event-count + no-OOM evidence.
