# Nexmark Phase — Realistic-workload Validation of the OOC Backend

> **Position in the thesis arc:** the 10k OOC sweep
> (`../e3big/EXPERIMENT-PLAN-FULL.md`) is the *characterization + tuning* step —
> it answers H1–H5 and locates the survival ceiling. This phase is the
> *realism / validation* step. **No more knob-tuning.** RocksDB config is
> *frozen* at whatever the 10k data says wins; this phase tests whether that
> frozen config survives realistic streaming load.

## 0. What this phase answers

| RQ / Claim                                                                                  | Tested by                                  |
|---------------------------------------------------------------------------------------------|--------------------------------------------|
| **RQ1 (realism)** — does OOC survive on canonical streaming workloads where in-mem doesn't? | Variant axis: ooc vs inmem at scale ≥ 5 M  |
| **RQ2 (realism)** — what's throughput + p99 on Nexmark, at the frozen config?               | All cells, all queries                     |
| **NES integration cost (single-op vs multi-op)**                                            | Q3-bridge (single agg) vs Q7 (single max) vs Q8_Variant (join + agg) |
| **Window-flush dynamics** that the 10k phase skipped (100 s windows there → 10 s here)      | All cells; many more windows per run       |

**Not** in scope: more compression/wbuf/cache sweeping (10k already covered that).
**Not** in scope: sliding or session windows (explicit user decision).

## 1. Workloads (all tumbling, Generator-source — same primitive as the 10k)

All three queries share a `bid(timestamp, auctionId, bidder, datetime, price)` stream
emitted by the Generator. `auctionId` is the keying dimension; `timestamp` is event
time. Window size **10 s** (vs 100 s in the 10k phase) — produces 10× more
window-flush events per run, exercising the window-close path the 10k averaged away.

| Query | Shape | Why |
|-------|-------|-----|
| **Q3-bridge** — `SELECT auctionId, SUM(price) GROUP BY auctionId WINDOW TUMBLING(ts,10s)` | Single tumbling agg, all-distinct keys | **Bridge with the 10k sweep**: same shape as `SpillSmokeVoid` but with Nexmark schema + 10 s window. Lets us directly compare 10k-phase numbers to this phase's at the matching state. |
| **Q7** — `SELECT auctionId, MAX(price) GROUP BY auctionId WINDOW TUMBLING(ts,10s)` | Single tumbling max, hot-skewed keys | Nexmark Q7 stand-in (canonical Q7 is "highest price per period" with a self-join; we use max-per-key, structurally simpler, still hot-key). Tests the aggregate-op axis (max vs sum) under skew. |
| **Q8_Variant** — `bid INNER JOIN auction ON auctionId=id WINDOW TUMBLING(ts,10s)` | Tumbling 10s join + per-window grouping | Adapted from `nes-systests/benchmark/Nexmark.test` Q8_Variant. Tests **multi-operator pipelines** — the only realistic-stress dimension the 10k skipped entirely. |

For Q8_Variant the `auction` stream is a 2nd Generator source (smaller scale, schema:
`auction(timestamp, id, initialbid, reserve, expires, seller, category)`).

> Caveat to document in the thesis: Generator-based "Nexmark" isn't bit-identical
> to upstream Flink's Nexmark generator → numbers are **internally
> comparable** (ooc vs inmem on the same generator) but **not directly comparable**
> to published Flink Nexmark throughput. A future follow-up could replace the
> Generator with `ATTACH FILE large/nexmark/bid_653M.csv` for upstream comparison.

## 2. Configuration — defended by independent prior work, not picked from the eval data

> **Methodological commitment:** every knob below is justified by a source that
> is *independent* of this phase's evaluation workloads (Microbenchmark-BA's
> db_bench results on RocksDB, the RocksDB tuning guide, or the theoretical H4
> ceiling formula). We deliberately do **not** run a "best config" search on
> the Nexmark workloads before evaluating on them — that would be survivorship
> bias and the OOC-vs-inmem comparison would be a function of "ours got tuned,
> theirs didn't" rather than a real-system result. The same in-mem variant runs
> with the equivalent default config, so the comparison is fair.

| Param | Value | Defence (independent of this phase's eval) |
|-------|-------|--------------------------------------------|
| `compression` | **lz4** | Microbenchmark-BA (db_bench, not NES) — lz4 = 4.4× over none on `readwhilewriting`, marginally beats snappy (3.9×) and zstd (2.5×). RocksDB community default for streaming/write-heavy LSM. |
| `write_buffer_size_mb` | **64** | RocksDB tuning guide cites 64 MB as the balanced default for write-heavy LSM. Microbenchmark-BA shows 64 → 128 gain is ~9 %; below 64 the gain is much steeper → 64 MB sits at the diminishing-returns knee. |
| `block_cache_size_mb` | **32** | RocksDB rule-of-thumb `cache:wbuf ≈ 0.5`. Also constrains cache to ≤ 6 % of the smallest cap in the matrix (cap=256), leaving cap-space for write buffer and runtime. |
| `soft_threshold_mb` | **182** | **H4 theoretical ceiling formula**: OOM ceiling = `2·wbuf + cache + 100 MB` = `128 + 32 + 100` = 260 MB. 70 % watermark = 182 MB. The "start trickling spill" threshold per H4's analysis. |
| `hard_threshold_mb` | **234** | Same formula; 90 % watermark = 234 MB. The "must spill now" threshold per H4. |
| `number_of_partitions` | **SWEPT: {1, 2, 4, 8}** | **Partition-at-spill is the architectural contribution of this thesis** (see `../E3-E4-RESULTS.md`: "naive OOC (P=1, no partitioning) is OOM-killed once retained state ≥ 800k keys… Partition-at-spill (P=8) completes the entire ladder"; `../SUMMARY-CONTENT.md`: "Partition-at-spill lets a tumbling windowed aggregation whose state exceeds RAM complete on RocksDB-backed [storage]"; `nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp` implements `spill(numberOfPartitions)`, `streamEmitByPartition()`, `partitionedSerialize()`). The thesis claim is "partitioning makes OOC work" — locking P to a single value would either evaluate the failed naive case (P=1) or force a heuristic-vs-bias debate over which P>1 to use. Sweeping {1, 2, 4, 8} as an axis lets the data — not a heuristic — locate the realistic-workload P-knee. P=1 is the strawman baseline; P=2/4/8 trace the contribution's effect on realistic workloads. |
| `POOL` (buffer mgr) | **16000** | NES baseline default ≈ 64 MB of 4 KB buffers ≈ one write-buffer's worth of pipeline buffering. Documented as the default in the worker config; not lifted to "make ooc survive further" since that would be tuning against the eval. |
| `THREADS` | **4** | Pinned to the per-cell core budget (`--cpus=4` in `run-cell.sh`) so the worker actually uses all available cores. Independent of the workload. |

**Note on the values not matching the 10k's baseline.** The 10k's Sweep F
baseline used `soft=4 / hard=8` (deliberately aggressive to force spill on
every cell so the 10k could *characterise* the spill backend). For *evaluation*
the principled choice is the H4 70 %/90 % values above, which let in-mem run
unimpeded until the OOM ceiling is genuinely close. The 10k's Sweep F tests
exactly the (182, 234) point — if its verdict shows that watermark produces
materially worse throughput at the smaller scales than (4, 8), document the
deviation in the thesis; do **not** flip the Nexmark phase to (4, 8) to chase
the number.

**In-mem variant uses the equivalent default**: same buffer pool (POOL=16000),
same worker thread count (4), same cap, no spill knobs apply. This keeps the
comparison fair — neither side gets workload-specific tuning.

## 3. Matrix — sized to produce per-query heatmaps

Heatmaps are the deliverable (see §5). To draw them with statistical resolution
that survives review (CV ≤ 0.15, ±2 % CI on throughput, ±5 % on survival rate
near boundary), the matrix needs finer scale + cap resolution and more reps than
a one-off validation would.

| Axis | Values | Count |
|------|--------|-------|
| **query** | q3_bridge, q7_max_skewed, q8_variant | 3 |
| **scale** (bid tuples) | 1 M, 2 M, 5 M, 10 M, 20 M, 40 M | **6** (log-spaced; 80 M dropped — was the highest-cost / lowest-marginal-info point; 40 M already tests 4× past inmem ceiling) |
| **cap** (MB) | 256, 512, 1024 | **3** (tight / baseline / generous) |
| **`number_of_partitions` (P)** | 1, 2, 4, 8 | **4** swept axis — the thesis's architectural contribution; data, not heuristic, locates the realistic-workload P-knee |
| **variant** | ooc (cap × P combinations), inmem (cap only — P axis N/A) | 2 base |
| **rep** | 1..40 | 40 |

- ooc cells (cap × P): 3 × 6 × 3 × 4 × 40 = **8 640 cells**
- inmem cells (cap only): 3 × 6 × 3 × 40 = **2 160 cells**
- Inmem-uncapped refs (no cap, no P): 3 × 6 × 40 = **720 cells**
- **Total: 11 520 cells**

Mean cell wall-clock (Generator-bound, scales linearly with state, plus
window-flush + spill overhead): ~157 s averaged across the 1 M–40 M ladder
(≈ 15 s at 1 M, ≈ 240 s at 20 M, ≈ 480 s at 40 M). Total compute:

- Serial: ~502 h ≈ **21 days**
- **`-P 4` with CPU pinning: ~126 h ≈ 5.2 days** (4 cells × 2 dedicated physical cores each = exact fit on i7-10700K's 8 physical cores; no inter-cell sharing, no oversubscription, clean measurements)

`launch.sh` defaults to `-P 4` and assigns each concurrent cell a slot ID 0..3; `run-cell.sh` translates that to `--cpuset-cpus={0,1,8,9}/{2,3,10,11}/{4,5,12,13}/{6,7,14,15}` so the cell is pinned to its own 2 physical cores (4 logical CPUs via SMT). Higher `-P` values are explicitly capped by the launcher because the host only has 8 physical cores — going above 4 would force cells to share physical cores, contaminating throughput/latency measurements.

Expected status distribution (drives the heatmap colour gradients):

| Cell | Expected | Why |
|------|----------|-----|
| `inmem` at scale ≤ 2 M (any cap) | `ok` | State fits |
| `inmem` at scale ≥ 5 M, cap=256 | `oom` (rc=137) | Even modest state busts the cap |
| `inmem` at scale ≥ 10 M, cap=512 | `oom` | The "OOC enables survival" headline frontier |
| `inmem` at scale ≥ 20 M, cap=1024 | `oom` | Confirms cap doesn't fix it |
| `ooc` at scale ≤ 20 M, cap ≥ 512 | `ok` | The safe-zone OOC unlocks |
| `ooc` at scale = 40-80 M | mixed `ok`/`err` (rc=141) | The new OOC failure boundary — pool-starvation, not OS-OOM |
| `inmem_uncapped` at any scale | `ok` (unless host OOM) | Overhead denominator |

## 4. Files (this directory)

| Path | What | Notes |
|------|------|-------|
| `work/q3_bridge.test`       | Q3-bridge query + Generator schema | Bid stream only; uniform `auctionId` |
| `work/q7_max_skewed.test`   | Q7 query + Generator schema        | Bid stream only; binomial `auctionId` (hot-key) |
| `work/q8_variant.test`      | Q8_Variant query                    | Bid + auction Generator sources |
| `gen-matrix.sh`             | Emits `jobs.txt` per the matrix above | REPS overridable; one row per cell |
| `run-cell.sh`               | Per-cell runner (Docker, capped)    | Adapted from e3big — same TSV schema for analyzer reuse |
| `launch.sh`                 | xargs -P launcher                   | Same as e3big |
| `collect.sh`                | TSV concat with header              | Same as e3big |

## 5. Analysis — heatmaps are the deliverable

To be drafted **after** data lands (`analyze_heatmaps.py`, see §6 step 7). The
target output is the figure set below — every artefact maps to one or more thesis
chapters and to the open RQs.

### 5.1 Per-query heatmaps (×3 queries → 15 base figures)

Each is a 2D grid on **scale × cap**, with a colour gradient + per-cell mean-±-CI
annotation. Cells with `n < ~30 ok reps` get hashed for "low-confidence".

| # | Figure | What the cell shows | Thesis chapter |
|---|--------|--------------------|----------------|
| 1 | **Survival heatmap (inmem)** | `% reps status=ok` | RQ1, "the baseline failure surface" |
| 2 | **Survival heatmap (ooc)** | same | RQ1, "the surface OOC unlocks" |
| 3 | **Throughput heatmap (ooc, ok cells)** | mean tuples/sec | RQ2, "performance landscape" |
| 4 | **OOC-overhead heatmap** | `throughput_ooc / throughput_inmem_uncapped` | RQ2, "what you pay for the survival ceiling" |
| 5 | **p99 latency heatmap (ooc)** | mean p99 µs over ok reps | RQ2, "tail behaviour near the boundary" |

### 5.2 Cross-cutting summary figures

| # | Figure | What | Thesis chapter |
|---|--------|------|----------------|
| 6 | **Survival-delta map** (×3 queries) | Boolean grid: green = both ok, yellow = only ooc ok, red = both fail | RQ1 headline |
| 7 | **Failure-mode breakdown** (×3 queries) | Stacked bar at each (scale, cap): ok / oom (rc=137) / err (rc=141) | New: documents the two failure modes |
| 8 | **Spill-amplification heatmap** (ooc only, ×3 queries) | `spill_dir_mb / state_mb` | RQ3, storage cost of survival |
| 9 | **Per-cell CV grid** (×3 queries) | sanity / data quality | Hand-off check |
| 10| **Bridge plot: Q3-bridge ooc vs 10k phase** | Throughput at matched (scale, cap), 10 s vs 100 s window | Cross-phase consistency |

### 5.3 Verdicts that drop straight into the thesis

- **OOC vs inmem survival frontier** — at which (scale, cap) does ooc let the
  query survive where inmem can't? Quantified per query.
- **OOC overhead at fixed scale** — for cells where both survive, what's
  the throughput cost of going through RocksDB? Per query and per scale.
- **Two failure modes** — at what (scale, cap) does ooc itself break, and is
  it OS-OOM (rc=137) or pool-starvation (rc=141)? [[project-ooc-err-policy]]
- **Hot-key skew effect** — does Q7's NORMAL_DISTRIBUTION change the survival
  frontier or the overhead vs Q3-bridge's all-distinct uniform?
- **Multi-op effect** — does Q8_Variant's join+agg compound spill costs vs
  single-op queries?

## 6. Launch checklist (apply after the 10k finishes)

> The config in §2 is fixed and defended; **no pre-tuning sweep before launch.**
> Re-analysing the 10k data is fine and expected — it gives the thesis a fuller
> account of *why* the §2 choices land where they do — but it does **not** feed
> back into the Nexmark §2 table. If the 10k's Sweep F shows the (182, 234)
> watermark is suboptimal at small state, that finding is reported as-is in the
> thesis; the Nexmark phase still runs with (182, 234) because the choice was
> justified by the H4 ceiling formula a priori.

1. **Wait for 10k to finish**: `kill -0 $(cat ../e3big/launch.pid) 2>/dev/null && echo running || echo done`.
2. **Re-analyse 10k for the thesis** (does not change §2): `cd ../e3big && bash collect.sh && python3 analyze.py runs.tsv figs && cat figs/H1-H5-verdict.md`.
3. **Reset Nexmark workdir**: `cd .planning/eval-phase/nexmark-phase && rm -rf results logs jobs.txt work/rendered`.
4. **Generate**: `REPS=40 bash gen-matrix.sh` — confirm **11 520 jobs**.
5. **Pre-flight smoke**: `head -10 jobs.txt > smoke.txt && xargs -P 2 -L 1 bash run-cell.sh < smoke.txt` — verify ≥8 `status=ok`.
6. **Launch**: `nohup bash launch.sh 4 > launch.out 2>&1 & echo $! > launch.pid; disown` — ~**5.2 days at -P 4 with CPU pinning** (4 slots × 2 dedicated physical cores; clean measurement, no inter-cell sharing).
7. **Collect + analyse**: `bash collect.sh && python3 analyze_heatmaps.py runs.tsv figs` (analyser drafted after data lands, per §5).

## 7. Open decisions for the reader

- **Drop Q8_Variant?** if 5 h fits the schedule budget, keep it — the multi-op story is otherwise absent from the thesis. If not, the cut is clean (Q8_Variant alone = 1/3 of cells).
- **Add a custom IoT-sensor query?** Out of scope for "Nexmark phase"; could be its own §3.5 in the thesis if needed.
- **Should `cap` be re-introduced as an axis?** Only if the 10k Sweep A's P-knee finding is ambiguous — otherwise leave it fixed at 512 MB.
