---
title: "Well-configured OOC vs in-memory at 512 MB: OOC prevents OOM in a window (rho~1.5-2.2), at ~15% throughput / ~20% p99 cost, with correct output"
date: 2026-06-08
tags: [thesis, nebulastream, ooc, rocksdb, RQ1, RQ2, finding, significance, scope-limited]
status: concrete initial evidence; honestly scope-limited
---

# Flagship comparison — results, significance, and scope

## Setup (Config B, fixed for both arms; only spill differs)
Release build, `--cpus=2`, cap=512 MB (`--memory-swap=512m`, no swap), **pool=16000 (125 MB)**, **W=50k**,
RocksDB lz4/wbuf16/cache8/P=4, tmpfs input → SD-card spill, full mem_trace+gauge instrumentation.
5 rho points (state/cap, measured from uncapped footprint) × {OOC@512 ×8, in-mem@512 ×6, uncapped ×8,
+OOC/uncapped checksum}. ~120 runs. rho measured = uncapped-in-mem anon / 512.

## Results
| rho (meas) | in-mem @512 | OOC @512 | Fisher p | tps overhead | p99 overhead | checksum |
|---:|---|---|---:|---:|---:|---|
| 0.69 | 6/6 complete | 4/8 (4 OOM) | 0.085 | – | – | MATCH |
| 0.97 | 6/6 complete | 0/8 (OOM) | 3.3e-4 | – | – | – |
| 1.56 | 0/6 (OOM) | **6/8 complete** | 9.7e-3 | −18% | +17% | **MATCH** |
| 2.21 | 0/6 (OOM) | **7/8 complete** | 4.7e-3 | −12% | +23% | (rep failed) |
| 3.52 | 0/6 (OOM) | 0/8 (OOM) | 1.0 | – | – | – |

**Pooled rho>=1: OOC 13/24 vs in-mem 0/18 → Fisher's exact p = 1.16e-4.** Throughput Welch-t significant
(p down to 2e-9). **OOC failures are OOM, NOT crashes** (1 rc134 in 40 OOC runs) — genuine memory behaviour.

## Interpretation (honest)
OOC prevents OOM **in a window, not universally**:
- **rho ~1.5–2.2 (the win):** in-mem OOMs, OOC completes (6–7/8) with **correct output** (checksum MATCH at
  rho1.56) at ~15% throughput / ~20% p99 cost. RQ1 validated + significant here.
- **rho ~1 (OOC counterproductive):** in-mem *fits* (6/6) but OOC **OOMs 8/8** — its RocksDB + spill/restore
  + terminal-flush transients push it over 512 while plain in-mem stays under. Overhead > benefit.
- **rho ~3.5 (beyond OOC's envelope):** both OOM — spilling can't reclaim fast enough (see root cause).
- **rho<1:** OOC even teeters (4/8 OOM @ rho0.69) — transient spikes near a tight cap; in-mem is rock-solid.

## ROOT CAUSE of the narrow window (measured)
Spilling reduces the **logical** store but NOT **resident RAM**: the `UnpooledChunksManager` returns a chunk
to the OS only when ALL its segments drain, so spilled-but-mixed chunks stay resident (see
[[project-ooc-memory-measurement]]: OOC anon ≈ in-mem anon). So OOC's footprint ≈ in-mem + overhead ⇒ OOC
OOMs near the same point as in-mem (plus its transients) instead of dramatically below it.

## SCOPE — these findings hold ONLY for this configuration
Valid for: 512 MB cap, `--cpus=2`, pool=16000, W=50k, this Zipfian/out-of-order workload, release build,
SD spill, no swap. **Transferable (qualitative):** a well-configured OOC can process tumbling state
moderately larger than memory (≈1.5–2.2×) without OOM, producing correct results, at a ~15%/~20% cost.
**NOT generalised:** the exact rho window, the costs, rho_max≈3.5, and the rho≈1 counterproductivity —
all shift with cap/pool/window/workload/storage/build. Explicitly NOT claimed: OOC prevents OOM at any
state size; OOC helps at/below budget (it hurts at rho≈1); or that any numbers hold off this config.

## BOUNDED variant comparison (pooled HashMap pages, branch ooc-pooled-bounded; same matrix, b_ labels, page_size=8192)
Combined dataset: `results/combined_dataset.csv` (in-mem vs unpooled-OOC vs bounded-OOC).

| rho | in-mem | OOC-unpooled | OOC-bounded | bnd tps ovh | bnd p99 ovh |
|---:|---|---|---|---:|---:|
| 0.69 | 6/6 | 4/8 | 8/8 | +4% | +23% |
| 0.97 | 6/6 | 0/8 | 1/8 | +0% | +6% |
| 1.56 | 0/6 | 6/8 | 6/8 | -3% | +8% |
| 2.21 | 0/6 | 7/8 | 8/8 | -4% | +3% |
| 3.52 | 0/6 | 0/8 | 0/8 | – | – |

Pooled rho>=1: bounded-OOC 14/24, unpooled-OOC 13/24, in-mem 0/18.
**Fisher bounded-OOC vs in-mem: p=4.1e-5** (headline holds). **Fisher bounded vs unpooled OOC: p=1.0** (no completion difference).

**Bounding (pooling the data pages, recycled on spill) — what it did and did NOT do:**
- DID NOT widen the operating window: same rho~1.5-2.2; still fails at rho~1 (overhead) and rho~3.5 (ceiling); statistically identical completion to unpooled (p=1.0). ⇒ data-page chunk-retention was NOT the binding capacity limit.
- DID slash the cost: throughput overhead ~15% (unpooled) → ~3% (bounded); lower p99 overhead. (Recycling pooled buffers avoids unpooled-allocator churn.)
- DID fix reliability/teetering: 8/8 @rho0.69 (vs 4/8) and 8/8 @rho2.21 (vs 7/8) — transient near-cap OOM spikes removed.
- Implication: the remaining capacity limits are the bucket directories (~30% of HashMap, still unpooled — a Nautilus structure), the fixed pool floor, and the un-spillable hot window. Widening the window needs Nautilus-side changes (bucket-dir layout, [[FINDINGS-hashmap-entry-vtable|entry header]]) + pool/hot-window handling; the JIT/codegen pipeline is needed for none of it.

## Caveats
- Checksum coverage partial (correctness proven at rho0.69 & rho1.56; the uncapped checksum cells used
  pool=16000 and exhausted it, and 2 OOC checksum reps OOM'd). The rho1.56 MATCH (correct while spilling) is
  the meaningful proof.
- The rho axis is confounded by OOC's own overhead shifting the effective budget — a cleaner future design
  is the per-arm **minimum-survival-cap** sweep (decoupled from fixed-512 teetering).
