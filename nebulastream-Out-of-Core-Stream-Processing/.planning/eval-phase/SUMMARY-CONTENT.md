# 5-page summary — content brief (feeds /frontend-design)

Audience: thesis advisor / reader. Tone: simple, iterative — each section builds on the previous. Diagrams + code
blocks must have generous spacing (no overlap). Embed the E4 figures (in e3/figs/) once generated.

## Headline (the one-sentence result)
Partition-at-spill lets a tumbling windowed aggregation whose state exceeds RAM complete on RocksDB-backed
out-of-core storage where the in-memory engine is OOM-killed: at a 320 MB cap the same query goes **rc 137 → rc 0**
and the slice-store working set drops from the whole retained band (~78 MB) to **~4 MB** (the 1/P bound, P=8).

---

## PAGE 1 — Problem & goal
- **Context:** NebulaStream (NES) is a push-based stream processor. Windowed aggregation (e.g. `GROUP BY key
  WINDOW TUMBLING`) keeps per-window hash-map state in RAM. For long windows / many keys (IoT smart-city sensor
  streams) this state can exceed memory → the engine is OOM-killed.
- **Goal (proposal §3.3 gate):** process tumbling-window state **larger than physical memory without OOM**, by
  spilling cold state to disk (RocksDB) and bringing it back to emit results.
- **Research questions:** RQ1 integrate an OOC backend into the push model; RQ2 throughput + tail latency (p95/p99)
  of a research-informed RocksDB config; RQ3 how CPU/disk/storage scale as state grows (write amplification,
  footprint).
- **Scope LOCKED (signed proposal DIMA V2.4 §2.3.2):** tumbling windows only; NO sliding windows; NO
  variable-sized serialization; NO Nautilus JIT / codegen edits. Everything is host-side.
- Diagram idea (well-spaced): source → [slice store: per-window hash maps in RAM] → trigger → probe → sink, with a
  red "state > RAM ⇒ OOM" callout on the slice store.

## PAGE 2 — The system we built
- **SpillConfig** knobs: `enabled`, `soft/hard_threshold_mb`, `write_buffer_size_mb`, `block_cache_size_mb`,
  `emit_lag`, `number_of_partitions`, `rocksdb_path`.
- **SpillBackend** interface with 3 impls: Null, InMemory, **RocksDB** (durable, lz4, BE key
  `<sliceEnd><worker><partition>`).
- **SliceSerializer:** ChainedHashMap ⇄ flat byte record (drop next-pointers, rebuild chains on read). Fixed-size
  entries only.
- **SpillableAggregationSlice / SpillableTimeBasedSliceStore:** subclass the default store; own a SpillBackend;
  a **background spiller jthread** evicts cold slices on memory pressure (soft threshold → preemptive background
  spill; hard threshold → synchronous mandatory spill). Async-probe-drain invariant: never spill state already
  handed to the async probe (raw pointers in flight).
- Small code snippet: the SpillConfig fields (well-spaced).
- Diagram idea: worker threads → slice store; a side "spiller thread" moving cold slices to RocksDB; thresholds
  on a memory gauge.

## PAGE 3 — What happened: three discoveries (the iterative story)
This is the heart of "what happened" — each fix exposed the next real bottleneck.
1. **Discovery 1 — "cold ≡ emitted" (E0 smoke).** First real query spilled **0 bytes**. Root cause: in tumbling
   windows a slice becomes *cold* and *emitted* in the same step, so the {cold ∧ not-emitted} spill set is always
   empty. *Fix:* **emit-decouple** — a lagged emit watermark (`emit_lag`) retains a band of completed-but-unemitted
   windows, which ARE spillable.
2. **Discovery 2 — terminal-flush re-materialization (E0-REDO).** Now a real query spills (45 MB), but spill-ON and
   spill-OFF still die identically at tight caps. Root cause: at end-of-stream the retained band must
   **re-materialize into RAM buffers in one burst to emit** — regardless of where it was stored. *Config tuning
   cannot win.* First clean measurement: process peak 417 MB while the store itself was only ~20 MB — a ~400 MB
   delta dominated by the terminal flush. This reframed the whole problem.
3. **Discovery 3 — bound the burst (Phases 1–2).** Phase 1: pace the flush (emit one window at a time, free before
   next). Phase 2: **partition-at-spill** so only 1/P of each window is resident at any moment (next page).
- Diagram idea: a timeline/3-step ladder, each step a box with "fix → next bottleneck revealed", arrows down,
  generous vertical spacing.

## PAGE 4 — Partition-at-spill (the contribution) + headline result
- **Idea (textbook grace-hash / external aggregation — Shapiro 1986, Graefe 1993; used by Postgres/DuckDB/Spark):**
  at spill time, split a worker's window hash-map by `hash(key) % P` into **P self-contained blobs** keyed
  `(sliceEnd, worker, partition)`. At terminal emit, **stream one partition back whole at a time** → read
  amplification 1×, resident memory **1/P** of the window.
- **REVISED L3 (an audit caught this):** emit only the **non-empty** partitions, contiguously renumbered — an empty
  chunk has a null final map and the un-editable Nautilus probe would dereference it and crash. Skip-empty keeps
  every emitted chunk valid; the ChunkCollector still completes.
- **Correctness:** P=1 is byte-identical to the in-memory engine (fast path); a differential test shows P=1 vs P=8
  produce **byte-identical** output (50 020 rows) — partitions are key-disjoint and complete.
- **Headline result (320 MB cap, 4 threads):** P=1 → **rc 137 (OOM)**; P=8 → **rc 0**, store working set **78 → 4 MB**,
  19 slices / 77.8 MB spilled and streamed back. *(embed fig1_oom_survival.png + fig2_residency.png)*
- Code snippet: the partition split (conceptual `hash % P`), well-spaced.

## PAGE 5 — Evaluation & status
- **E1 instrumentation (this milestone):** production-visible, grep-parseable metrics at query stop:
  spill/restore counts+bytes+latency (incl. the partitioned read-back), **RocksDB write-amp + on-disk footprint**,
  and **per-task p50/p95/p99**. +18 unit tests; full suite **893/893**.
- **E3 sweep + E4 figures:** *(embed fig3_throughput, fig4_latency, fig5_writeamp_footprint, fig6_spill_restore)*
  - RQ1/§3.3: OOM-survival matrix — baseline dies where OOC lives.
  - RQ2: throughput overhead vs uncapped in-memory baseline; per-task tail latency.
  - RQ3: write amplification (~1.0, flush-dominated at this scale) + SST footprint scaling; spill/restore volume.
  - One concrete point (smoke): emit_lag 2M @320 MB P=8 → 134 s, store_peak 4 MB, spilled 19 / restored 45.6 MB,
    write-amp 1.00, SST 16 MB, p50 1 µs / p95 1.27 ms / p99 1.50 ms.
- **Honesty caveats (from E1-MEASUREMENT-CAVEATS.md):** latency is per-task not per-window; percentiles unreliable
  for small n; RocksDB Statistics adds a few % Put overhead (constant across cells); restore latency is an
  end-to-end read-back proxy; throughput includes query startup; process RSS is pool-dominated (store_peak is the
  metric partitioning controls).
- **Status:** Phases 0–2 + E1 instrumentation + E2 harness + E3 data + E4 figures complete; everything is in place
  for the thesis write-up. Engineering is committed on `feat/ooc-phase1-spill-skeleton` (not pushed — manual review).
- 2e (partition-granularity backpressure) specified but not built — its trigger was not met (P=8 already rc 0).
