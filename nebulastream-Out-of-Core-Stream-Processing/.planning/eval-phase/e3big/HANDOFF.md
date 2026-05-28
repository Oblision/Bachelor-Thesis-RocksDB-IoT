# HANDOFF — running the scaled OOC sweep on your home device

Everything to launch, monitor, resume, and analyze the 186-config evaluation. Plan: `../EXPERIMENT-PLAN-FULL.md`.

## 0. Prerequisites (one time)
- Docker image `nebulastream/nes-development:local-rocksdb` available on the machine.
- The repo checked out with a **built** `cmake-build-debug` (the systest binary must exist). Build once:
  ```
  docker run --rm --workdir /workspace -v "$PWD:/workspace" nebulastream/nes-development:local-rocksdb \
    bash -c "cd cmake-build-debug && ninja"
  ```
  (Must include the E1 + H2 commits — the metric NES_INFO lines come from those.)
- Tools used: `docker`, `bash`, `xargs`, `awk` (all standard). No GNU parallel needed.

## 1. Generate the job list
```
cd .planning/eval-phase/e3big
REPS=50 bash gen-matrix.sh        # 186 configs x 50 = 9,300 cells. Use REPS=20 for a ~1-day run.
```
Prints the job count + a runtime estimate. Edit the axis arrays / baseline at the top of `gen-matrix.sh` to retune.

## 2. Launch (parallel + resumable)
```
bash launch.sh 8                  # 8 = parallelism. Pick so: parallelism*4 <= cores AND parallelism*512MB <= RAM.
```
- Each cell is a capped Docker `systest` run (~2 min); `8` runs them 8 at a time.
- **Resumable:** every finished cell writes `results/<label>.tsv`; `run-cell.sh` skips cells that already have one.
  If the machine reboots or you Ctrl-C, just **re-run the same `launch.sh 8`** — it continues where it stopped.
- Run it under `nohup`/`tmux` for an unattended multi-day run:
  ```
  nohup bash launch.sh 8 > launch.out 2>&1 &
  ```

### Picking parallelism vs duration (each cell ~2 min)
| Reps | Cells | 4-wide | 8-wide | 12-wide |
|---|---|---|---|---|
| 20 | 3,720 | ~31 h | ~16 h | ~10 h |
| 50 | 9,300 | ~78 h | ~39 h | ~26 h |
To *fill* ~10 days deliberately: REPS=50 at 4-wide ≈ 3.3 days; raise REPS or lower parallelism to stretch.
(Statistically, past R≈50 the 95% CI barely moves — CI ∝ 1/√R.)

## 3. Monitor
```
ls results | wc -l                # cells done
tail -f launch.out                # live
grep -h done logs/*.log | tail    # recent results incl. status/throughput/stall
```

## 4. Collect + analyze
```
bash collect.sh                   # results/*.tsv -> runs.tsv (with header)
python3 analyze.py runs.tsv figs  # extended analyzer -> per-RQ + per-hypothesis figures + H1-H5 verdict table
```
(`analyze.py` here is the extended analyzer; the original `../e3/analyze_ooc.py` covers the core survival/residency
figures. The extended one adds compression/wbuf/cache/P/stall/spill-latency axes and the H1–H5 table.)

## 5. What you get
- `runs.tsv` — every cell, 27 columns (axes + status + throughput + peak/store_peak + spill/restore + write-amp +
  footprint + **write_stall_micros** + p99).
- `figs/` — survival (incl. in-mem OOM at 5M), P-knee (residency vs overhead), compression (H3), wbuf (H1),
  cache, watermark (H4), stall% (H2), spill-latency-per-100MB (H5), plus per-cell CV (data quality).
- An H1–H5 verdict table (confirmed / refuted / reframed) from the data → drop into the thesis + the HTML summary.

## Notes / caveats
- Spill scratch dirs are `/tmp/nes-spill-<label>` (cleaned per run). Ensure `/tmp` has room.
- The **skewed** workload uses the Generator's binomial integer "normal" (hot-key skew, not broad Zipf — a
  documented limitation). Verify its distinct-key count once on your data (`logs/A_*skewed*` log).
- Measurement caveats carry over: per-task (not per-window) latency; RocksDB Statistics adds a small constant
  overhead; process RSS is pool-dominated (store_peak is the partition-controlled metric). See
  `../E1-MEASUREMENT-CAVEATS.md`.
