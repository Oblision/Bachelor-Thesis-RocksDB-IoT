# Writing Points — full argument sequence for the thesis draft

Companion to `OUTLINE.md`. The outline says what each chapter contains; THIS document is the ordered
list of claims the draft will make, one point per line. Drafting rule: each point becomes one
paragraph (or merges with its neighbor); each sentence in the draft carries exactly one point; all
sentences are simple and declarative.

**Construction rules (binding for the draft):**
1. One point per sentence. No sentence does two jobs.
2. Declarative statements. No rhetorical questions, no hedging stacks, no filler transitions.
3. **No concept is used before it is introduced.** Chapter 2 (Background) and Chapter 3 (Related
   Work) introduce every concept; the concept ledger below tracks this. A reader who knows only
   Chapters 2–3 can follow everything after them.
4. Every empirical claim carries its evidence tag [E:...] in this file; in the draft it becomes a
   table/figure reference.
5. Negative results are stated as plainly as positive ones.

**Concept ledger (term → introduced at point):** pipeline→2.1, push-based→2.2, worker thread/task→2.3,
tuple buffer→2.4, global buffer pool→2.5, allocation timeout/abort→2.6, tumbling window→2.7,
slice→2.8, ChainedHashMap/page/bucket directory→2.9, pooled vs unpooled page→2.10, watermark/
lateness→2.11, emit lag/retained windows→2.12, RocksDB/LSM/memtable/SST/compaction/write
amplification→2.13–2.15, hot-cold principle→2.16, cgroup/OOM kill→2.17, ρ (state-to-cap ratio)→2.18,
outcome classes→2.19, Fisher/Welch→2.20, soft/hard watermark→5.4–5.5, spiller→5.4, reserve/levers→5.9,
backpressure/gridlock guard→5.10, feasibility corridor→7.6a, three regimes→7.6i.

---

## Ch. 1 — Introduction

- 1.1 Stream processing systems compute continuously over unbounded data streams.
- 1.2 Stateful operators, such as windowed aggregations, store intermediate state between events.
- 1.3 NebulaStream is a stream processing system for the Internet of Things, and it keeps all
  operator state in main memory.
- 1.4 Edge and IoT devices have small main memories.
- 1.5 When operator state grows beyond the available memory, the operating system terminates the
  process and processing halts.
- 1.6 Provisioning more memory or moving computation to the cloud defeats the purpose of edge
  processing, because data movement dominates its cost [13,1].
- 1.7 Cloud stream processing systems solve this with an out-of-core state backend: state spills to
  local disk when memory runs short, with RocksDB as the industry standard [9].
- 1.8 NebulaStream uses a push-based execution model, and the combination of this model with an
  out-of-core backend has not been evaluated systematically [proposal §1.3].
- 1.9 This thesis builds an out-of-core backend for tumbling-window state in NebulaStream and
  evaluates it on edge-class memory budgets.
- 1.10 Result preview, stated in one paragraph: the backend processes workloads whose state exceeds
  memory without crashes inside a characterizable operating window; the cost is 3–22% throughput;
  during the evaluation we found a single engine configuration default that silently disabled the
  intended memory architecture, and correcting it (one flag) together with new pool-pressure
  mechanisms raised stability to in-memory parity at 25% less resident memory; the remaining failure
  mode is diagnosed precisely.
- 1.11 Contributions, numbered C1–C6 (backend, instrumented harness, operating windows, the pooling
  discovery + unlock, the failure taxonomy, open-source branch).
- 1.12 Chapter guide, one sentence per chapter.

## Ch. 2 — Background
*(Every point introduces exactly one concept the rest of the thesis uses.)*

- 2.1 NebulaStream compiles queries into pipelines of operators [1,2].
- 2.2 Execution is push-based: sources push tuples through pipelines as data arrives; no consumer
  polls [14].
- 2.3 A fixed set of worker threads executes pipeline work as tasks from a queue.
- 2.4 Tuples travel and are processed in fixed-size buffers.
- 2.5 All buffers come from one global buffer pool, which has a fixed number of buffers and is
  allocated once at engine start. *(This pool is the central shared resource of the thesis.)*
- 2.6 When a thread requests a buffer from an empty pool, it waits up to one second; if no buffer
  arrives, the engine aborts the query.
- 2.7 A tumbling window partitions the stream into fixed-length, non-overlapping time intervals;
  the operator computes one aggregate per key per window.
- 2.8 NebulaStream stores window state in slices; a slice holds the aggregation state of one window
  interval.
- 2.9 Each slice's state is a hash map (ChainedHashMap): entries live on fixed-size pages; a bucket
  directory indexes them; each entry has a 24-byte header and the aggregate payload.
- 2.10 Map pages come from one of two allocation types: **bounded allocation** (pages taken from
  the fixed global buffer pool and returned on release — the standard discipline of database buffer
  managers) or **unbounded allocation** (pages from the general-purpose process heap, limited only
  by the external memory cap); which one happens depends on the configured page size relative to
  the engine's buffer size. These two terms are used consistently for the rest of the thesis.
  *(Stated here as an engine property; its consequences appear in Chapter 7.)*
- 2.11 Streams arrive out of order; a lateness bound defines how late a tuple may be; the watermark
  is the time up to which results may be finalized.
- 2.12 Completed windows are retained for a configured emit lag before their results are emitted;
  with window length W and emit lag L, the store retains N_w = L/W completed windows.
- 2.13 RocksDB is a persistent key-value store with a log-structured merge design [4].
- 2.14 Writes go to an in-memory memtable and are flushed to sorted files on disk; background
  compaction merges files.
- 2.15 Compaction rewrites data; the ratio of bytes written to disk over bytes written by the
  application is the write amplification [6,8].
- 2.16 The external-memory principle: keep frequently used (hot) data in memory and move infrequently
  used (cold) data to disk [3].
- 2.17 Linux cgroups enforce hard memory limits on a process group; exceeding the limit triggers the
  kernel out-of-memory killer, which terminates the process.
- 2.18 We measure state pressure as ρ: the steady-state memory footprint of the workload divided by
  the memory cap; ρ > 1 means the state does not fit.
- 2.19 A run therefore ends in one of four outcomes: completion, out-of-memory kill (cap exceeded),
  allocation abort (pool exhausted, point 2.6), or timeout.
- 2.20 We compare completion proportions with Fisher's exact test and means with Welch's t-test.

## Ch. 3 — Related Work

- 3.1 Apache Flink ships RocksDB as its production out-of-core state backend and is the reference
  integration [9].
- 3.2 Flink's runtime differs from NebulaStream's: its state access is checkpoint-oriented and its
  data exchange is pull-based at the boundaries Marcu and Bouvry analyze [14]; results from Flink do
  not transfer directly.
- 3.3 WindFlow demonstrates larger-than-memory windowed processing in a single-node engine and shows
  the problem is open beyond Flink [11].
- 3.4 AION manages window state across memory and persistent storage and separates hot from cold
  windows, which matches our slice granularity choice [12].
- 3.5 RocksDB tuning studies show parameters mitigate but do not remove memory and I/O sensitivity
  [5,6,7,8]; we therefore fix one research-informed configuration instead of searching configurations.
- 3.6 Fragkoulis et al. survey stream processing evaluation practice and motivate our single-node
  methodology [10].
- 3.7 Gap statement: no published work evaluates an out-of-core window state backend inside a
  push-based engine with one shared global buffer pool under hard memory caps; this thesis does.

## Ch. 4 — Goal and Research Questions
*(Short chapter; mirrors proposal §2.)*

- 4.1 Goal: store tumbling-window operator state larger than memory in NebulaStream, using a
  research-informed RocksDB configuration as the state backend.
- 4.2 RQ0: how can an out-of-core backend integrate into the push-based execution model? (answered
  by design + implementation).
- 4.3 RQ1: does the backend prevent out-of-memory failures beyond the memory budget, and where?
- 4.4 RQ2: at what cost in throughput and tail latency, including spill and restore cost?
- 4.5 RQ3: how does memory behave as state grows past the cap, and why do residual failures happen?
- 4.6 RQ4 (additional): how do CPU utilization and disk I/O evolve?
- 4.7 RQ5 (additional): how efficiently is storage used (write amplification, footprint composition)?
- 4.8 Scope: tumbling windows only, single node, no JIT or code-generation changes, no fault
  tolerance; success criterion: complete workloads whose state exceeds memory, verified against a
  sufficient-memory baseline.

## Ch. 5 — Design

- 5.1 Four requirements follow from the goal: spill before memory pressure becomes fatal; force
  spilling under acute pressure; never change query results; leave the JIT pipeline untouched.
- 5.2 The design replaces the in-memory slice store with a spillable slice store behind the same
  interface; operators above it are unchanged.
- 5.3 A slice moves through five states: hot (current windows), cold (no longer written), spilled
  (in RocksDB), restored (read back for emission), emitted.
- 5.4 A soft watermark triggers preemptive spilling: a background spiller thread moves cold slices
  to RocksDB whenever resident slice bytes exceed the soft threshold.
- 5.5 A hard watermark forces synchronous spilling when the soft mechanism falls behind.
- 5.6 Slices serialize page-by-page into RocksDB key-value pairs; the serializer preserves the exact
  map layout so restore is a byte copy.
- 5.7 Before a window's results are emitted, its spilled slices are restored.
- 5.8 The controller's control signal is the resident byte count of slice data pages; other pool
  consumers (input, queues, emission) are invisible to it. *(Stated as a design property; Chapter 7
  measures its consequences.)*
- 5.9 Design iteration two adds four pool-pressure levers, each configuration-gated: (1a) the spiller
  also drains when free pool buffers fall below a reserve; (1b) an allocation that finds the pool
  empty triggers an immediate spill of one cold slice instead of waiting passively; (1c) restores
  before emission proceed only when the pool has headroom; (1d) bucket directories count toward the
  resident budget.
- 5.10 Design iteration three adds allocation backpressure, also gated: an allocation that would
  abort instead stalls in bounded rounds; a progress guard aborts early if no buffer is returned
  pool-wide for two rounds, because then the threads that would return buffers are themselves blocked.
- 5.11 Every mechanism is off by default; with all gates closed the engine is byte-identical to the
  unmodified baseline, which makes every comparison in Chapter 7 an A/B test on one binary.

## Ch. 6 — Implementation

- 6.1 The store is created during query lowering; its configuration travels from the worker
  configuration through the engine builder into the store and the buffer manager.
- 6.2 The page-pooling boundary is one comparison in ChainedHashMap: a page is pooled if the
  configured page size fits the engine's buffer size; the engine default is 4096 bytes, our pages
  are 8192 bytes. *(Fact stated here; consequence measured in 7.6d.)*
- 6.3 The buffer manager gains three extensions: an availability query, pressure listeners (lever
  1b's hook), and the backpressure loop with a recycle counter as its progress signal.
- 6.4 Six gauges instrument the memory system: a 0.2-second cgroup sampler (anon/file/shmem), pool
  occupancy at rent time, unpooled live bytes, exact per-map composition at map destruction, the
  spill controller's view at every spiller wake, and per-stall backpressure events.
- 6.5 The harness runs each measurement cell in Docker with a hard memory cap and pinned CPUs, input
  on tmpfs, spill target on an SD card, one cell at a time, and writes one 34-column row per cell.
- 6.6 Correctness is verified by output checksums against the in-memory baseline; baseline
  equivalence of the gated binary is verified by a levers-off regression arm.

## Ch. 7 — Evaluation

### 7.1 Method
- 7.1.1 Workloads are synthetic sensor streams: Zipf-distributed keys, out-of-order arrival with
  jitter equal to the lateness bound, and density-matched scaling so that key count and rows per
  window grow with the target state size.
- 7.1.2 ρ is measured, not assumed: the uncapped in-memory footprint of each workload divided by the
  cap under test.
- 7.1.3 Three variants run on identical workloads: in-memory uncapped (correctness reference),
  in-memory capped (the failure the backend must prevent), and out-of-core bounded.
- 7.1.4 The matrix spans caps {512, 1024, 2048} MB, pool sizes, and ρ from 0.45 to ~3, with 8–10
  repetitions per cell; ~1000 cells in total.
- 7.1.5 Fixed configuration: W=50 k tuples, L=4 M (N_w=80), page size 8192, lz4, 16 MB write buffer,
  8 MB block cache, 4 partitions, 2 CPU cores.
- 7.1.6 Hypotheses for every follow-up experiment were pre-registered before the runs; the
  registration documents are in the repository.
- 7.1.7 Configuration discipline: every knob is pinned explicitly per cell, and one gauge is
  sanity-checked per run; Chapter 8 reports the incident that taught us this rule.

### 7.2 RQ1 — effectiveness
*(Two architectures are evaluated, named by their allocation type from point 2.10: the
engine-default configuration uses UNBOUNDED allocation (maps on the heap); the corrected
configuration uses BOUNDED allocation (maps in the pool, Chapter 5 iterations two onward, config
W). Why there are two is the subject of 7.6; here the windows of both are reported.)*
- 7.2.1 At 512 MB under the default architecture, the backend completed 14/24 runs where the
  in-memory engine completed 0/18 [E: Fisher p=4.1×10⁻⁵, flagship].
- 7.2.2 Output checksums match the in-memory reference while spilling is active, under both
  architectures [E: default arch ρ=1.56; corrected arch 4/4 identical, checksum 6620446084].
- 7.2.3 The default architecture has cap-dependent operating windows: 512 MB covers ρ≈1.2–2.2;
  1024 MB reaches ρ≥1.54; both p=3.6×10⁻⁴ [E: L-matrix]; at 2048 MB it completes heavy workloads
  (r500, r700 10/10) below ρ≈1 [E: corrected high-ρ cells].
- 7.2.4 Bounded allocation's window is governed by a two-sided geometry law, not by ρ: the pool must
  hold the hot band (lower bound), and the pool plus its own induced overhead must fit under the cap
  (upper bound — every resident slice carries unpooled directory bytes, so anon grows superlinearly
  with pool size) [E: 96k unlock vs 128k floor death vs 512/24k rc137 closure].
- 7.2.5 At geometry-correct pools (≈40% of cap), bounded allocation covers nearly the full heap
  window at the larger caps and adds cells no heap variant completes: 2048 → r200–r700 all complete
  (r700 8/8 at 45% throughput cost vs heap; r300 5/8 is bounded-ONLY — both heap arms 0/8); 1024 →
  r150 and r300 8/8; 512 → the upper bound sits below r150's band (base = 24% of cap), so only
  fits-cells and the throttled ρ≈2 cell complete — a permanent, explained small-cap limit
  [E: 96k/48k/24k runs].
- 7.2.5b The architectures' residual division: heap keeps the smallest cap, the fast-ingest valley
  cells, the throughput edge at the heavy end, and the lean-footprint case at big bands; bounded
  keeps the footprint guarantee, the bounded-only cells, and graceful degradation. r1000 at 2048
  fails under every configuration tested — the absolute edge [E: master table].
- 7.2.6 ρ alone does not decide completion: under the default architecture a ρ≈0.74 workload at
  2.9 M tuples/s fails where a ρ≈0.80 workload at 1.1 M tuples/s completes 10/10 [E: r300 vs r700
  at 2048]; under the corrected architecture completion at 512/16k is non-monotone in ρ
  (r075 8/8, r100 2/8, r150 1/8, r200 8/8) because fast ingest outruns the drain while heavy maps
  self-throttle into the pool's backpressure [E: W 512 column, survivor tps 3.33 vs 1.98 M].
- 7.2.7 Completion is therefore a race between ingest rate and drain rate in both architectures;
  this reframing drives the rest of the evaluation.

### 7.3 RQ2 — cost
*(All headline cost comparisons are made at MATCHED I/O granularity (~8 K buffers in every variant,
including the in-memory reference); 7.3.5 explains why.)*
- 7.3.1 At matched granularity, out-of-core costs 11–15% throughput where both variants complete,
  under either allocation type [E: I8/U8/W table].
- 7.3.2 The cost grows with state size and shrinks with memory headroom [E: tables, trend].
- 7.3.3 Under unbounded allocation, tail latency grows into milliseconds at ten-million-key working
  sets — the spill-restore round trip in the tail — but matched I/O granularity cuts it sharply
  (r700: p99 2509 → 899 µs, throughput +36%) [E: r700 default vs U8].
- 7.3.4 At matched granularity, out-of-core p99 is 1.1–1.6× the in-memory engine; an apparent 2–5×
  gap in earlier comparisons was the granularity, not the architecture — per-task latency scales
  with buffer size in every variant, including in-memory (96 → 219 µs) [E: I8 vs historic in-mem].
- 7.3.5 I/O buffer granularity is an engine-wide performance factor in its own right: raising the
  default 4096-byte buffers to ~8 K speeds up even the in-memory engine by up to 13% (3.25 → 3.62 M
  tuples/s) and dominates the shipped default at every tested cell — an actionable finding for the
  engine itself [E: I8 vs in-mem@4096; U8 vs default OOC].
- 7.3.6 Bounded allocation's footprint advantage is confirmed at matched granularity and is larger
  than any earlier estimate: −33% resident memory vs the in-memory engine (924 vs 1387 MB) and
  −27% vs heap-allocated out-of-core at the same cell [E: 2048/r200 across I8/U8/W].
- 7.3.7 Spill and restore latencies are reported as distributions [E: spill metrics lines].
- 7.3.8 The cost is configuration-point dependent; we report ranges, not single numbers.

### 7.4 RQ4 — CPU and I/O *(analysis pass over existing columns; no new runs)*
- 7.4.1 CPU utilization of the backend vs the in-memory engine across ρ.
- 7.4.2 Disk read and write volume scale with spilled state volume; SD-card write bandwidth bounds
  the drain rate from 7.2.6.

### 7.5 RQ5 — storage efficiency
- 7.5.1 Map state decomposes into 51% entry headers, 34% payload, 14% bucket directory, <1% slack
  [E: HMGAUGE, 26 M entries].
- 7.5.2 Eight bytes of every header is a virtual-table pointer that is never dispatched; removing it
  would cut state by 20% but requires an entry-inheritance refactor; documented, not implemented.
- 7.5.3 A one-line bucket-directory shrink did not work: two-thirds of maps obtain their directory
  size on paths the change did not cover; completion was unaffected [E: bucket-tweak cells, negative].
- 7.5.4 Write amplification from RocksDB statistics [coverage check pending; report subset honestly
  if partial].
- 7.5.5 A pool-sizing rule follows from the data: the pool must hold band + soft budget + emission
  pipeline + I/O, subject to pool + unpooled + base ≤ 0.9 × cap.

### 7.6 RQ3 — memory behavior and the stability investigation
*(Told in investigation order; each step was pre-registered before its run.)*

- 7.6a Failures map onto two independent constraints — pool demand ≤ pool size (else allocation
  abort) and pool floor + unpooled + base ≤ cap (else OOM kill) — which together form a feasibility
  corridor per cap and workload [E: 2D matrix structure; knife-edge example 2048/r700: pools 16k ✓
  32k ✓ 64k 0/10].
- 7.6b Where it completes, the backend is also lighter: 14–22% less resident memory than in-memory
  on identical workloads, because pooled pages are recycled on spill; the advantage grows as the
  pool shrinks [E: r500/r700 at 2048, −245/−363 MB].
- 7.6c The four levers of design iteration two worked mechanically but converted no failing cell;
  the obvious conclusion — an un-spillable floor — was wrong [E: S-matrix, 188 cells].
- 7.6d A design review found why: the engine's 4096-byte default (point 6.2) kept every map page
  unpooled in every experiment; the pool the levers defended contained no state; the floor
  conclusion was retracted [E: avail≈15933/16000 during build while maps held 110–364 MB].
- 7.6e One flag (buffer size 8192) engages the intended architecture; the decisive cell then reads:
  0/10 baseline → 0/16 levers-without-pooling → 0/8 pooling-without-levers → 5/8 → 7/8 with both
  [E: Fisher p=8.1×10⁻⁶ vs unpooled levers; p=7.0×10⁻⁴ vs pooling alone].
- 7.6f At that operating point the backend reaches throughput parity with the in-memory engine
  (3.38 vs 3.44 M tuples/s) at 25% less resident memory (517 vs 685 MB) and 2.2× p99 [E: money cell
  vs in-mem 1024/r150].
- 7.6g Pooling alone already helps elsewhere: blocked allocations throttle ingest to the drain rate,
  and a cell at 3/10 rises to 23/24 with no levers at all [E: 512/16k/r200, p=1.6×10⁻⁴].
- 7.6h Pooling rewrites the sizing rules: the pool is allocated up front and is therefore the
  resident floor; cells with large pools now die by OOM kill; lean pools win both footprint and
  survival, so the old footprint-vs-survival tradeoff dissolves [E: 1024/64k cells, old 10/10 cost
  cell now 0/8 OOM].
- 7.6i Three pre-registered alternative explanations for the remaining failures were refuted:
  deriving the soft threshold from the pool instead of the cap (0/8 everywhere), bounding source
  admission (max_inflight 64→16, no change), and bounding the task queue (1000→8, no change)
  [E: F-matrix + probe].
- 7.6j Allocation backpressure (design iteration three) also converted nothing — and its
  instrumentation supplied the diagnosis: in 118 of 138 stalls, no buffer was recycled anywhere in
  the pool for two full seconds [E: B3 cells, BPGAUGE].
- 7.6k The residual failure is therefore a role-blind allocation deadlock: emitted-but-unprocessed
  window state pins the pool; draining it requires worker threads; every worker is blocked
  allocating.
- 7.6l The results compose into a three-regime feasibility law: (i) demand fits the pool →
  completes at full speed; (ii) demand exceeds the pool but the memory cap throttles ingest →
  completes at reduced speed; (iii) demand exceeds the pool and ingest is unconstrained → deadlock
  [E: the same workload completes 23/24 at cap 512 and dies 0/8 at cap 1024 with twice the pool].
- 7.6m The recommended operating configuration is pooling + reserve + paced restore with
  backpressure off; backpressure costs 10% throughput near the edge and converts nothing, but its
  gridlock guard ships as diagnostic instrumentation.
- 7.6n The full-grid confirmation run (config W) adds three closing findings: soft thresholds must
  stay at the cap-derived rule — the pool-derived rule loses 8/8 → 3/8 at the decisive cell
  [E: V2 arbitration, p=0.026]; a memory-corruption crash cluster appears at high-churn
  cells (≈20 cells, only in cells failing anyway) [E: rc134/rc11, bad_function_call, malloc
  corruption] — root-caused in 7.6u; and the two architectures'
  windows are complementary (7.2.5), with the role-blind deadlock (7.6k) as the proven reason the
  pooled window cannot widen — making role-aware allocation the single mechanism that would merge
  the two windows.
- 7.6o The one-flag correction changes two things at once — allocation type AND I/O buffer
  granularity — so a dedicated disambiguation arm separates them: unbounded allocation at
  99.2%-matched granularity (buffer 8128 < page 8192 keeps maps on the heap) [E: U8 arm, smoke
  signature pool≈empty/maps 136 MB unpooled].
- 7.6p The disambiguation confirms the pooling attributions where it matters: the ρ≈2 win at 512 MB
  is allocation-type-causal (pooled 8/8 vs matched-granularity heap 0/8, p=7.8×10⁻⁵); the 512/r150
  regression is pooling's cost (heap keeps 7/8, pooled drops to 1/8, p=5.1×10⁻³); the high-ρ
  territory belongs to heap allocation (r700: heap 8/8 vs pooled 0/8, p=7.8×10⁻⁵); and the lever
  effect stands unchanged within fixed granularity (0/8 → 7/8, p=7.0×10⁻⁴).
- 7.6q One attribution is honestly mixed: at the money cell, granularity alone recovers 3/8 from
  0/10 (p=0.069) by eliminating the I/O-flood deaths, and pooling+levers reach 11/16 — the pairwise
  edge over matched-granularity heap is directionally positive but not separable at this sample
  size (p=0.15). The five-point ladder is reported in full: 0/10 → 3/8 → 0/8 → 5/8 → 7/8.
- 7.6r The initial all-zero bounded column at 2048 was pool geometry, not the deadlock: the tested
  pools bracketed but missed the working range, and the untested 96k point (39% of cap) completes
  r200 through r700 [E: sliver + boundary runs].
- 7.6s The geometry rule transplants across caps until the base footprint forbids it: 48k at 1024
  unlocks r150/r300; 24k at 512 dies rc137 because pool-induced directory overhead breaks the cap —
  the two-sided law of 7.2.4 in its final form.
- 7.6t The fast-ingest valley recurs at every cap and pool (512: r100–r150; 1024: r200 3/8;
  2048: r300 5/8): survivors complete below ~2.5 M tuples/s ingest; sprinters above ~2.7 M die —
  the race model's signature, now observed nine times across three caps and both architectures.
- 7.6u The crash cluster is root-caused by sanitizer instrumentation (manual arena poisoning, since
  both custom allocators are invisible to ASan): the per-worker slice cache — an engine default —
  hands compiled code raw map pointers that bypass every spill fence; a stale cached pointer races a
  legitimate restore into the same map (double-free, falsified both directions: cache-on 3/3 crash,
  cache-off 4/4 clean) [E: ASan report + falsification runs].
- 7.6v The obvious mitigation fails for a structural reason: with the cache off, every late write
  pins its slice until emission, and with jitter equal to the retention lag the entire band is
  pinned — spilling becomes impossible (0/8 at every cell including former winners, zero slices
  spilled). The two defects mask each other; the headline data (cache on) is unaffected because the
  corruption manifests only in cells that fail regardless [E: gauntlet run].
- 7.6w The architectural conclusion: unmediated pointers into evictable state require an
  invalidation protocol — Flink's backend mediates every state access, NebulaStream's slice cache
  does not; the precise upstream fix is epoch-stamped cache entries invalidated on spill (one
  compare per cache hit).

### 7.7 Summary
- 7.7.1 One table: each RQ → verdict → strongest single number.

## Ch. 8 — Discussion

- 8.1 Threats to validity, one point each: one physical machine and one SD card; synthetic workloads
  only (in scope by design); 4–17% between-session throughput drift, so all key contrasts are
  within-session; ρ for the largest workloads is derived from in-memory footprints at 2048 MB;
  checksum coverage is partial (two workload points per architecture); the allocation-type vs
  granularity confound existed in early cross-flag comparisons and was RESOLVED by the dedicated
  disambiguation arm (7.6o–7.6q) rather than left as a caveat.
- 8.2 The configuration-pinning lesson: an early sweep produced a fake optimum because one binary
  default (page size) differed from the baseline; the rule from 7.1.7 exists because of this.
- 8.3 Generalizability: the three-regime law and the deadlock pattern plausibly apply to any engine
  with one fixed shared buffer pool; the specific thresholds and the pooling boundary are
  NebulaStream-specific.
- 8.4 Practical recipe for an edge deployment: enable pooling, lean pool, soft ≈ pool/3, reserve 15%,
  paced restore, check the sizing rule from 7.5.5.

## Ch. 9 — Conclusion and Future Work

- 9.1 One conclusion sentence per contribution C1–C6.
- 9.2 Future work in order of precision: role-aware allocation (a drain-side reserve that build
  allocations cannot consume — the one mechanism the deadlock diagnosis isolates); closed-loop
  ingest admission keyed to pool headroom; the 20% header reduction; exception-safe restore;
  open-window spilling (requires Nautilus changes, explicitly out of scope here); sliding windows
  and Nexmark.
- 9.3 Upstream recommendations to NebulaStream, one sentence each: raise the 4096-byte
  operator-buffer default toward the page size (dominated at every tested cell, +13% even
  in-memory); make the pooled/unpooled allocation boundary explicit and observable instead of an
  implicit size comparison; ship the pool-pressure levers config-gated.

## Back matter
- Per OUTLINE.md: bibliography [1–14] + AI-tool citations; Further Use of AI Tools section
  (DIMA policy); Appendices A–E (full tables, gauge glossary, negative-results ledger, configuration
  reference, AI-generated content).
