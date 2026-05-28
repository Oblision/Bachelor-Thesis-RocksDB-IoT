# Phase 2 (L2⊕L3 partitioned streaming emit) — FEASIBILITY VERDICT

Inputs: `.planning/dev-eng-cpp-phase2/exploration/{MULTICHUNK,BACKEND-STREAM}.md` (read for line-level evidence).

## Verdict: FEASIBLE host-only on correctness; the PROCESS-peak bound additionally needs pacing or threads>1.

### Gate 1 — multi-chunk emit correctness: ✅ CLEARED (host-only)
- Probe (`AggregationProbePhysicalOperator::open`) is PER-BUFFER: combines its hashMaps[] into a private finalHashMap,
  lowers each key once, frees finalHashMap. Key-disjoint chunks ⇒ each key emitted exactly once. NO JIT edit.
- ChunkCollector/sequencing already supports N chunks per sequence (chunkNumber=INITIAL+p, lastChunk on the last).
  PRECEDENT: `StreamJoinOperatorHandler::triggerSlices` emits N² chunks/sequence in production today.
- GC/output-watermark gated until lastChunk ⇒ slices not freed mid-emit; emittedKeys protection holds ⇒ no UAF.
- Sinks: File appends per buffer, Void discards, Network propagates chunk metadata. All fine.
- Per-chunk the handler sets: same sequenceNumber; chunkNumber=INITIAL+p; lastChunk=(p==P-1); watermark=windowStart;
  numberOfTuples = THIS partition's count.
- MANDATORY precondition: partition by hash%P host-side (post-hoc split; insert-time routing is JIT = forbidden).

### Gate 2 — partitioned readback: ✅ CLEARED via "partition-at-read" (Option a, lowest risk)
- Current blob = one per (sliceEnd, worker): [40B header][hash|payload]* fixed stride. unspill rebuilds ALL entries.
- New `SliceSerializer::partitionedDeserialize(blob, provider, P, p)` = deserialize but skip entries where
  hash%P != p (no pool page for skipped). Transient heap = one worker's blob (small, freed after scan); buffer-pool
  rented = one partition's pages only.
- ChainedHashMap dtor (clear→storageSpace.clear) returns pages to the pool immediately.
- NO on-disk format change, NO SpillBackend interface change, NO backend reimpl. Blast radius ~30+25+20 lines.
- Deferred: Option b (partition-at-spill, re-key per partition — higher blast radius, write-amp ×P) and Option c
  (pinned-view streaming get — Phase 3 refinement). Not needed for the peak bound.

### THE NUANCE (load-bearing, from Phase-1 evidence)
"Destroy-before-next-build bounds peak" (BACKEND-STREAM §6) holds ONLY for SYNCHRONOUS emit. Emit is ASYNC
(emitBuffer queues; probe drains later). So Phase 2 bounds the BUILD-side peak (the whole window is never resident —
read partition-by-partition from RocksDB) but the IN-FLIGHT emitted set can still pile up to ≈ the whole band if the
terminate loop outruns the probe — the SAME failure mode that made Phase 1 neutral (store_peak stayed 78MB, OOM at 320
even threads=4). Phase 2 mitigates this two ways, NOT eliminates it: (1) in-flight units are 1/P smaller; (2)
production is RocksDB-I/O-gated (each partition build = a get()), so at threads>1 the fast probe is far more likely to
keep up. A HARD bound still needs light backpressure — but now at partition granularity (cheap) and Phase 1 already
laid the per-window ensure-resident seam to hang it on. Plan: build Phase 2, MEASURE the 320 cell at threads>1; if the
in-flight pileup persists, add partition-granularity pacing (bounded in-flight chunks) as 2e.

## Build sequence (each its own /dev-eng-orchestra-cpp run; regression ≥ 835)
- **2a** `SliceSerializer::partitionedDeserialize` + unit tests (filter correctness: union of P partitions == full
  deserialize; disjoint; empty partitions ok). Pure, isolated, lowest risk — do first.
- **2b** `SpillableAggregationSlice::streamEmitByPartition(backend, provider, P, callback)` — per partition p, per
  worker: get→partitionedDeserialize→hand the per-worker partition map to the callback; the slice stays spilled.
  Tests: per-partition residency/contents; pages reclaimed between partitions.
- **2c** EmittedAggregationWindow OWNS the per-worker partition maps (new `vector<unique_ptr<HashMap>>` + hashMaps[]
  point into it), freed in the HOST cleanup lambda the probe invokes at open() end (host code, no JIT edit). Resolves
  the ownership wrinkle (partition maps are not slice-owned). numberOfHashMaps = W (per-worker partition maps); probe
  combines across workers as today.
- **2d** new partitioned terminal-emit path in the store/handler: for each window, for p=0..P-1 build the W per-worker
  partition maps → emit as chunk p (chunkNumber=INITIAL+p, lastChunk=(p==P-1), same seq#, watermark=windowStart) →
  release. Add `number_of_partitions` (P) to SpillConfig (default 1 = today's whole-window emit, byte-identical).
- **2e (conditional)** partition-granularity backpressure if 2d's e2e still OOMs (bound in-flight chunks).
- Validate after 2d: `run-rss.sh F20_on320 320 on 16000 4 8 2000000 4 0 0` with P>1 → target rc 137→0, store_peak
  ≪ 78MB. Also a differential correctness run (P=1 vs P=8 identical output) — the key-disjoint dup-row guard.

## Key risks to watch
- Key-disjoint correctness (dup rows) — the differential P=1 vs P>1 test is the gate.
- Partition-map ownership/lifetime across the async probe drain (2c) — must free after drain, not before.
- Skew: hash%P may produce uneven partitions; "bounded" partition may be large under skew. Acceptable for the demo;
  note in thesis.
- The async in-flight bound (the nuance above) — measure, add 2e only if needed.
