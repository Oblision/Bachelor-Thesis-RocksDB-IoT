# Point Sequence — the draft in reading order (review document)

2026-06-12. This is the merge of WRITING-POINTS.md (the ~140 ordered claims, IDs kept for
traceability) into the official DIMA 7-chapter template structure. One bullet = one paragraph
(or merges with its neighbor). Review pass: confirm order, spot gaps, mark cuts.

Flags used: **[MOVED]** point re-homed by the template's chapter order · **[NEW]** newer than
OUTLINE.md, from the final experiment rounds · **[DECISION]** needs your call.

**Construction rules (binding, unchanged):** one point per sentence; declarative; no concept used
before it is introduced; every empirical claim becomes a table/figure reference; negative results
stated as plainly as positive ones.

**[DECISION D1] Related Work moves to Chapter 6 (template order).** WRITING-POINTS rule 3 assumed
Related Work directly after Background ("a reader who knows only Ch. 2–3 can follow everything").
With the template order, the reader meets Design/Evaluation before Related Work. The concept ledger
shows no concept is *introduced* in Related Work (all come from Background and the Solution
chapter), and Flink-as-reference-integration already appears in the Introduction (1.7). So the move
is safe if Background carries all terminology — which it does. Confirm or we argue for deviating
from template order with the advisor.

---

## Chapter 1 — Introduction (~4 pages)

| # | Point (one paragraph each) |
|---|---|
| 1.1 | Stream processing systems compute continuously over unbounded data streams. |
| 1.2 | Stateful operators, such as windowed aggregations, store intermediate state between events. |
| 1.3 | NebulaStream is a stream processing system for the Internet of Things; it keeps all operator state in main memory. |
| 1.4 | Edge and IoT devices have small main memories. |
| 1.5 | When operator state grows beyond available memory, the operating system terminates the process; processing halts. |
| 1.6 | Provisioning more memory or moving computation to the cloud defeats the purpose of edge processing — data movement dominates its cost [13,1]. |
| 1.7 | Cloud stream processing systems solve this with an out-of-core state backend; RocksDB is the industry standard [9]. |
| 1.8 | NebulaStream is push-based; this model + an out-of-core backend has not been evaluated systematically. |
| 1.9 | This thesis builds an out-of-core backend for tumbling-window state in NES and evaluates it on edge-class memory budgets. |
| 1.10 | Result preview in one paragraph: no crashes inside a characterizable operating window; 3–22% throughput cost; one engine default silently disabled the intended memory architecture — the one-flag fix + pool-pressure mechanisms give in-memory-parity stability at 25% less resident memory; the remaining failure mode is precisely diagnosed. |
| 1.11 | Contributions C1–C6 (backend; instrumented harness; operating windows; pooling discovery + unlock; failure taxonomy; open-source branch). |
| 1.12 | Chapter guide, one sentence per chapter — bridge into Background. |

## Chapter 2 — Scientific Background (~4–6 pages, WRITTEN FIRST)

*Every point introduces exactly one concept. Section grouping for readability:*

**2.A Stream processing and NebulaStream** (§2.1)
| # | Point |
|---|---|
| 2.1 | NES compiles queries into pipelines of operators [1,2]. |
| 2.2 | Execution is push-based: sources push tuples through pipelines; no consumer polls [14]. |
| 2.3 | A fixed set of worker threads executes pipeline work as tasks from a queue. |
| 2.4 | Tuples travel and are processed in fixed-size buffers. |
| 2.5 | All buffers come from ONE global buffer pool — fixed size, allocated once at engine start. *(The central shared resource of the thesis.)* |
| 2.6 | A thread requesting a buffer from an empty pool waits up to one second, then the engine aborts the query. |

**2.B Windowed aggregation** (§2.2)
| # | Point |
|---|---|
| 2.7 | Tumbling window: fixed-length, non-overlapping intervals; one aggregate per key per window. |
| 2.8 | NES stores window state in slices; a slice holds one window interval's aggregation state. |
| 2.9 | Slice state is a hash map (ChainedHashMap): entries on fixed-size pages; bucket directory; 24-byte header + payload per entry. |
| 2.10 | **Binding vocabulary:** map pages come from **bounded allocation** (pages from the fixed global pool, returned on release) or **unbounded allocation** (general-purpose process heap, limited only by the external cap); which one happens depends on configured page size vs engine buffer size. *(Stated as engine property; consequences in Ch. 5.)* |
| 2.11 | Out-of-order arrival; lateness bound; watermark = time up to which results may be finalized. |
| 2.12 | Completed windows are retained for emit lag L; with window length W the store retains N_w = L/W windows. |

**2.C RocksDB and the LSM tree** (§2.3)
| # | Point |
|---|---|
| 2.13 | RocksDB is a persistent key-value store with log-structured merge design [4]. |
| 2.14 | Writes → in-memory memtable → flushed to sorted files; background compaction merges files. |
| 2.15 | Compaction rewrites data; bytes-to-disk ÷ bytes-from-application = write amplification [6,8]. |

**2.D External-memory principle** (§2.4)
| # | Point |
|---|---|
| 2.16 | Keep hot data in memory, move cold data to disk [3]. *(Edge twist foreshadowed: on a fixed small pool, "hot" includes the I/O pipeline.)* |

**2.E Metrics and terms** (§2.5)
| # | Point |
|---|---|
| 2.17 | Linux cgroups enforce hard memory limits; exceeding them triggers the kernel OOM killer. |
| 2.18 | ρ = steady-state footprint ÷ memory cap; ρ > 1 means the state does not fit. |
| 2.19 | Four run outcomes: completion / OOM kill / allocation abort (2.6) / timeout. |
| 2.20 | Fisher's exact test for completion proportions; Welch's t-test for means. Bridge into Ch. 3. |

## Chapter 3 — Research Problem (~2 pages) [MOVED: was WP Ch. 4]

| # | Point |
|---|---|
| 4.1 | Goal: store tumbling-window operator state larger than memory in NES, using a research-informed RocksDB configuration. |
| 4.2 | RQ0: how can an out-of-core backend integrate into the push-based execution model? |
| 4.3 | RQ1: does it prevent OOM failures beyond the memory budget — and where? |
| 4.4 | RQ2: at what cost (throughput, tail latency, spill/restore)? |
| 4.5 | RQ3: how does memory behave past the cap; why do residual failures happen? |
| 4.6 | RQ4 (additional): CPU utilization and disk I/O evolution. |
| 4.7 | RQ5 (additional): storage efficiency (write amplification, footprint composition). |
| 4.8 | Scope: tumbling only, single node, no JIT changes, no fault tolerance; success = complete beyond-memory workloads, verified against a sufficient-memory baseline. Bridge into Ch. 4. |

## Chapter 4 — A Spillable Slice Store for NebulaStream (~11 pages) [MOVED: WP Ch. 5 + Ch. 6 merged]

**4.A Design** (§4.1–4.5)
| # | Point |
|---|---|
| 5.1 | Four requirements: spill before pressure is fatal; force spill under acute pressure; never change results; leave the JIT untouched. |
| 5.2 | Replace the in-memory slice store behind the SAME interface; operators above are unchanged. |
| 5.3 | Slice lifecycle: hot → cold → spilled → restored → emitted. FIGURE: architecture/lifecycle. |
| 5.4 | Soft watermark = preemptive spilling: background spiller moves cold slices to RocksDB when resident slice bytes exceed the soft threshold. |
| 5.5 | Hard watermark = forced synchronous spilling when the soft mechanism falls behind. |
| 5.6 | Slices serialize page-by-page into RocksDB key-value pairs; layout preserved so restore is a byte copy. |
| 5.7 | Before emission, spilled slices are restored. |
| 5.8 | The controller's signal is resident slice bytes; other pool consumers are invisible to it. *(Design property; Ch. 5 measures the consequences.)* |
| 5.9 | Iteration two — four config-gated pool-pressure levers: (1a) drain when free pool < reserve; (1b) empty-pool allocation triggers immediate spill of one cold slice; (1c) paced restore (only with pool headroom); (1d) bucket directories count toward the budget. |
| 5.10 | Iteration three — allocation backpressure, gated: would-abort allocations stall in bounded rounds; progress guard aborts early if no buffer returns pool-wide for two rounds. |
| 5.11 | Everything off by default = byte-identical engine; every comparison in Ch. 5 is an A/B test on one binary. |

**4.B Implementation** (§4.6–4.9)
| # | Point |
|---|---|
| 6.1 | Store created during query lowering; configuration travels worker-config → engine builder → store + buffer manager. |
| 6.2 | The page-pooling boundary is ONE comparison in ChainedHashMap: pooled iff page size ≤ engine buffer size; default 4096 < our 8192. *(Fact here; consequence measured in Ch. 5.)* |
| 6.3 | Buffer manager gains: availability query; pressure listeners (lever 1b's hook); backpressure loop with recycle counter as progress signal. |
| 6.4 | Six gauges: 0.2-s cgroup sampler; pool occupancy at rent; unpooled live bytes; exact per-map composition at destruction; spill-controller view per wake; per-stall backpressure events. |
| 6.5 | Harness: Docker hard cap + pinned CPUs, tmpfs input, SD-card spill target, one cell at a time, 34-column row per cell. |
| 6.6 | Correctness via output checksums vs in-memory baseline; gated-binary equivalence via levers-off regression arm. Bridge into Ch. 5. |

## Chapter 5 — Experimental Evaluation (~18 pages, the core)

**5.1 Experimental setup** — WP 7.1.1–7.1.7
| # | Point |
|---|---|
| 7.1.1 | Synthetic sensor streams: Zipf keys, out-of-order (jitter = lateness), density-matched scaling. |
| 7.1.2 | ρ is measured, not assumed (uncapped in-memory footprint ÷ cap under test). |
| 7.1.3 | Three variants on identical workloads: in-mem uncapped (correctness reference), in-mem capped (the failure to prevent), out-of-core bounded. |
| 7.1.4 | Matrix: caps {512, 1024, 2048} MB × pools × ρ 0.45–~3 × 8–10 reps; ~1000 cells. |
| 7.1.5 | Fixed config: W=50 k, L=4 M (N_w=80), page 8192, lz4/wbuf16/cache8/P=4, 2 cores. |
| 7.1.6 | All follow-up hypotheses pre-registered before runs; registration docs in the repository. |
| 7.1.7 | Configuration discipline: every knob pinned; one gauge sanity-checked per run; the teaching incident reported in §5.8. |

**5.2 RQ1 — Effectiveness** — WP 7.2.1–7.2.10
*(Two architectures named by allocation type from 2.10: default = UNBOUNDED (heap maps); corrected = BOUNDED (pooled maps). Why there are two is §5.6's subject; here both windows are reported.)*
| # | Point |
|---|---|
| 7.2.1 | 512 MB, default arch: 14/24 vs in-mem 0/18 [Fisher p=4.1×10⁻⁵, flagship]. |
| 7.2.2 | Checksums match the in-memory reference WHILE spilling, both architectures [ρ=1.56; 4/4 identical]. |
| 7.2.3 | Default arch windows: 512 → ρ≈1.2–2.2; 1024 → ρ≥1.54 (both p=3.6×10⁻⁴); 2048 → heavy workloads complete below ρ≈1. |
| 7.2.4 | **[NEW]** Bounded arch window is governed by a two-sided geometry law, not ρ: pool ≥ hot band AND pool + induced overhead ≤ cap (anon grows superlinearly with pool size). |
| 7.2.5 | **[NEW]** At geometry-correct pools (≈40% of cap): 2048 → r200–r700 all complete, r300 5/8 is bounded-ONLY (both heap arms 0/8); 1024 → r150/r300 8/8; 512 → upper bound below r150's band — a permanent, explained small-cap limit. |
| 7.2.5b | **[NEW]** Residual division of labor between the architectures; r1000@2048 fails under every configuration — the absolute edge. |
| 7.2.6 | ρ alone does not decide completion: ρ≈0.74 @2.9 M tps fails where ρ≈0.80 @1.1 M tps completes 10/10; bounded completion at 512/16k is non-monotone in ρ (8/8, 2/8, 1/8, 8/8). |
| 7.2.7 | Completion is a race between ingest rate and drain rate — the reframing that drives the rest of the chapter. |
| 7.2.8 | **[NEW]** Transfer to realistic workload (Nexmark-style bid stream): bounded completes what in-mem cannot (8/8, 7/8 vs 0/8; p=7.8×10⁻⁵, 7.0×10⁻⁴), verified OOC work (610 MB spilled). Supporting evidence; synthetic grid stays primary. |
| 7.2.9 | **[NEW]** Same run = the failure model's out-of-distribution test, passed: zero corruption in 210 in-order cells (predicted); every bounded failure lands where the two-sided law puts it. |
| 7.2.10 | **[NEW]** Honest transfer cost: in-order streams pay the full round trip the synthetic jitter masked; fits-cells over-spill (−42…−54%); win cell −46…−59% vs unlimited reference; state-aware threshold = documented future work, deliberately not tuned on evaluation workloads. |

**5.3 RQ2 — Cost** — WP 7.3.1–7.3.8
*(All headline comparisons at MATCHED I/O granularity ~8 K; 7.3.5 explains why.)*
| # | Point |
|---|---|
| 7.3.1 | Matched granularity: −11…−15% throughput where both arms complete, either allocation type. |
| 7.3.2 | Cost grows with state size, shrinks with headroom. |
| 7.3.3 | Unbounded: p99 into ms at 10⁷-key sets; matched granularity cuts it sharply (r700: 2509→899 µs, +36% tps). |
| 7.3.4 | **[NEW]** Matched-granularity p99 = 1.1–1.6× in-mem; the earlier 2–5× gap was granularity, not architecture. |
| 7.3.5 | **[NEW]** I/O buffer granularity is an engine-wide factor by itself: ~8 K buffers speed the IN-MEMORY engine up to +13% — an actionable engine finding. |
| 7.3.6 | **[NEW]** Bounded footprint advantage at matched granularity: −33% vs in-mem (924 vs 1387 MB), −27% vs heap OOC. |
| 7.3.7 | Spill and restore latencies as distributions. |
| 7.3.8 | Cost is configuration-point dependent; ranges, not single numbers. |

**5.4 RQ4 — CPU and disk I/O** — WP 7.4.1–7.4.2 (+ DATA-AUDIT numbers)
| # | Point |
|---|---|
| 7.4.1 | CPU vs ρ, backend vs in-mem (in-mem 91–97% CPU, zero I/O; spilling winners 89–92%; throttled regime 63% = I/O-bound signature; heavy bounded 54% = backpressure idle). |
| 7.4.2 | Disk read/write scale with spilled volume; SD-card write bandwidth bounds the drain rate from 7.2.6. |

**5.5 RQ5 — Storage efficiency** — WP 7.5.1–7.5.5
| # | Point |
|---|---|
| 7.5.1 | Composition at 26 M entries: 51% headers / 34% payload / 14% directory / <1% slack. |
| 7.5.2 | 8 B per header = never-dispatched vtable pointer; removing it cuts state 20%; needs refactor; documented, not implemented. |
| 7.5.3 | Bucket-directory one-liner did NOT work (negative result, stated plainly). |
| 7.5.4 | Write amplification: device-level proxy io_w/spill-dir = 1.0–1.8; NOT RocksDB's internal counter — stated as such. |
| 7.5.5 | Pool-sizing rule: pool ≥ band + soft + emission pipeline + I/O, subject to pool + unpooled + base ≤ 0.9·cap. |

**5.6 RQ3 — Memory behavior and the stability investigation** — WP 7.6a–7.6w
*(Told in investigation order; every step pre-registered.)*
| # | Point |
|---|---|
| 7.6a | Two independent constraints → feasibility corridor; knife-edge example (2048/r700: 16k ✓ 32k ✓ 64k 0/10). |
| 7.6b | Where it completes, the backend is lighter: −14…−22% resident vs in-mem; recycling mechanism; advantage grows with leaner pools. |
| 7.6c | The four levers converted NO failing cell; the obvious conclusion (un-spillable floor) was wrong. |
| 7.6d | Design review found why: the 4096-byte default kept every map page unpooled in every experiment; the pool the levers defended contained no state; retraction documented. |
| 7.6e | One flag engages the intended architecture; the decisive cell ladder: 0/10 → 0/16 → 0/8 → 5/8 → 7/8 [p=8.1×10⁻⁶]. |
| 7.6f | At that point: throughput parity with in-mem (3.38 vs 3.44 M tps), −25% resident (517 vs 685 MB), p99 2.2×. |
| 7.6g | Pooling alone helps elsewhere: 3/10 → 23/24 with no levers [p=1.6×10⁻⁴] — intrinsic backpressure. |
| 7.6h | Pooling rewrites sizing: pool = upfront resident floor; big pools now OOM; lean pools win footprint AND survival — the tradeoff dissolves. |
| 7.6i | Refutation ledger: soft-from-pool, max_inflight, task-queue bound — all pre-registered, all refuted. |
| 7.6j | Backpressure converted nothing — but its instrumentation supplied the diagnosis: 118/138 stalls with zero pool-wide recycling for 2 s. |
| 7.6k | Residual failure = role-blind allocation deadlock: emitted state pins the pool; draining needs workers; all workers blocked allocating. |
| 7.6l | Three-regime feasibility law: (i) fits → full speed; (ii) cap throttles ingest → completes reduced; (iii) unconstrained → deadlock. Same workload: 23/24 at cap 512, 0/8 at cap 1024 with twice the pool. |
| 7.6m | Ship config = pooling + reserve + paced, backpressure OFF (costs 10%, converts nothing; gridlock guard ships as diagnostics). |
| 7.6n | **[NEW]** Full-grid confirmation (W): cap-derived soft threshold confirmed (pool-derived loses 8/8→3/8); crash cluster appears at high-churn cells (root-caused in 7.6u); windows are complementary; role-aware allocation = the single merging mechanism. |
| 7.6o | **[NEW]** The one-flag fix changes TWO things (allocation type + granularity) → dedicated disambiguation arm (U8: heap maps at 99.2%-matched granularity). |
| 7.6p | **[NEW]** Disambiguation confirms: ρ≈2 win at 512 is allocation-type-causal (8/8 vs 0/8, p=7.8×10⁻⁵); 512/r150 regression is pooling's cost; high-ρ territory belongs to heap; lever effect stands within fixed granularity. |
| 7.6q | **[NEW]** One honestly mixed attribution at the money cell: granularity alone recovers 3/8 (p=0.069); pooled edge directionally positive, not separable (p=0.15); the five-point ladder reported in full. |
| 7.6r | **[NEW]** The all-zero bounded column at 2048 was pool GEOMETRY, not deadlock: tested pools bracketed the working range; 96k completes r200–r700. |
| 7.6s | **[NEW]** Geometry rule transplants across caps until base footprint forbids it (48k@1024 unlocks; 24k@512 dies rc137) — the two-sided law in final form. |
| 7.6t | **[NEW]** The fast-ingest valley recurs at every cap and pool: survivors <~2.5 M tps, sprinters >~2.7 M die — the race model's signature, observed nine times. |
| 7.6u | **[NEW]** Crash cluster root-caused (manual arena poisoning for ASan): the per-worker slice cache (engine default) hands compiled code raw map pointers bypassing every spill fence; stale pointer races a restore (falsified both directions: cache-on 3/3 crash, cache-off 4/4 clean). |
| 7.6v | **[NEW]** Obvious mitigation fails structurally: cache-off pins every late-write slice until emission → with jitter = retention lag the whole band is pinned, spilling impossible (0/8 everywhere). The two defects mask each other; headline data unaffected (corruption only in already-failing cells). |
| 7.6w | **[NEW]** Architectural conclusion: unmediated pointers into evictable state need an invalidation protocol (Flink mediates every access; NES's slice cache does not); precise upstream fix = epoch-stamped cache entries invalidated on spill. |

**5.7 Summary** — WP 7.7.1: one table, each RQ → verdict → strongest single number.

**5.8 Threats to validity** [MOVED: WP 8.1–8.2 live here per template — interpretation stays with the experiments]
| # | Point |
|---|---|
| 8.1 | One point each: one machine, one SD card; synthetic only (in scope by design); 4–17% session drift → within-session contrasts; ρ for largest workloads derived from in-mem@2048; partial checksum coverage; the allocation-vs-granularity confound RESOLVED by the disambiguation arm, not left as caveat. |
| 8.2 | The configuration-pinning lesson: an early sweep produced a fake optimum via one differing binary default (page size); rule 7.1.7 exists because of this. Bridge into Ch. 6. |

## Chapter 6 — Related Work (~3 pages) [MOVED: WP Ch. 3; drafted second, placed sixth]

| # | Point |
|---|---|
| 3.1 | Flink ships RocksDB as its production OOC backend — the reference integration [9]. |
| 3.2 | Flink's runtime differs: checkpoint-oriented state access, pull-based exchange at the boundaries Marcu & Bouvry analyze [14]; results do not transfer directly. |
| 3.3 | WindFlow: larger-than-memory windowed processing single-node; the problem is open beyond Flink [11]. |
| 3.4 | AION: window state across memory/persistent storage, hot/cold separation — matches our slice granularity [12]. |
| 3.5 | RocksDB tuning studies: parameters mitigate but do not remove memory/I/O sensitivity [5,6,7,8] → we fix ONE research-informed configuration instead of searching. |
| 3.6 | Fragkoulis et al. survey → single-node methodology [10]. |
| 3.7 | Gap statement: no published work evaluates an OOC window backend inside a push-based engine with one shared global pool under hard memory caps; this thesis does. Bridge into Ch. 7. |

## Chapter 7 — Conclusion (~3 pages) [MOVED: WP Ch. 9 + Discussion remnants 8.3–8.4]

| # | Point |
|---|---|
| 9.1 | One conclusion sentence per contribution C1–C6 (problem + methodology restated first). |
| 8.3 | Generalizability: three-regime law + deadlock pattern plausibly apply to any fixed-shared-pool engine; thresholds + pooling boundary are NES-specific. |
| 8.4 | Practical recipe: pooling on, lean pool, soft ≈ pool/3, reserve 15%, paced restore, sizing rule from 7.5.5. |
| 9.2 | Future work in order of precision: role-aware allocation (drain-side reserve build allocations cannot consume); closed-loop ingest admission on pool headroom; 20% header reduction; exception-safe restore; epoch-stamped cache invalidation (from 7.6w); open-window spilling (Nautilus); sliding windows. |
| 9.3 | Upstream recommendations to NES, one sentence each: raise the 4096 B default toward page size (+13% even in-memory); make the allocation boundary explicit and observable; ship the levers config-gated. |

## Back matter
- Bibliography [1]–[14] + AI-tool citations (bibliography.bib already carries extended keys from the May draft — prune/verify against final citations).
- declaration-of-aids.tex: levels-of-use table + links to docs/ai-usage phase folders (+ the new initial-draft folder when added).
- Appendices A–E: full result tables · gauge glossary · negative-results ledger · configuration reference · AI-generated content.

---

## Open questions for this review
- **D1** (top): Related Work at position 6 — accept template order?
- **D2:** 7.2.8–7.2.10 (Nexmark) — include as §5.2 subsection "Transfer to a realistic workload"? It is supporting evidence per WP; alternative is an appendix. Recommendation: keep in §5.2, clearly labeled supporting.
- **D3:** 7.6u–7.6w (slice-cache corruption) — full subsection in §5.6, or compressed into one paragraph + threats? WP treats it as part of the investigation arc. Recommendation: keep, it is pre-registered and root-caused.
- **D4:** WP 7.5.4 write-amplification coverage check is still pending (DATA-AUDIT soft spot 4) — run before §5.5 is drafted.
- **D5:** OUTLINE's Abstract mentions p≤3.6×10⁻⁴ numbers from the older framing; abstract will be rewritten LAST from the final chapter set.
