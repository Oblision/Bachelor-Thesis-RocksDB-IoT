# E1 measurement caveats (carry into thesis §eval + the summary — honesty)

These are real interpretation limits of the E1 instrumentation, surfaced by code review. State them where the
corresponding number is reported.

1. **Per-task, not per-window latency (PR3).** The engine exposes per-task start/complete events only; p50/p95/p99
   are over per-task execution durations, not per-window emit latency. The emitted line is suffixed `(per-task)`.
   Per-window would need an extra engine hook (out of scope: no JIT/engine-hot-path edits).
2. **Small-n percentiles (PR3).** Nearest-rank floor convention; for n < ~20, p95/p99 collapse toward the max/min and
   are unreliable. Report n alongside the percentiles; only trust tails when n is large.
3. **RocksDB Statistics overhead (PR2).** `rocksdb::CreateDBStatistics()` at `kExceptDetailedTimers` increments
   atomics on every Put/Get (~few % Put-path overhead). E1 OOC runs are therefore NOT directly comparable to the
   earlier 10k microbench runs (which had no Statistics object). Within E1 the overhead is constant across cells, so
   OOC-vs-OOC and OOC-vs-baseline comparisons stay valid. Note it where write-amp/footprint are reported.
4. **Restore-latency proxy (PR1).** The partitioned restore sample times `streamEmitByPartition` end-to-end =
   backend read + `SliceSerializer::deserialize` + emit-callback push — a "read-back cost proxy", not pure disk I/O.
   Describe it as such.
5. **bytesRestored unit mix (PR1).** On the full-unspill path bytesRestored accumulates deserialized in-memory bytes;
   on the P>1 streaming path it accumulates serialized blob bytes. Pure-P>1 thesis runs are internally consistent;
   if a run mixes both paths, the total mixes units. The new `partition_restores=` field counts streaming reads
   separately from `slicesUnspilled` (full unspills), so the two regimes are distinguishable in the log line.
6. **Throughput (PR4, external).** throughput = known generated tuples / wall-elapsed of the systest invocation;
   elapsed includes query compile + startup, a constant offset. Use for relative OOC-vs-baseline comparison, not as
   an absolute engine-throughput claim.
7. **Process RSS vs store working set.** The capped cgroup `memory.peak` is dominated by the fixed buffer pool
   (16000 × 8192 B ≈ 131 MB) + RocksDB + runtime; `store_peak` (the Spill-metrics `peak resident`) is the metric
   partitioning controls and is bounded ~1/P. Report both; attribute the bound to `store_peak`.
