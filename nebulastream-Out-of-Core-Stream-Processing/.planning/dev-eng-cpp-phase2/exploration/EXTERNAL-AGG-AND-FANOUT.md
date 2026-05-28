# External / Out-of-Core Aggregation, Partition Fan-out, and Streaming State Spilling

Research dossier for the **partition-at-spill** design decision in the thesis
*"Out-of-Core Stream Processing in NebulaStream"*.

Scope (explicitly NON-overlapping with the other two explorers):

- **Axis A** — Foundational external/grace/hybrid-hash aggregation prior art (for related-work + justification).
- **Axis B** — How to choose the partition fan-out *P*, recursion, and skew (actionable design guidance).
- **Axis C** — Production state-backend spilling for large **window** state (streaming-specific prior art).

This dossier does **not** cover push-based backpressure / flow-control, nor the NES codebase internals (covered by other explorers).

> **Notation used throughout** (Graefe's convention): *M* = memory budget (pages), *R* = input size (pages),
> *C* = cluster / unit-of-I/O size (pages), *F* = fan-out (number of partitions), *L* = number of recursion levels.
> We use *P* for the fan-out we choose in our design (= Graefe's *F*).

---

## Axis A — External / out-of-core aggregation: foundational prior art

### A.1 The canonical algorithm (grace / hybrid hash, partition-at-a-time)

The textbook technique for processing a hash-based set operation whose hash table does not fit in
memory is **partition the input so each piece fits in memory, then process one partition at a time**.
This is identical for hash *join* and for hash *aggregation* (GROUP BY / duplicate removal), because
the partitioning step only needs the property that *"if two groups have a different hash value, they
cannot possibly be the same"* (DuckDB blog, [S9]) — so any group/row can be routed to exactly one
partition by `hash % P`, and each partition can then be finished independently.

Goetz Graefe's survey *Query Evaluation Techniques for Large Databases* (1993) is the canonical
reference and states the algorithm precisely ([S2]):

> "There are basically two methods for managing hash table overflow, namely avoidance and
> resolution. In either case, the input is divided into multiple partition files such that partitions
> can be processed independently from one another and the concatenation of the results of all
> partitions is the result of the entire operation. Partitioning should ensure that the partitioning
> files are of roughly even size, and can be done using either hash-partitioning or
> range-partitioning … Usually, partition files can be processed using the original hash-based
> algorithm." — Graefe 1993, §3.2

This is exactly **partition-at-spill**: at overflow time, split the build (the hash map of group→
accumulator) by `hash % P` into *P* independently processable on-disk partitions; at emit time read
one partition at a time, rebuild a small in-memory hash table for that partition, finalize, and emit.

**Why it bounds memory.** If the partitioning function is roughly uniform, each partition is `R/P`,
so memory use during the *process* phase is one partition plus the output buffer — independent of
total input *R*. Graefe formalises the multi-level case ([S2], §3.2): after *L* partitioning levels
each partition is `R' = R / F^L`, the size *shrinks by a factor equal to the fan-out per level*, and
*"the recursion terminates when the files fit into memory."* Hence peak resident state is *O(M)*, not
*O(R)* — this is the whole point of the technique and the justification for partition-at-spill.

**Hybrid vs. grace.** Graefe distinguishes (citing DeWitt et al. 1984 and **Shapiro 1986**, [S1]):

- **Grace hash** ([Kitsuregawa et al. via S2]) partitions the *entire* input to disk first, then
  processes partitions — pure overflow *avoidance*.
- **Hybrid hash** (Shapiro 1986, [S1]) keeps the first partition's hash table resident and only
  spills the other *F−1* partitions: *"hybrid hash algorithms use all available memory for in-memory
  processing, but at the same time are able to process large input files by overflow resolution."*
  ([S2]). Hybrid is the established default in real engines because it pays zero spill cost when the
  input *almost* fits (useful when *M < R ≤ F × M*).

For a *streaming slice store* the build is constructed incrementally as events arrive, so the natural
analogue is **grace-style partition-at-spill** (split the already-built map at spill time), optionally
with a hybrid optimisation (keep one partition resident). The thesis can cite Shapiro 1986 and
Graefe 1993 as the foundational basis and frame partition-at-spill as the streaming adaptation.

**Original sources & the partitioning skew lineage.** Graefe attributes overflow handling and
bucket-tuning under skew to the Tokyo (Kitsuregawa/Nakayama/Takagi) line of work ([S2]):

> "If it turns out that fewer partitions than have been created would have been sufficient … bucket
> tuning (collapsing multiple small buckets into larger ones) and dynamic destaging (determining which
> buckets should stay in memory) can improve the performance of hash-based operations
> [Kitsuregawa, Nakayama, and Takagi 1989; Nakayama, Kitsuregawa, and Takagi 1988]."

(See Axis B for the skew specifics: Nakayama/Kitsuregawa/Takagi 1988 dynamic destaging [S10];
Kitsuregawa/Nakayama 1989 bucket-size tuning [S11]; Kitsuregawa/Ogawa bucket-spreading parallel hash [S12].)

### A.2 How modern analytical engines do external/spilling GROUP BY

Every mature analytical engine implements partition-at-spill for GROUP BY. Summary table, then per-engine detail.

| Engine | Partitioning method | Partition-count choice | Memory bound |
|---|---|---|---|
| **PostgreSQL ≥13** | hash bits of grouping key → temp tape files; recursive on `used_bits` | `~1.5 × n_groups × entrysize / hash_mem`, clamped **[4, 1024]**, rounded to power of two | each spilled partition's hash table ≤ `hash_mem` |
| **DuckDB ≥0.9** | radix bits (middle bits of hash); **over-partitioning** (≫ #threads); buffer-manager spills *pages* | radix bits chosen from #threads and whether intermediates > memory; finer bits if a partition still > memory | one partition per thread fits in memory; "RAM-oblivious" phase 1 |
| **Spark SQL** | shuffle/exchange by `hash(key) % numPartitions`; `ExternalAppendOnlyMap`/`UnsafeExternalSorter` spill within a partition | `spark.sql.shuffle.partitions` (default **200**); AQE coalesces/splits at runtime | `spark.memory.fraction` per task; spill sorted runs merged |
| **Trino / Presto** | per-operator spill of accumulated aggregation state; join build partitioned by `task.concurrency` | partition count = `task.concurrency`; aggregation spills accumulator chunks | revocable memory revoked → spill → merge with lower footprint |
| **Hive / Tez** | grace-hash join + map-side hash agg flush; hybrid-hybrid grace hash join | configured number of partitions; can recurse | spill partitions when build exceeds memory |

**PostgreSQL (v13 disk-based hash aggregation, Jeff Davis).** Before v13, hashagg could exceed
`work_mem` and OOM; v13 made it spillable ([S4], [S5]). When the in-memory hash table exceeds the
memory limit it enters *"spill mode"*: existing groups keep aggregating, but any tuple that *would
create a new group* is written to a partition chosen by hash bits; after the input is consumed, each
spilled partition becomes a new batch and is reprocessed, *possibly spilling recursively* ([S4]).
The partition count is computed in `hash_choose_num_partitions` ([S6], source):

```c
/* nodeAgg.c */
#define HASHAGG_PARTITION_FACTOR 1.50
#define HASHAGG_MIN_PARTITIONS   4
#define HASHAGG_MAX_PARTITIONS   1024

partition_limit = (hash_mem_limit * 0.25 - HASHAGG_READ_BUFFER_SIZE) / HASHAGG_WRITE_BUFFER_SIZE;
mem_wanted      = HASHAGG_PARTITION_FACTOR * input_groups * hashentrysize;
dpartitions     = 1 + (mem_wanted / hash_mem_limit);
/* ... clamp to [MIN, MAX], cap by partition_limit, round to power of two ... */
```

So Postgres picks **enough partitions that each partition's hash table is expected to fit in
`hash_mem`** (`mem_wanted / hash_mem`), inflated 1.5× so it rarely needs a second recursive pass, and
*capped* so that the per-partition write buffers don't consume more than ~25% of `hash_mem`. The
in-source comment gives the exact trade-off ([S6]): the factor is `> 1` because *"there's not a high
cost to having a few too many partitions, and it makes it less likely that a partition will need to be
spilled recursively,"* while the cap exists because *"too many will result in lots of memory wasted
buffering the spill files (which could instead be spent on a larger hash table)."* This is the single
most directly transferable heuristic for our design (see Axis B).

**DuckDB (Kuiper, Boncz, Mühleisen, ICDE 2024 — "Robust External Hash Aggregation in the Solid State
Age", [S7]).** This is the most relevant primary source for us: a peer-reviewed paper specifically
about making external hash aggregation *robust* (no "performance cliff") on SSDs. Key design ([S7]):

- Two-phase, morsel-driven: **phase 1** thread-local pre-aggregation into a small fixed-size
  (`2^17 = 131,072`-entry) hash table, then **radix-partition** tuples into a *spillable page layout*;
  **phase 2** partition-wise parallel aggregation, scanning each finished partition straight into the
  next pipeline.
- Partitions are determined by *"a few of the middle bits of the hash that were not yet used for the
  salt (upper bits) or for the offset into the hash table (lower bits)"* — and *"the number of bits
  used to partition depends on the number of active threads and whether the size of the intermediates
  is larger than memory."* ([S7])
- **Over-partitioning** is the central trick: *"This can be achieved by over-partitioning, i.e.,
  creating many more partitions than threads. This keeps the memory pressure low during the second
  phase."* ([S7]) If a partition still doesn't fit, DuckDB **increases the radix bits** to repartition
  finer until each fits ([S8], 0.9.0 release notes).
- Spilling is delegated to the **buffer manager**, which writes *individual pages* (not whole
  partitions) on demand: *"the buffer manager writes individual pages, rather than entire partitions,
  to storage when needed … our algorithm is virtually RAM-oblivious during the … phase."* ([S7]) The
  payoff is *graceful degradation* rather than an OOM cliff — exactly the robustness property the
  thesis wants. This paper is the best single citation that partition-at-spill on SSD/flash is current
  best practice, not just 1980s theory.

**Spark SQL.** GROUP BY is a shuffle (`Exchange hashpartitioning(key, n)`) followed by per-partition
aggregation; within a task, aggregation/sort spill via `ExternalAppendOnlyMap` / `UnsafeExternalSorter`
writing sorted runs that are merge-read. The partition count is `spark.sql.shuffle.partitions`
(default **200**, [S13]); under-provisioning gives huge partitions that spill and straggle, over-
provisioning gives tiny tasks with scheduling/metadata overhead ([S13]). Since 3.0, **Adaptive Query
Execution** removes most of the manual tuning by *coalescing* post-shuffle partitions toward a target
size (`advisoryPartitionSizeInBytes`, e.g. 128 MB) and *splitting* skewed partitions at runtime
([S13], [S14]). The lesson for a *fixed-stride single-pass* design: Spark needs runtime adaptivity
precisely because a single static *P* is fragile under skew — we will instead caveat the skew limit.

**Trino / Presto.** Spill is opt-in (`spill_enabled`); memory-intensive aggregations write accumulated
aggregation state to disk and *"are then loaded back and merged with a lower memory footprint,"* using
**revocable memory** that the memory manager can reclaim by forcing a spill ([S15]). Joins partition
the build by `task.concurrency` and spill a subset of partitions plus matching probe rows ([S15]).
Notably the Trino docs warn that spill is *not* a guarantee: *"the system may fail to divide
intermediate data into chunks small enough to fit in memory, leading to out-of-memory errors"* ([S15])
— i.e. under pathological skew, single-level partitioning is insufficient. Useful caveat citation.

**Hive.** Implements grace hash join and a *Hybrid Hybrid Grace Hash Join* that blends in-memory and
grace partitioning, spilling partitions when the build exceeds the memory budget ([S16]).

### A.3 The citable "textbook best practice" framing (for the thesis)

> External (out-of-core) hash aggregation by **partitioning so that each partition's hash table fits
> in memory, then processing one partition at a time** is the established technique for memory-bounded
> grouping, going back to hybrid-hash join (Shapiro 1986 [S1]) and codified in Graefe's survey (1993
> [S2]). It is the mechanism used by every mature analytical engine that supports spilling GROUP BY:
> PostgreSQL's disk-based hash aggregation ([S4]–[S6]), DuckDB's radix-partitioned external
> aggregation ([S7]), Spark SQL's shuffle + external aggregation ([S13]), and Trino's spilling
> aggregations ([S15]). This thesis adapts that established technique to a streaming tumbling-window
> *slice store*: at spill time the in-memory aggregate map is partitioned by `hash % P` into *P*
> independently emittable on-disk blobs, bounding resident state to *O(M)* regardless of window-state
> size.

---

## Axis B — Partition fan-out heuristics: choosing *P*, recursion, and skew

### B.1 How real systems choose the number of partitions *P*

There are two distinct philosophies, and they converge on the same goal — **each partition's working
set must fit in memory** — but differ in whether they target that exactly or over-shoot.

**(1) Size-driven, "make each partition fit" (Graefe / PostgreSQL).** Choose *P* so that an input of
size *R* (or `n_groups × entrysize`) divided by *P* fits in the memory budget *M*:

```
P  ≈  ceil( input_size / M )            (each partition ≈ M)
```

PostgreSQL operationalises this as `P ≈ 1.5 × n_groups × entrysize / hash_mem`, clamped to **[4, 1024]**,
rounded up to a power of two, and additionally capped so per-partition write buffers stay ≤ ~25% of
`hash_mem` ([S6]). The 1.5× headroom deliberately over-provisions a little to avoid a recursive pass.

**(2) Radix-bit, "over-partition well beyond #consumers" (DuckDB / Spark / morsel engines).** Choose a
number of radix bits *b* giving `P = 2^b` partitions, with `P ≫ #threads`, so that load is balanced
across consumers *and* each partition is small enough to fit; bump *b* if a partition is still too big
([S7], [S8]). DuckDB explicitly *over-partitions* to keep phase-2 memory pressure low ([S7]).

**Graefe's hard ceiling on a single pass.** The maximum useful fan-out in one pass is bounded by
memory divided by the I/O unit, because each open output partition needs at least one write buffer
([S2], §3.2):

> "The maximal partitioning fan-out F … is determined by the memory size M divided over the cluster
> size C minus one cluster for the partitioning input, i.e., F = ⌊M / C − 1⌋, just like the fan-in for
> sorting."

So in a single pass you cannot exceed `F_max = ⌊M/C − 1⌋` partitions without thrashing the write
buffers. This is the formal statement of the "too many P" cost.

**Concrete defaults observed in the wild** (use as anchors, not gospel):

- PostgreSQL: 4 ≤ P ≤ 1024, power of two, sized to `hash_mem`. ([S6])
- Spark: default 200 shuffle partitions; production guidance often 2000 with AQE coalescing to ~128 MB
  targets. ([S13])
- DuckDB: radix bits derived from thread count and memory; over-partition ≫ threads; recurse on bits. ([S7], [S8])

### B.2 The "too few vs. too many *P*" trade-off (decision-relevant)

| | Too few *P* | Too many *P* |
|---|---|---|
| **Memory** | a partition may still be `> M` → recursion or OOM | per-partition write/read buffers (1 per open partition) eat the budget that could be a bigger hash table (PG: cap at ~25% of `hash_mem` [S6]) |
| **I/O** | fewer, larger sequential blobs (good for flash) | many tiny blobs → random-I/O amplification, lower per-op throughput |
| **RocksDB** | fewer, larger values → low key count, cheaper compaction | key explosion → more keys per SST, more compaction churn, more read/write amplification |
| **Skew** | each partition holds many keys → one hot key can blow a partition past *M* | finer split *can* dilute skew somewhat but never eliminates per-key skew |

The PostgreSQL source comment captures both sides crisply ([S6]): too many partitions waste memory on
spill buffers *"which could instead be spent on a larger hash table"*; the 1.5× factor exists because
*"there's not a high cost to having a few too many … and it makes it less likely that a partition will
need to be spilled recursively."* Net: **err slightly high on P** (cheap insurance against a second
pass), but **bounded** so buffers don't dominate — and for a flash-backed KV store, also bounded so you
don't fragment into many tiny random-I/O blobs.

### B.3 Recursive / multi-level partitioning

When one pass leaves a partition still larger than *M* (because *R/P > M*, or because of skew),
systems **recurse**: repartition that one partition using *fresh* hash bits. Graefe ([S2], §3.2):

> "It is possible that one or more partition files are larger than memory. In that case, partitioning
> is used recursively until the file sizes have shrunk to memory size … The recursion terminates when
> the files fit into memory."

The number of full recursion levels is logarithmic in the overflow ratio ([S2]):

```
L = ceil( log_F ( R / M ) )         # partition size after L levels: R / F^L
```

PostgreSQL recurses by consuming more hash bits (`used_bits` per `HashAggBatch`, [S4]/[S6]); DuckDB
recurses by adding radix bits ([S8]). **A good initial *P* almost always avoids recursion**: if you
pick `P ≥ R/M` (with headroom, e.g. PG's 1.5×) and the hash is uniform, then `R/P ≤ M` after a single
pass and `L = 0`. Recursion only becomes necessary when (a) you badly under-estimated *R* up front, or
(b) skew concentrates one partition. For a constrained device where *R* (the window's distinct-group
count) can be estimated and where a second pass is expensive, a single, generously-sized *P* is the
right default; recursion should be a *fallback* for the skew case, not the common path.

### B.4 Skew handling (the honest limitation)

`hash % P` produces uneven partitions whenever the *key* distribution is skewed (a few hot keys carry
most of the mass). This is a classical, well-studied problem in the hash-join literature. Survey of
mitigations:

1. **Better/stronger hash function.** A good mixing hash makes the *partition* sizes uniform even when
   key *frequencies* are uneven — *unless a single key dominates*, in which case no hash can help
   (one key = one partition). Graefe notes simple hashes often suffice but performance *"depends on the
   quality of the hash function"* ([S2]).
2. **More partitions (finer split).** Dilutes moderate skew across more, smaller partitions; does
   nothing for single-hot-key skew (that key's whole mass still lands in one partition) ([S2]).
3. **Bucket-size tuning / dynamic destaging** (Nakayama, Kitsuregawa, Takagi 1988 [S10];
   Kitsuregawa & Nakayama 1989 [S11]): at split time, *dynamically* decide which buckets stay resident
   and collapse small buckets, so resident memory adapts to the realised (skewed) distribution rather
   than a static plan.
4. **Bucket-spreading parallel hash** (Kitsuregawa, Ogawa [S12]): explicitly *spreads* a hot bucket's
   tuples across multiple processing nodes to rebalance skewed load — the robust-under-skew variant.
5. **Runtime/dynamic repartitioning** (Spark AQE skew-join, [S14]): detect a partition that is
   `> skewedPartitionFactor × median` and `> threshold`, then *split* it at runtime into sub-partitions.
   This is the modern production answer, but it requires runtime statistics and a planner feedback loop.
6. **Recursive partitioning as fallback** ([S2], [S4]): if a partition overflows on emit, repartition
   it with fresh bits. Bounds memory even under skew, at the cost of extra passes.
7. **Salting / explicit hot-key isolation** (common Spark practice, [S17]): append a random salt to hot
   keys to spread them, then re-merge. Application-level, intrusive.

**Pragmatic recommendation for our constrained-device, fixed-stride, single-pass design.** A static
single-pass `hash % P` cannot guarantee `partition ≤ M` under adversarial single-key skew — the same
limitation Trino acknowledges (*"may fail to divide intermediate data into chunks small enough … leading
to out-of-memory"*, [S15]). The pragmatic stance:

- Use a **good mixing hash** on the group key (not `key % P` directly) so partition sizes are uniform
  under *frequency* skew; this is cheap and removes the most common skew source.
- Size *P* with headroom (Axis B.5) so moderate imbalance still keeps each partition ≤ *M*.
- Provide a **recursive-repartition fallback** (or a streaming/merge fallback for the worst partition)
  so a single hot partition degrades gracefully instead of OOMing — even if it is slow, it must not crash.
- **Caveat in the thesis explicitly:** the design assumes group-key *frequencies* are not pathologically
  concentrated on a single key; under extreme single-key skew a partition may still exceed the budget,
  in which case the recursive fallback (or a per-key streaming aggregate) is required. This matches how
  even production engines (Spark, Trino, Postgres) treat skew as a runtime-adaptive concern rather than
  a single static *P* — and adaptive repartitioning is named as future work, not in scope for the
  constrained single-pass prototype.

### B.5 Constrained / edge-device angle (memory- AND I/O-constrained, RocksDB-specific)

Edge/IoT targets add a second constraint beyond RAM: **small, slow flash with limited IOPS** and a
write-endurance budget. This shifts the *P* trade-off toward **fewer, larger blobs**:

- **Favour sequential, batched I/O over random small I/O.** Many tiny partition blobs turn the emit
  phase into a random-read storm; fewer, larger partitions keep reads/writes sequential, which is
  dramatically faster and gentler on flash. The DuckDB paper's whole thesis ("…in the Solid State
  Age", [S7]) is that page-granular, buffer-manager-mediated spilling degrades *gracefully* on SSD;
  the corollary on weaker flash is to keep the spill unit reasonably large.
- **RocksDB key-granularity is the dominant knob.** RocksDB tunes a three-way trade-off between
  **write**, **read**, and **space amplification**, and compaction is what shifts the balance ([S18],
  [S19]). Implications for partition-at-spill:
  - **Many small keys** (e.g. one RocksDB key per group, or per (partition, group)) → large key count
    → more entries per SST, more comparisons, more compaction churn, **higher write & read
    amplification** on a device where every extra write costs endurance and IOPS.
  - **Fewer larger blobs** (e.g. one RocksDB key/value per *partition*, value = the serialized
    partition payload) → far fewer keys, near-sequential write of large values, **lower write
    amplification**, fewer compaction passes. On constrained flash this is strongly preferable.
  - Leveled compaction has ~T× higher write amplification than tiered but ~T× lower read amplification
    ([S19], [S20]); a write-once-read-once spill workload (write partition at spill, read once at emit)
    benefits from *minimising rewrites* — i.e., few large values and a compaction style that does not
    repeatedly rewrite the spilled data. Consider that spilled-partition data is *transient* (deleted
    after the window fires), so minimising compaction over it is pure win.
  - RocksDB-specific: per the tuning guide, large values can be kept out of the LSM via **BlobDB / blob
    files** (key-value separation), which is essentially the "fewer larger blobs" recommendation made
    native — worth a sentence in the thesis as the RocksDB-idiomatic way to store partition payloads
    cheaply ([S18]).

**Net edge-device recommendation:** prefer **a modest *P*** (enough to bound memory with headroom, not
maximal) and **one RocksDB key per partition with a large serialized value** (or blob-separated),
rather than one key per group. This minimises key count, write amplification, compaction work, and
random I/O — the things that hurt most on small, IOPS-limited flash.

---

## Axis C — Production state-backend spilling for large WINDOW state (streaming prior art)

### C.1 Apache Flink — RocksDB keyed/window state backend

Flink's **RocksDB state backend** (`EmbeddedRocksDBStateBackend`) is the canonical example of a
streaming engine that keeps large keyed/window state on disk instead of the JVM heap ([S21], [S22]):

- **Why it exists:** *"If the state of your job is larger than can fit in local memory (e.g., long
  windows, large keyed state) … you should use RocksDBStateBackend."* It *"can support state larger
  than the total configured heap capacity,"* bounded only by available disk ([S21]). State is stored as
  **serialized bytes**, off-heap/native memory first, **flushed to local disk** past a threshold ([S21]).
- **How window state is addressed on disk (key insight for us).** Flink stores keyed state in RocksDB
  with a **composite key = serialized `<KeyGroup, Key, Namespace>`** and the value = serialized state;
  each registered state is its **own RocksDB column family** ([S23], [S25]). For *window* operators the
  **Namespace is the window** (e.g. the `TimeWindow`). This means each *(key, window)* pair is an
  *independently addressable* on-disk entry — Flink never has to materialise the whole window's state on
  the heap; it reads/writes exactly the entries it needs, and RocksDB sorts by the composite key so a
  given key's windows are clustered ([S23]). **This is the direct streaming precedent for
  partition-at-spill**: disk-resident state keyed by a window/namespace, read back per-unit at fire time.
- **How it avoids OOM at window fire.** Two mechanisms: (1) **incremental aggregation** — with
  `ReduceFunction`/`AggregateFunction`, Flink folds each arriving element into a *single* accumulator
  per `(key, window)` rather than buffering all elements, so the per-window state is *O(1)* in element
  count, not *O(#events)* ([S24]). The whole window is therefore never materialised in heap. (2) For
  window functions that *do* buffer elements (`ProcessWindowFunction` with a list), the buffer lives in
  RocksDB on disk, not heap. At fire time the trigger fetches the accumulator/elements for *that*
  `(key, window)` from RocksDB, emits, and clears it — peak heap is one window's working set, not all
  windows.
- **Incremental checkpoints:** RocksDB is *"currently the only state backend that supports incremental
  checkpointing,"* storing only deltas since the last checkpoint, which is what makes terabyte-scale
  state operationally viable ([S21], [S26]).
- **Large-window guidance:** Flink explicitly recommends RocksDB for *"long windows"* and provides
  large-state tuning docs (memory budgeting between RocksDB buffers/caches via managed memory, etc.)
  ([S21], [S22]).

**Takeaway for related work:** Flink already solves "huge window fires without heap OOM" via
disk-resident, namespace-keyed (per-window) state + incremental aggregation. Our contribution is *not*
inventing this; it is bringing an analogous **spillable, partitioned slice store to NebulaStream**,
which currently has no spillable state backend (positioning below).

### C.2 Other streaming engines with disk-spilling state

- **Kafka Streams (RocksDB stores).** Local state stores are RocksDB-backed ([S27]). **Windowed**
  stores are organised into time-based **segments** (separate RocksDB instances/column families per
  time range), so expired windows are dropped by *deleting whole segments* rather than scanning/deleting
  per key; message keys are **composite (key + window timestamp)** ([S27], [S28]). State exceeding RAM
  spills to local disk via RocksDB, and durability is via a compacted+deleted **changelog topic**
  ([S27], [S28]). The *segment-per-time-range* structure is another close analogue of partition-at-spill
  (disk-resident, independently droppable chunks keyed by window).
- **Spark Structured Streaming (RocksDB state store provider, ≥3.2).** *"Rather than keeping the state
  in the JVM memory, RocksDB efficiently manages the state in the native memory and the local disk.
  RocksDB's ability to spill data over to disk is what allows Spark Structured Streaming to handle state
  data that exceeds the available memory."* ([S29], [S30]) Used for streaming aggregation,
  `dropDuplicates`, stream-stream joins, and `flat/mapGroupsWithState`; chosen specifically to avoid GC
  pauses with millions of keys ([S29], [S30]).
- **Materialize / RisingWave / Arroyo** (lighter touch, secondary): RisingWave and Arroyo target
  disaggregated/SQL streaming with state offloaded to object storage / embedded KV stores; RisingWave
  uses a Hummock LSM state store on object storage, and the broader pattern (state too big for RAM →
  LSM/KV on disk or object store) is now standard across modern streaming SQL engines. (Cite as
  industry-trend support; primary docs vary by version.)

### C.3 Positioning our work (what they do vs. what motivates this thesis)

- **What they do:** Flink, Kafka Streams, and Spark Structured Streaming all keep large
  keyed/windowed state **on disk via RocksDB**, address each window independently (Flink's
  `<KeyGroup,Key,Namespace>`; Kafka's per-segment composite keys), use **incremental aggregation** so a
  window's state is *O(1)* in event count, and fire windows by reading back only the relevant
  per-window entries — never materialising all window state on the heap ([S21]–[S30]).
- **What motivates this thesis:** **NebulaStream has no spillable state backend** — windowed-aggregation
  state currently lives in memory and a window larger than RAM causes OOM. This thesis brings the
  established disk-spilling-state idea (partition-at-spill, grace/hybrid-hash style, Axis A) into NES's
  slice-store, targeting **constrained/edge devices** where the existing engines' assumptions
  (server-class RAM, fast NVMe, runtime-adaptive repartitioning) do *not* hold. The novelty is the
  *combination*: external-aggregation partition-at-spill, adapted to a streaming tumbling-window slice
  store, tuned for memory- and IOPS-constrained hardware — a niche the production backends above do not
  specifically target.

---

## Recommendations for our partition-at-spill design

### R.1 A defensible default for *P* and how to pick it

Adopt the **PostgreSQL-style, size-driven** rule (it is the most directly citable and the most
transferable to a single-pass design):

```
P = clamp(  ceil( HEADROOM × est_state_bytes / mem_budget ),  P_min,  P_max )
    rounded up to a power of two
```

with concrete, defensible parameter choices:

- **`HEADROOM = 1.5`** — exactly PostgreSQL's `HASHAGG_PARTITION_FACTOR`; cheap insurance against a
  second (recursive) pass under mild imbalance, with the explicit Postgres rationale to cite ([S6]).
- **`est_state_bytes`** — estimated size of the built aggregate map for the window (distinct groups ×
  per-group accumulator+key size). If unknown up front, measure at spill time (the map already exists
  when we decide to spill) — this is *better* than Postgres, which must estimate before building.
- **`mem_budget`** — the resident memory we allow one partition's rebuilt hash table to occupy on emit.
- **`P_min = 4`, `P_max`** — `P_min = 4` per Postgres; cap `P_max` driven by *two* limits: (a) Graefe's
  buffer ceiling `F_max = ⌊M/C − 1⌋` so write buffers don't thrash ([S2]); (b) on edge flash, a *much*
  tighter cap (e.g. tens, not 1024) to avoid many-tiny-blob random I/O and RocksDB key explosion (R.3).
  Round *P* up to a power of two so `hash % P` becomes a bit-mask and recursion can consume fresh bits.

Because we choose *P after* the map is built (we know `est_state_bytes` exactly), and we size for
`R/P ≤ mem_budget` with 1.5× headroom, a uniform hash gives **L = 0 recursion levels** in the common
case — a single pass, which is the right default for constrained devices where extra passes are costly.
Keep recursion (consume fresh hash bits and re-split a still-too-big partition) strictly as a
**fallback** ([S2], [S4]).

> **One-line defensible default to put in the thesis:** *"We choose the partition count as
> `P = next_pow2(clamp(⌈1.5 · S / B⌉, 4, P_max))`, where S is the measured spilled-state size and B the
> per-partition memory budget — the size-to-fit rule of Graefe (1993) [S2] with PostgreSQL's 1.5×
> headroom factor [S6] — so that under a uniform hash each partition's rebuilt hash table fits in B in
> a single pass, with recursive repartitioning as a fallback."*

### R.2 The skew caveat to put in the thesis

> **Caveat (to state explicitly):** `hash % P` only bounds per-partition memory when group-key
> *frequencies* are not pathologically concentrated. A good mixing hash makes partitions uniform under
> moderate frequency skew, but a *single dominant key* maps its entire mass to one partition, which no
> static fan-out can bound — the same limitation acknowledged by production engines (e.g. Trino:
> spilling *"may fail to divide intermediate data into chunks small enough … leading to out-of-memory"*
> [S15]). Our single-pass, fixed-stride design therefore assumes bounded single-key mass; we provide
> **recursive repartitioning as a graceful-degradation fallback** ([S2], [S4]) so an overflowing
> partition is re-split (or streamed) rather than causing OOM. *Runtime-adaptive repartitioning and
> bucket-spreading under heavy skew (Nakayama/Kitsuregawa/Takagi 1988 [S10]; Kitsuregawa/Ogawa [S12];
> Spark AQE skew handling [S14]) are out of scope and noted as future work.*

### R.3 The RocksDB key-granularity recommendation

> **Store one RocksDB key per partition, not per group.** Use a key like `<window_id, partition_id>`
> with the value being the serialized partition payload (all that partition's group→accumulator
> entries), rather than one RocksDB key per group. Rationale: on memory- and IOPS-constrained flash,
> fewer-but-larger values minimise RocksDB **key count, write amplification, and compaction churn**
> ([S18]–[S20]) and keep spill writes / emit reads **sequential** (one large blob per partition) rather
> than a random-I/O storm of tiny keys. Because spilled-partition data is **transient** (deleted once
> the window fires), also minimise compaction over it; consider RocksDB **BlobDB / key-value
> separation** to keep these large transient values out of the LSM ([S18]). This complements R.1: a
> *modest* *P* (R.1's edge-tightened `P_max`) plus *one-key-per-partition* gives few, large, sequential
> blobs — the configuration that is gentlest on constrained flash.

### R.4 Summary of the three knobs

| Knob | Recommendation | Primary citation |
|---|---|---|
| Fan-out *P* | `next_pow2(clamp(⌈1.5·S/B⌉, 4, P_max))`, *P_max* tightened for edge flash | Graefe size-to-fit [S2]; PG 1.5× factor + [4,1024] [S6] |
| Recursion | single pass by default (size *P* after build); recursive re-split as fallback only | Graefe `L=⌈log_F(R/M)⌉` [S2]; PG `used_bits` recursion [S4] |
| Skew | mixing hash + headroom + recursive fallback; caveat single-key skew; adaptive repartitioning = future work | [S10],[S12],[S14],[S15] |
| RocksDB granularity | one key per *partition* (large value), blob-separation for transient data | RocksDB tuning/amplification [S18]–[S20] |

---

## Sources

**Axis A — foundational external aggregation**

- **[S1]** Leonard D. Shapiro, *"Join Processing in Database Systems with Large Main Memories"*, ACM
  Transactions on Database Systems (TODS) 11(3), Sept 1986. (Origin of hybrid hash join / memory-bounded
  partitioning.) PDF: https://cs-people.bu.edu/~mathan/reading-groups/papers-classics/join.pdf — Semantic
  Scholar: https://www.semanticscholar.org/paper/03d5c3b608dcc22789567de40e7f2dd6250841df
- **[S2]** Goetz Graefe, *"Query Evaluation Techniques for Large Databases"*, ACM Computing Surveys
  25(2), June 1993, pp. 73–170 (revised Nov 1993). (Canonical survey; §3.2 hashing, recursive
  partitioning, fan-out `F=⌊M/C−1⌋`, `L=⌈log_F(R/M)⌉`.) PDF:
  https://cs.uwaterloo.ca/~david/cs848s13/graefe.pdf — DOI: https://dl.acm.org/doi/10.1145/152610.152611
- **[S3]** Goetz Graefe, *"Sort vs. Hash Revisited"*, IEEE TKDE, 1994. (Duality of sort/hash set-matching.)
  PDF: https://15721.courses.cs.cmu.edu/spring2016/papers/graefe-tkde1994.pdf
- **[S4]** PostgreSQL commit / thread, *"Disk-based Hash Aggregation"* (Jeff Davis, v13). Spill mode,
  partitioned spill, recursive reprocessing. https://www.postgresql.org/message-id/E1jEhaL-0002Ur-2L@gemulon.postgresql.org
  — design thread: https://www.postgresql.org/message-id/507ac540ec7c20136364b5272acbcd4574aa76ef.camel@j-davis.com
- **[S5]** PostgreSQL feature matrix, *"Hash aggregation can use disk"* (v13).
  https://www.postgresql.org/about/featurematrix/detail/hash-aggregation-can-use-disk/
- **[S6]** PostgreSQL source, `src/backend/executor/nodeAgg.c` — `hash_choose_num_partitions`,
  `HASHAGG_PARTITION_FACTOR 1.50`, `HASHAGG_MIN_PARTITIONS 4`, `HASHAGG_MAX_PARTITIONS 1024`, partition
  trade-off comments. https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeAgg.c
- **[S7]** Laurens Kuiper, Peter Boncz, Hannes Mühleisen, *"Robust External Hash Aggregation in the Solid
  State Age"*, IEEE ICDE 2024 (CWI Amsterdam). Radix-partitioned external aggregation, over-partitioning,
  RAM-oblivious phase 1, page-granular buffer-manager spilling, graceful degradation.
  PDF: https://duckdb.org/pdf/ICDE2024-kuiper-boncz-muehleisen-out-of-core.pdf
- **[S8]** DuckDB, *"Announcing DuckDB 0.9.0"* (2023-09-26) — out-of-core hash aggregation, radix
  partitioning, increasing radix bits when a partition doesn't fit. https://duckdb.org/2023/09/26/announcing-duckdb-090
- **[S9]** DuckDB blog, *"Parallel Grouped Aggregation in DuckDB"* (2022-03-07) — radix partitioning for
  parallel aggregation; partition threshold (10,000-row local HT). *(vendor blog)*
  https://duckdb.org/2022/03/07/aggregate-hashtable

**Skew lineage (Axis A/B)**

- **[S10]** M. Nakayama, M. Kitsuregawa, M. Takagi, *"Hash-Partitioned Join Method Using Dynamic
  Destaging Strategy"*, VLDB 1988, pp. 468–478. (Dynamic destaging for skew.)
- **[S11]** M. Kitsuregawa, M. Nakayama, M. Takagi, *"The Effect of Bucket Size Tuning in the Dynamic
  Hybrid GRACE Hash Join Method"*, VLDB 1989. (Bucket-size tuning under unbalanced bucket distribution.)
  https://www.semanticscholar.org/paper/f2dbcae5911a67984901015d0204a0d9b217ac52
- **[S12]** M. Kitsuregawa, Y. Ogawa, *"Bucket Spreading Parallel Hash: A New, Robust, Parallel Hash Join
  Method for Data Skew in the Super Database Computer (SDC)"*, VLDB 1990. (Skew-robust parallel hash.)
  https://www.semanticscholar.org/paper/5b67b1e3482041f1de435f3dd4445c86eaeae84f

**Axis B — fan-out / skew in modern engines**

- **[S13]** Apache Spark, *"Performance Tuning"* (SQL) — `spark.sql.shuffle.partitions` (default 200),
  AQE coalesce/skew, `advisoryPartitionSizeInBytes`. https://spark.apache.org/docs/latest/sql-performance-tuning.html
- **[S14]** Databricks, *"Adaptive Query Execution: Speeding Up Spark SQL at Runtime"* (2020-05-29) —
  coalesce post-shuffle partitions, skew-join split/replicate. *(vendor blog, authoritative on AQE)*
  https://www.databricks.com/blog/2020/05/29/adaptive-query-execution-speeding-up-spark-sql-at-runtime.html
- **[S15]** Trino, *"Spill to disk"* docs — aggregation spill (accumulated state written then merged with
  lower footprint), join build partitioned by `task.concurrency`, revocable memory, no-guarantee caveat.
  https://trino.io/docs/current/admin/spill.html — spilling properties: https://trino.io/docs/current/admin/properties-spilling.html
- **[S16]** Apache Hive, *"Hybrid Hybrid Grace Hash Join, v1.0"* — grace-hash + in-memory blend, spill
  partitions on overflow. https://cwiki.apache.org/confluence/display/Hive/Hybrid+Hybrid+Grace+Hash+Join,+v1.0
- **[S17]** (Skew salting, common practice) Suffyan Asad, *"Handling Data Skew in Apache Spark"* — salting
  hot keys. *(blog, practitioner)* https://medium.com/@suffyan.asad1/handling-data-skew-in-apache-spark-techniques-tips-and-tricks-to-improve-performance-e2934b00b021

**Axis B — RocksDB amplification / constrained device**

- **[S18]** Facebook, *"RocksDB Tuning Guide"* (official wiki) — write/read/space amplification trade-off,
  compaction, BlobDB / key-value separation. https://github.com/facebook/rocksdb/wiki/RocksDB-Tuning-Guide
- **[S19]** S. Dong et al., *"Optimizing Space Amplification in RocksDB"*, CIDR 2017 — three-way
  amplification trade-off, leveled vs tiered. https://www.cidrdb.org/cidr2017/papers/p82-dong-cidr17.pdf
- **[S20]** *"Benchmarking, Analyzing, and Optimizing Write Amplification"*, EDBT 2025 — leveled vs tiered
  write/read amplification (T× relationship). https://openproceedings.org/2025/conf/edbt/paper-114.pdf

**Axis C — streaming state-backend spilling**

- **[S21]** Apache Flink, *"Using RocksDB State Backend in Apache Flink: When and How"* (2021-01-18,
  official Flink blog) — serialized bytes, off-heap→disk, state > heap, incremental checkpoints, long
  windows / large keyed state. https://flink.apache.org/2021/01/18/using-rocksdb-state-backend-in-apache-flink-when-and-how/
- **[S22]** Apache Flink docs, *"Tuning Checkpoints and Large State"*.
  https://nightlies.apache.org/flink/flink-docs-master/docs/ops/state/large_state_tuning/
- **[S23]** Flink keyed-state layout: RocksDB key = serialized `<KeyGroup, Key, Namespace>`, value =
  serialized state; one column family per registered state; window = namespace. `RocksDBKeyedStateBackend`
  API + Ververica memory guide: https://nightlies.apache.org/flink/flink-docs-release-2.0/api/java/org/apache/flink/state/rocksdb/RocksDBKeyedStateBackend.html
  — https://www.ververica.com/blog/manage-rocksdb-memory-size-apache-flink
- **[S24]** Apache Flink docs, *Windows* — `ReduceFunction`/`AggregateFunction` incremental aggregation
  (single accumulator per window, no full-window buffering). https://nightlies.apache.org/flink/flink-docs-master/docs/ops/state/state_backends/
- **[S25]** FLIP-41, *"Unify Binary format for Keyed State"* — composite `<KeyGroup,Key,Namespace>` key
  format. https://cwiki.apache.org/confluence/display/FLINK/FLIP-41:+Unify+Binary+format+for+Keyed+State
- **[S26]** Apache Flink, *"Managing Large State in Apache Flink: An Intro to Incremental Checkpointing"*
  (2018-01-30, official blog). https://flink.apache.org/2018/01/30/managing-large-state-in-apache-flink-an-intro-to-incremental-checkpointing/
- **[S27]** Apache Kafka Streams developer guide — RocksDB local state stores; windowed stores use
  time-based segments; changelog compaction+deletion. https://docs.confluent.io/3.1.1/streams/developer-guide.html
- **[S28]** Coralogix, *"Kafka Streams Window By & RocksDB Tuning"* — windowed RocksDB store segments,
  composite (key + window timestamp) keys, segment dropping for expiry. *(vendor eng blog)*
  https://coralogix.com/blog/kafka-streams-window-by-rocksdb-tuning/
- **[S29]** AWS, *"RocksDB 101: Optimizing stateful streaming in Apache Spark…"* — Spark Structured
  Streaming RocksDB state store manages state in native memory + local disk, spills to disk. *(vendor blog)*
  https://aws.amazon.com/blogs/big-data/rocksdb-101-optimizing-stateful-streaming-in-apache-spark-with-amazon-emr-and-aws-glue/
- **[S30]** Apache Spark, *"Structured Streaming Programming Guide"* — RocksDB state store provider (≥3.2),
  large keyed state, GC-pause avoidance, `flat/mapGroupsWithState`.
  https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html
