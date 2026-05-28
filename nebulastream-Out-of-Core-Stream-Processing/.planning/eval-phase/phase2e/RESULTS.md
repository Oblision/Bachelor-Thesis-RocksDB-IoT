# Phase 2e trigger measurement — RESULT: 2e NOT needed

Date: 2026-05-25. Harness: `phase2e/run-rss-part.sh` (= phase0c `run-rss.sh` + the 2d `number_of_partitions` knob).
Workload: `phase0c/SpillSmokeVoid.test` (Void sink, no result-diff pollution). Cell: **320 MB cap, threads=4**, pool=16000, soft=4 MB, hard=8 MB, emit_lag=2 000 000.

| Run | number_of_partitions | rc | container peak | store_peak (slice-store resident) | slices spilled | spill dir |
|---|---|---|---|---|---|---|
| `F20_p1_320` | 1 (baseline) | **137 (OOM-kill)** | 320 MB (=cap) | NA (killed before metrics) | 0 (killed) | 43 MB |
| `F20_p8_320` | 8 | **0 (success)** | 320 MB (=cap) | **4 MB** (4 254 720 B) | 19 / 77.8 MB | 43 MB |

## Decision: 2e is NOT built for Phase 2

DESIGN-BRIEF §3/§7 trigger: build 2e IFF the 320 cell at threads>1, P>1 returns `rc=137` OR `store_peak ≈ the whole retained band`. The P=8 run returns **`rc=0` AND `store_peak=4 MB ≪ 78 MB`** → the trigger is NOT met → 2e is NOT built (recorded as "available-if-needed" future work).

## Interpretation (thesis headline)

Partition-at-spill (2a–2d) alone closes the OOM the thesis targets: the 320 cell goes **`rc 137 → rc 0`**, and the slice-store resident working set drops from ~78 MB (the whole retained band, which P=1 cannot fit under the cap) to **4 MB** — the `1/P` build-side bound holding exactly as designed (B/P with P=8). 19 slices (77.8 MB of window state) are spilled and streamed back one partition at a time, so the process completes the aggregation that previously OOM-killed.

Caveat (honest): the *container* peak RSS sits at the 320 MB cap for both runs because the fixed buffer pool (16000 × 8192 B ≈ 131 MB) + RocksDB + runtime dominate process RSS; `store_peak` is the metric partitioning controls, and it is bounded to 4 MB. A smaller pool would show process peak tracking `store_peak` down; the in-flight-chunk pileup that 2e would bound did NOT cause an OOM here (rc=0), which is exactly why 2e is not triggered. If a future workload/config does drive `rc=137` or `store_peak ≈ band` at P>1, the 2e counting-semaphore design (DESIGN-BRIEF §3) is specified and ready to implement.

Raw: `phase2e/results.tsv`, `phase2e/logs/F20_p{1,8}_320.log`.
