# E1 — Evaluation Instrumentation — DESIGN BRIEF (authority doc)

Phase: EVAL-PLAN Phase E1 (instrumentation). Run via `/dev-eng-orchestra-cpp`.
Branch: `feat/ooc-phase1-spill-skeleton` (base for E1 = `ad5d2fbc8d`, Phase 2 2d HEAD).
Build/test ONLY in Docker `nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug`.
Regression baseline: **875/875** unit tests under `ctest -j8 -LE 'Systest|DockerCompose' --timeout 200`.

## Goal
Make the proposal's RQ2/RQ3 metrics measurable from the eval harness, as **production-visible,
CSV-parseable** output, WITHOUT perturbing the freshly-landed concurrency/partition store.

E1 was scoped (see `.planning/eval-phase/E1-EXPLORE.md` reasoning, captured below) and is **3 code PRs +
1 no-code confirm**, NOT the 4-from-scratch the original EVAL-PLAN.md §E1 implies — Phase 0a/0b pre-built
most of PR1/PR2.

## Prior-art already landed (DO NOT rebuild — extend)
- **Phase 0b (`08a107ca38`) — `SpillMetrics`** in `SpillableTimeBasedSliceStore.hpp` (~:220). Fields:
  slicesSpilled, slicesUnspilled, bytesSpilled, bytesRestored, peakResidentBytes,
  spill/restoreLatency{P50,P95,P99,Max}Us, spill/restoreSampleCount. Public `getMetrics()`.
  `recordSpillSample()`/`recordUnspillSample()` called at all spill/unspill sites (incl. P>1 spill at
  `cpp:~2509`), **timed OUTSIDE evictionMutex**. `logMetrics()` emits ONE `NES_INFO` line
  `"Spill metrics: spilled {} slices / {} bytes, restored ... ; spill latency us p50=.. p95=.. p99=.. max=.. (n=..); restore latency us ..."`
  at `stopSpiller()`. Harness already greps it (`run-rss.sh:~80`).
- **Phase 0a (`9b5894f71e`)** — `SpillConfig.writeBufferSizeMB` + `blockCacheSizeMB` wired into RocksDB
  Options in `RocksDBSpillBackend.cpp:~115-136` (write_buffer_size; NewLRUCache block_cache). Default 0 = RocksDB default.
- **Phase 2 (2a–2d)** — partition-at-spill. `spill()/unspill()` take `numberOfPartitions`. Terminal emit on
  P>1 streams partitions back via `SpillableAggregationSlice::streamEmitByPartition` (const; reads blobs;
  **leaves slice spilled — does NOT call unspill()**).

## Scope — the 3 code PRs + 1 confirm

### E1-PR1 — close the partitioned-restore instrumentation gap (RQ2 cost / RQ3)
**Problem:** the P>1 terminal streaming read-back (`streamEmitByPartition` / the store's `streamWindowPartition`)
reads partition blobs from the backend but records **no restore sample** → on the *headline partitioned path*
`bytesRestored` and restore-latency report **zero**, making the instrumentation misleading exactly where the
thesis demo lives.
**Required:** record a restore sample (bytes read back + latency) for the partitioned streaming read path,
consistent with the existing `recordUnspillSample()` discipline (time the I/O, OUTSIDE any store lock; do not
change eviction-mutex hold time). Bytes = the partition blob bytes actually read from the backend.
Also: confirm/lock the `logMetrics()` line format is stable and document the field grammar for the harness.
**Non-goal:** changing emit semantics or making streamEmitByPartition unspill-then-reside (it must stay
streaming/1-partition-resident — that is the whole point of Phase 2).

### E1-PR2 — RocksDB internal statistics: write-amp + on-disk footprint (RQ3)
Config surface is DONE (0a). **Add** RocksDB statistics:
- Enable `rocksdb::Statistics` on the Options (or use `DB::GetProperty`), at the single Options/DB construction
  site in `RocksDBSpillBackend.cpp`.
- Expose, via a new accessor on `RocksDBSpillBackend` (e.g. `RocksDBMetrics getDbMetrics() const`):
  - **write amplification** — derive from the stats the microbench's `parse_write_amp` formula uses
    (bytes written to SST / bytes flushed from memtable; or COMPACT_WRITE_BYTES+FLUSH_WRITE_BYTES vs
    user bytes — pick what RocksDB exposes cleanly and document the exact formula).
  - **on-disk footprint** — `rocksdb.total-sst-files-size` (and/or `rocksdb.live-sst-files-size`).
  - (optional, cheap) block-cache hit/miss if available — RQ3 nicety, not required.
- Surface these at query stop as a **CSV-parseable** `NES_INFO` line (extend `logMetrics()` or a sibling
  `RocksDB stats:` one-liner). The store reaches the backend it already owns.
**Non-goal:** any config search; the config is fixed (lz4 / wbuf 32MB / cache 32MB per microbench).

### E1-PR3 — per-task tail latency p50/p95/p99 (RQ2)
Infra exists (`QueryEngineStatisticListener` interface; `TaskExecutionStart`/`TaskExecutionComplete` events;
composite-listener registration used by the single-node worker / systest).
**Required:** a new listener (e.g. `PercentileTaskLatencyListener`) that:
- pairs `TaskExecutionStart`→`TaskExecutionComplete` per `(pipelineId,taskId)`, accumulates per-task durations,
- computes p50/p95/p99 (+ max, n) at query stop,
- emits a **CSV-parseable** `NES_INFO` one-liner (same grammar family as `Spill metrics:`),
- registers through the existing composite/listener path so a normal systest run collects it (no JIT, no engine
  hot-path edit beyond consuming events that already fire).
**Caveat to DOCUMENT (honesty):** unit is **per-task**, not per-window — the engine only exposes per-task events;
per-window would need an extra hook. State this in the metric's log line / comment.
**Thread-safety:** the listener is called from engine threads — guard its accumulator (it is off the spill
eviction path; keep it lock-light, e.g. per-thread buffers merged at stop, or a single small mutex — must not
add contention to task execution).

### E1-PR4 — Generator throughput (NO CODE)
Confirm the generated tuple count is externally known (Generator config / workload row count) so throughput =
known_tuples / measured_elapsed is computed in the harness. Document the source; only add a counter if E0/E3
shows the external number is unreliable. Default: no code.

## LOCKED constraints (hard — reviewers MUST NOT flag these as bugs; fix-agents MUST NOT violate)
1. **NO Nautilus/JIT/codegen edits.** (proposal §2.3.2). Instrumentation is host-side only.
2. **NO sliding windows; NO variable-sized serialization.** Tumbling, fixed-size only.
3. **Build & test ONLY in Docker** `nebulastream/nes-development:local-rocksdb` (`/workspace` mount,
   `cmake-build-debug`). `ninja` and `ctest` run **UNPIPED under `set -e`** (a `| tail`/`| head` masks failures).
4. **Regression must stay ≥ 875** unit tests (`-LE 'Systest|DockerCompose'`). New tests add to it.
5. **P=1 byte-identical** behavior preserved. Instrumentation must be observation-only — zero change to spill/
   emit/aggregation results at any P.
6. **Do NOT perturb the eviction-mutex hold time.** All new timing/counter work for spill/restore stays OUTSIDE
   the eviction lock, consistent with the no-I/O-under-lock invariant. PR3's listener must not add task-path contention.
7. **Production-visible (survive Release):** metrics emitted via `NES_INFO` at query stop and/or a public
   accessor — NOT test-only (`*ForTest`). Output must be a **stable, grep-parseable one-liner** matching the
   existing `Spill metrics:` pattern so `run-rss.sh`/`run-rss-part.sh` can extend their TSV.
8. **Manual push.** Commit per PR; do NOT push. No co-author trailer.
9. `.planning/` is gitignored — planning artifacts are not committed.

## Output contract (the harness target)
Every new metric appears at query stop as a single `NES_INFO` line with `key=value` (or fixed-order) fields,
prefixed by a stable tag (`Spill metrics:`, `RocksDB stats:`, `Task latency us:`), so a one-line `grep | sed`
in the existing shell harness extracts it. PR-by-PR, extend `run-rss-part.sh`'s TSV columns to include the new
fields (harness edit lives under `.planning/eval-phase/`, not the NES repo — not committed).

## PR boundaries (for the review pass)
PR1 = `nes-physical-operators` slice/store (restore sampling on partitioned read).
PR2 = `nes-physical-operators` RocksDBSpillBackend (+ store surfacing) + maybe `SpillConfig` (no — config done).
PR3 = `nes-query-engine` / single-node-worker (new listener + registration).
PR1↔PR2 both touch the store's `logMetrics`; sequence PR1 then PR2 to avoid trampling, or assign one owner to
`logMetrics`. PR3 is independent (different module) → parallelizable with PR1/PR2.

## Open items for the architects
- O1: exact RocksDB stat keys/formula for write-amp on the installed RocksDB version (verify `GetProperty`
  property names exist; `rocksdb.total-sst-files-size`, `rocksdb.compaction-stats`/`rocksdb.stats`).
- O2: where the partition-blob byte count is cheapest to capture for PR1 (in `streamEmitByPartition`'s read
  loop return, vs the store wrapper) — keep the slice method's signature change minimal.
- O3: PR3 accumulator design (per-thread vs single mutex) given the engine threading model.
