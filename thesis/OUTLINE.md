# Thesis Outline — Out-of-Core Stream Processing in NebulaStream

Bachelor's thesis (12 ECTS), TU Berlin DIMA. Submission anchor: 2026-07-17.
Scaffolding per the signed proposal (DIMA template V2.4). Each section lists the MAIN POINTS to be
presented, the EVIDENCE it draws on, and the FIGURES/TABLES it produces. Evidence paths are relative
to `nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/`.

**RQ mapping (proposal §2.2 → working RQ numbering used in all analysis docs):**

| working | proposal question | status |
|---|---|---|
| RQ-0 (design) | "How can an OOC storage solution be integrated into NES's push-based execution model?" | answered by Ch. 4–5 |
| RQ1 (core) | success criterion §3.3: process windows whose state exceeds memory without OOM | answered, p<4×10⁻⁴ at two caps |
| RQ2 (core) | throughput + tail latency of the research-informed RocksDB config; spill/restore cost | answered, 3–22% / p99 µs→ms |
| RQ3 (core) | how memory behaves as state grows beyond capacity; why failures happen | answered: feasibility corridor + three-regime law |
| RQ4 (additional) | CPU utilization and disk I/O evolution | data in every cell TSV (cpu_usec, io_r, io_w) — analysis pass pending |
| RQ5 (additional) | storage efficiency: write amplification + footprint composition | footprint composition done (HMGAUGE); write-amp from RocksDB stats — coverage check pending |

Page budget (~50 pages total): Intro 4 · Background 6 · Related Work 3 · Design 6 · Implementation 5 ·
Evaluation 18 · Discussion 3 · Conclusion 2 · front/back matter ~3.

---

## Front matter
- **Declaration of Authorship** (sworn affidavit, DIMA policy page 1) — includes the generative-AI
  disclosure sentence (tool name/version/purpose).
- **Abstract** (~200 words): edge devices OOM on stateful windows → RocksDB-backed spillable slice
  store behind soft/hard watermarks → validated operating window at 512/1024 MB (Fisher p≤3.6×10⁻⁴,
  correct results while spilling) at 3–22% throughput cost → a one-flag architecture discovery
  (buffer pooling never engaged) whose correction unlocks in-memory-parity stability (0/10 → 7/8) with
  25% less resident RAM → residual failure formally diagnosed as a role-blind allocation deadlock with
  a three-regime feasibility law. Honest framing: effectiveness proven inside a characterized window,
  not universally.

## Ch. 1 — Introduction (≈4 pages)
**1.1 Motivation.** Proposal §1.1–1.4 verbatim ideas: in-memory operator state OOMs on edge devices;
cloud offloading is economically wrong at city scale (Horowitz [13], NES platform [1,2]); spilling to
cheap local storage keeps only final results moving.
**1.2 Problem statement.** Tumbling-window state > RAM on a single constrained node; NES push-based
execution may amplify contention (Marcu & Bouvry [14]) — the open question the proposal poses.
**1.3 Contributions (numbered list — the spine of the whole thesis):**
1. A spillable window-slice store for NES (RocksDB backend, soft/hard watermarks, background spiller,
   emit-lag retention) integrated WITHOUT touching the Nautilus JIT (scope §2.3.2 respected).
2. A reproducible cgroup-constrained evaluation harness with time-aligned memory-component
   instrumentation (mem_trace + 6 gauges), ~1000 measured cells, pre-registered hypotheses.
3. Empirical operating windows for OOC on edge budgets: cap-dependent ρ-ranges, with completion shown
   to be an ingest-vs-drain race, not a state-size threshold.
4. A load-bearing architecture finding: the engine's default `operator_buffer_size` (4096) silently
   disabled HashMap page pooling (page_size 8192) in every configuration — and the one-flag fix plus
   pool-pressure levers yields in-memory-parity stability at −25% resident RAM.
5. A formal failure taxonomy (three-regime feasibility law) ending in a diagnosed role-blind
   allocation deadlock, with the fix precisely specified as future work.
6. Open-source branch with config-gated levers (default-off = byte-identical baseline).
**1.4 Thesis structure** paragraph.

## Ch. 2 — Background (≈6 pages)
- **2.1 NebulaStream & push-based execution**: source→pipeline→sink, task-based query engine, the
  global BufferManager pool as THE shared resource (sizes, allocation paths — needed later for Ch. 6
  failure analysis); Nautilus JIT exists but is out of scope.
- **2.2 Windowed aggregation in NES**: tumbling windows, slices, ChainedHashMap state (entry layout:
  24 B header incl. vtable ptr, payload, bucket directory — sets up RQ5), watermarks/lateness,
  emit-lag L and retained windows N_w = L/W.
- **2.3 RocksDB / LSM**: memtable→SST, compaction, write amplification; the research-informed config
  (lz4, write-buffer 16 MB, block-cache 8 MB, partitions P=4) citing Jin [6], Pamarthi [5], Adão [7],
  Wei [8].
- **2.4 External-memory principle** (Graefe [3]): spill cold, keep hot — and the edge-specific twist:
  on a fixed small pool, "hot" includes the I/O pipeline, foreshadowing Ch. 6.
- **2.5 Metrics primer**: ρ (state-to-cap ratio, measured via uncapped/in-mem steady anon ÷ cap),
  cgroup anon/file/shmem, rc taxonomy (0 ok / 137 OOM-kill / 141 pool-exhaustion abort / 124 timeout).

## Ch. 3 — Related Work (≈3 pages)
- Flink + RocksDB as the reference integration [9] — pull-based checkpoint-centric vs NES push-based.
- WindFlow larger-than-memory [11], AION window state across memory/persistent storage [12] — closest
  academic neighbors; neither evaluates a push-based shared-pool engine under hard cgroup caps.
- Evaluation methodology: Fragkoulis survey [10] → single-node methodology; pre-registration and
  negative-result reporting as methodological stance (link to Ch. 6 discipline).
- Positioning sentence: this thesis is an *evaluation-first* contribution (proposal §1.3: systematic
  evaluation of push-based + RocksDB has not been conducted).

## Ch. 4 — Design (≈6 pages)
- **4.1 Requirements** from proposal §3.1: spill on soft watermark (preemptive, cold-first), hard
  watermark (mandatory), correctness preserved, no JIT changes, config-gated.
- **4.2 SpillableTimeBasedSliceStore**: architecture diagram — slice lifecycle (hot → cold → spilled →
  unspilled-for-trigger → emitted), background spiller (100 ms poll), SliceSerializer page format,
  residentBytes() accounting and its blind spots (honest design discussion — sets up §6.6).
- **4.3 Watermark semantics**: soft = preemptive spill threshold, hard = forced; threshold derivation
  (and the later-learned rule that budgets must derive from the POOL not the cap — forward reference).
- **4.4 Spill policy iterations** (each tested in Ch. 6): baseline cold-first; aggressive-spill;
  threshold map; **Tier-1 pool-pressure levers** (1a spiller drains on pool availability < reserve,
  1b allocation-pressure rescue listener, 1c paced trigger flush, 1d bucket-dir accounting); **N2
  allocation backpressure** (stall + recycle-epoch gridlock guard). Design rationale for each, one
  paragraph apiece; all config-gated, default-off.
- **4.5 What is deliberately NOT designed**: open-window spilling (would need Nautilus), sliding
  windows, durability/WAL (scope §2.3.2).

## Ch. 5 — Implementation (≈5 pages)
- **5.1 Integration points**: store creation in lowering (LowerToPhysicalWindowedAggregation),
  config plumbing (SpillConfig / QueryExecutionConfiguration → NodeEngineBuilder → BufferManager),
  AbstractBufferProvider extension (availability query + pressure-listener interface).
- **5.2 The pooling boundary** (the discovery, presented as implementation analysis):
  `ChainedHashMap::insertEntry` pools pages iff page_size ≤ operator_buffer_size; default 4096 < 8192
  ⇒ all map pages unpooled by default; bucket directories always unpooled. One flag changes the
  memory architecture of the whole engine. (Full impact deferred to §6.6 — here just the mechanism.)
- **5.3 Instrumentation** (contribution 2): mem_trace (0.2 s cgroup sampler), POOLGAUGE (1/4096 rents),
  UPOOLGAUGE, HMGAUGE (exact per-map composition at dtor), SPILLGAUGE (controller view per spiller
  wake), POOLRESCUE, BPGAUGE + final metrics lines; per-cell 34-column TSV; harness env-gating
  (RESERVEPCT/PACED/OPBUFSIZE/MAXINFLIGHT/BACKPRESSMS/ADMQ).
- **5.4 Reproducibility**: Docker cgroup caps (memory+swap), CPU pinning (CPUSET 0,1,8,9 / 2 worker
  threads), tmpfs input / SD-card spill target, sequential cells (no co-running), resumable runners,
  one-binary-many-configs gating (levers off = byte-identical), all runners + docs in repo.

## Ch. 6 — Evaluation (≈18 pages — the core)
- **6.1 Methodology.**
  - Workloads: synthetic Zipf-skewed, out-of-order (jitter D = lateness = 4 M), density-matched
    generator (keys/rows-per-window formulas); ρ-files r075…r1000; ρ_meas defined from measured
    uncapped anon (note for r500+: in-mem@2048 footprints are the ρ-truth — methodological note).
  - Fixed config: W=50 k, L=4 M (N_w=80), pageSize 8192, lz4/wbuf16/cache8/P=4, --cpus=2.
  - Variants: in-mem baseline (no cap = effectiveness reference; capped = the OOM victim), OOC
    bounded; matrix dims cap ∈ {512,1024,2048} × pool × ρ × 8–10 reps.
  - Statistics: Fisher exact (completion), Welch (tps/p99); pre-registration of hypotheses before
    each run (EXPERIMENT-stability-levers.md §1, §7, §8, §9 as the audit trail).
  - **Configuration-pinning discipline** (lesson learned, presented as method): every knob pinned
    explicitly; one-gauge sanity check per run (the pageSize confound caught this way — moved the
    full story to §7.1 threats).
- **6.2 RQ1 — Effectiveness (does OOC prevent OOM beyond the budget?).**
  - Flagship 512 MB: bounded 14/24 vs in-mem 0/18, Fisher p = 4.1×10⁻⁵; **checksum MATCH while
    actively spilling** (correctness under spill).
  - Per-cap windows (best pool, 10 reps): 512→ρ∈[≈1.2, ≈2.2] (r150 8/10 vs 0/10, p=3.6×10⁻⁴);
    1024→ρ≥1.54 (r300 8/10 vs 0/10, p=3.6×10⁻⁴); 2048→no demonstrated win above ρ≈1 (honest).
  - **The race caveat** (key insight): ρ alone does not determine completion — old-r300 (ρ≈0.74,
    2.9 M tps) fails where r700 (ρ≈0.80, self-throttled 1.14 M tps) completes 10/10: completion =
    ingest rate vs spill-drain rate. FIGURES: completion heatmap cap×pool×ρ; flagship Fisher bar;
    ingest-rate scatter (tps vs outcome at matched ρ).
- **6.3 RQ2 — Cost (throughput, tail latency, spill/restore).**
  - Both-arms-succeed table: Δtps −3% (flagship sweet spot) to −22% (r700@2048), growing with state
    size, shrinking with headroom; p99 µs→ms at 10⁷-key working sets (460 µs → 2509 µs) = the
    spill/unspill round-trip surfacing in the tail.
  - Spill/restore latencies from Spill metrics lines (per-slice spill/unspill cost distribution).
  - Report the RANGE, not one number (config-point dependence). FIGURES: Δtps and p99-ratio vs ρ per
    cap; spill/restore latency CDF.
- **6.4 RQ4 (additional) — CPU & disk I/O evolution.** cpu_usec + cgroup io.stat per cell: CPU
  utilization vs ρ (in-mem vs OOC), read/write bytes vs spilled-state volume; SD-card as realistic
  edge storage. [Analysis pass still to run over existing TSV columns — no new experiments needed.]
- **6.5 RQ5 (additional) — Storage efficiency & footprint composition.**
  - HMGAUGE: header 51% / payload 34% / bucket-dir 14% / slack 0.7% at 26 M entries; the 8 B
    never-dispatched vtable pointer = 20% reduction documented option (entry-inheritance refactor,
    measured but deferred); bucket-tweak negative result (mixed bucket counts — 2/3 of maps sized
    elsewhere; no completion effect).
  - RocksDB on-disk footprint + write amplification from captured RocksDB stats [coverage check
    pending — if write-amp not uniformly captured, report for instrumented subset + say so].
  - Pool-sizing formula: pool ≥ band + soft + emit pipeline + I/O; feasibility check
    8.25 KB·pool + unpooled + base ≤ 0.9·cap. FIGURES: composition stacked bar; footprint vs ρ.
- **6.6 RQ3 — Memory behavior & the stability investigation (the thesis's distinctive chapter).**
  Structured as the actual investigation arc, each step pre-registered:
  1. **Failure anatomy on the 2D map**: two independent constraints (peak pool demand ≤ pool;
     pool floor + unpooled + base ≤ cap) → feasibility corridor; knife-edge pool sensitivity
     (2048/r700: 16k ✓ 32k ✓ 64k 0/10 OOM).
  2. **Resident-RAM advantage**: OOC anon 14–22% below in-mem at 2048 (r500 −245 MB, r700 −363 MB);
     mechanism = pooled-page recycling (HashMap residual 620→210 MB); corollary: advantage GROWS
     with leaner pools.
  3. **The unpooled-pool discovery** (§7 design review): every prior run had maps on the heap; pool
     was I/O-only; errata (pool-MB labels 2×); retraction of the "hot-band floor" claim — presented
     as finding + corrected interpretation, demonstrating the audit trail.
  4. **The unlock (N1)**: attribution ladder at 1024/32k/r150 — historic 0/10 → unpooled levers 0/16
     → pooled-no-levers 0/8 → +reserve 5/8 → +paced **7/8** (p = 8.1×10⁻⁶ vs unpooled levers); at
     **tps parity with in-mem** (3.38 vs 3.44 M) and **−25% steady anon** (517 vs 685 MB), p99 2.2×.
     Plus: pooled allocation = intrinsic backpressure (512/16k/r200: 3/10 → 23/24 even leverless,
     p = 1.6×10⁻⁴). New rules: pool = upfront anon floor (big pools now OOM); lean pools win
     footprint AND survival — the tradeoff dissolves.
  5. **The refutation ledger (§8)**: soft-from-pool refuted, max_inflight refuted, task-queue bound
     refuted — every cheap explanation eliminated, all pre-registered.
  6. **The deadlock diagnosis (§9 / N2)**: allocation backpressure with recycle-epoch gridlock guard;
     138 stalls → 118 gridlock aborts (zero pool-wide recycles in 2 s), 10 resolved, 0 budget —
     regime-3 failures are a role-blind allocation DEADLOCK (emitted slice state pins the pool;
     draining needs the blocked workers), not a throughput race.
  7. **The three-regime feasibility law** (the capstone): (i) fits → full speed; (ii) demand > pool
     but cgroup throttles ingest → completes (512/16k/r200 23/24 @2.5 M tps; same workload 0/8 at
     cap 1024 with double the pool, dying at 3.2 M tps); (iii) unconstrained → deadlock. Ship config
     = B2 (pooled + reserve + paced, backpressure off).
  FIGURES: money-cell attribution ladder; anon(t)/avail_buf(t)/resident(t) overlay at the money cell
  (the "reserve being defended" trace); three-regime diagram; gridlock death timeline
  (SPILLGAUGE + BPGAUGE); regime-2 vs regime-3 same-workload contrast.
- **6.7 Summary table**: every RQ → verdict → strongest single number.

## Ch. 7 — Discussion (≈3 pages)
- **7.1 Threats to validity**: single machine/single SD card; synthetic workloads only (scope);
  session drift (4–17% tps across sessions → within-session contrasts preferred — state this as
  measured); the pageSize confound incident + the pinning discipline that fixed it; ρ_meas for
  r500+ derived from in-mem@2048; checksum coverage partial (proven at two ρ points).
- **7.2 Generalizability**: which findings are NES-specific (pool architecture, push-based emit
  floods) vs general (three-regime law plausibly applies to any fixed-pool streaming engine;
  external throttling as accidental governor).
- **7.3 Practical guidance**: config recipe for an edge deployment (B2 + lean pool + soft≈pool/3 +
  cap-aware sizing formula) — the "engineer's takeaway" box.

## Ch. 8 — Conclusion & Future Work (≈2 pages)
- Conclusions mirror the contribution list 1:1 (each with its number).
- Future work, in order of precision: (1) role-aware allocation — drain-side reserve the build path
  cannot consume (specified at the AbstractBufferProvider level, ~the one lever the diagnosis
  isolates); (2) closed-loop ingest admission on pool headroom (what the cgroup does by accident);
  (3) vtable removal (−20%/entry, needs entry-inheritance refactor); (4) open-window spilling
  (Nautilus scope); (5) sliding windows, Nexmark (proposal stretch goal).

## Back matter
- **Bibliography**: proposal [1]–[14] + AI-tool citations per DIMA policy.
- **Further Use of AI Tools** (DIMA policy, mandatory): Claude Code (Anthropic; model + version) —
  purposes: implementation assistance for the evaluated levers/instrumentation, experiment
  orchestration and statistical analysis, language checking; per policy: prompts/chat histories
  documented; all design decisions, interpretations, and text are the author's responsibility.
- **Appendix A**: full result tables (per-cell completion/rc/tps/p99/anon per matrix).
- **Appendix B**: gauge & metric glossary (one table: gauge → what → sampling rate → file).
- **Appendix C**: negative-results ledger (bucket tweak; aggressive/lazy thresholds; soft-from-pool;
  max_inflight; admission-queue bound; backpressure-as-fix) — each with one-line evidence.
- **Appendix D**: configuration reference (every pinned knob + the env-gate table from run-cell.sh).
- **Appendix E**: AI Generated Content (screenshots/exports per DIMA policy).

---

## Open items before/while writing (tracked)
1. RQ4 analysis pass over existing cpu_usec/io columns (no new runs needed).
2. RQ5 write-amplification coverage check in captured RocksDB stats; report subset if partial.
3. Figure production pass (the 9 named figures above) from existing TSVs/traces/gauges.
4. Decide LaTeX skeleton (TU Berlin/DIMA class) and port this outline into it.
