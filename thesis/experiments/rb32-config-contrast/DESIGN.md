# RocksDB config contrast — 32/32 vs 16/8 (RB32)

**Goal.** Re-run the existing spill evaluation with the microbenchmark's
throughput-/compaction-optimal RocksDB config (`write_buffer=32 MB`,
`block_cache=32 MB`) against the thesis baseline (`16 MB / 8 MB`), to test whether
the larger config — affordable only once the memory cap is expanded — gives a
**better profile at the larger caps** by reducing spill-drain load.

This is an **A/B on one variable** (the RocksDB write-buffer/cache sizing). The
partition count stays 4 and every other knob is held identical.

---

## 1. Why this experiment exists

The thesis fixes the spill backend at `lz4 / 16 MB write buffer / 8 MB block
cache / 4 partitions`, held constant across all caps (512/1024/2048 MB). That
`16/8` choice is the **memory-fit** point for the *tightest* cap:

- `SpillConfig.hpp:56` records the reason — RocksDB's untuned ~64 MB write-buffer
  default is "a fixed memory tax under a tight cap (see E0)".
- `E0-SMOKE-RESULTS.md` shows why the tax is fatal at 512 MB: fixed overhead
  (buffer pool counted across two embedded nodes, JIT, page cache) already
  dominates, so every MB RocksDB reserves is subtracted from state headroom.
- The microbenchmark (`Microbenchmark-BA`) shows shrinking is nearly free at
  512 MB: block cache is flat on throughput (8→256 MB), and `lz4 + ≥16 MB`
  write buffer already drives stalls below 1%.

**But the cap was later expanded to 1024/2048 MB**, and `16/8` was *not* revisited.
At those caps the `+40 MB` tax of `32/32` is affordable, and the microbenchmark
shows `32/32` buys real compaction relief:

| 16 MB → 32 MB write buffer (microbench, verified) | value |
|---|---|
| `readwhilewriting` throughput | 23,146 → 27,186 ops/s (+~17%) |
| `fillrandom` write-amplification | 1.41 → 0.91× (~35% fewer bytes to disk) |
| stalls (lz4) | already <1% at both |
| cache 8 → 32 MB throughput | flat |

Ch.5 establishes that **the SD card's write bandwidth bounds the drain rate**
(`5Experimental.tex:343`) and that completion is an ingest-vs-drain race. So
~35% fewer compaction bytes should **drain faster → complete more** at the caps
where `32/32` fits.

---

## 2. Hypotheses

- **H1 (larger caps win).** At 1024 and 2048 MB, `32/32` ≥ `16/8` on completion
  fraction (Fisher's exact, per matched cell).
- **H2 (tight cap loses — validates the baseline).** At 512 MB, `32/32` ≤ `16/8`:
  the `+40 MB` tax costs state headroom under the tight cap. This is the expected
  crossover that turns the result into "the optimal RocksDB config is
  cap-dependent" rather than "we mis-configured".
- **H3 (mechanism = drain).** `32/32` writes fewer bytes to the SD card
  (`io_w_b`, `spill_dir_mb`) and sustains a higher drain rate, which is the
  causal path for H1.

---

## 3. Method

- **One variable changes:** `write_buffer 16→32 MB`, `block_cache 8→32 MB`.
  `number_of_partitions` stays 4; window 50000, `emit_lag` 4 000 000, pools,
  `soft=cap/10 hard=cap/5`, levers (`RESERVEPCT=15 PACED=on`), workloads,
  CPU pinning, reps — all identical to the baseline.
- **Same binary.** These are runtime args; no rebuild. (Must be the lever-branch
  build — `strings $SYSTEST | grep pool_reserve_pct`.)
- **Baseline confirmed at 16/8/4.** Every reported cell used `16 8 4`: all main
  drivers pass it as positional args 16/17/18 to `run-cell.sh` (the `64/32`
  fallback default in `run-cell.sh` was never reached). So the A/B is clean.
- **No edits to the fork.** A shim (`run-cell-rb32.sh`) forces `32/32/4`, tags
  the label, and skips spill-OFF cells; `run_rb32_contrast.sh` generates mirror
  drivers from the originals by redirecting only their `run-cell.sh` call. The
  mirror drivers and shim are **untracked** in the fork and removable.
- **Label tagging.** `W_c512_p8k_r075_r1` → `W_b32_c512_p8k_r075_r1`. Results
  never collide with the baseline, `data.py` still parses cap/pool/ρ/rep, and the
  pair is recovered by stripping `_b32`.

---

## 4. Scope (full spill-ON mirror)

The shim runs every **spill-ON** cell of the mirrored drivers (spill-OFF, pure
in-memory cells are skipped — no RocksDB, byte-identical). Default coverage = the
headline RQ grids:

| Driver | Campaign | ~cells |
|---|---|---|
| `final_matrix.sh` | `W_` (RQ1/RQ2 bounded grid, 512/1024/2048) | ~282 |
| `large_matrix.sh` | `L_` (heap OOC grid; inm-threshold cells init RocksDB → kept) | ~650 |
| `lever_matrix.sh` | `S_` (stability levers) | ~188 |
| `lever_matrix_n1.sh` | `N_` (pooling B0/B1/B2) | ~265 |
| `nx_matrix.sh` | `NX_` (Nexmark) | ~210 |
| `u8_arm.sh` | `U_` (matched-granularity heap control) | ~113 |
| `followup_n3n4.sh` | `F_` | ~69 |
| `n2_matrix.sh` | `B3_` | ~61 |
| `pool_sweep.sh` | `psweep_` | ~60 |
| `gaps_run.sh` + `boundary_run.sh` | `W_`/`U_` gap + boundary fills | ~80 |
| `contrast512.sh` + `skewrun{,2,3,4,5}.sh` | `ooc512_` 512-cap contrast / skew | ~150 |
| **full spill-ON total** | | **~2,500** |

All of the above are enabled by default in the `DRIVERS` array for the full data
set. The exact executed count is reported by the run via the shim's skip log.
Notes: the historical `V_` B2 grid has no separate driver — `final_matrix.sh`
reproduces that grid under its own label; `pool_sweep_uncapped.sh` is left out as
uncapped (outside the cap-contrast scope) but can be appended.

---

## 5. Execution

From this directory (canonical scripts live here in the wrapper repo):

```bash
./run_rb32_contrast.sh list     # show coverage + resolved fork path
./run_rb32_contrast.sh gen      # deploy shim + generate *_rb32.sh into lag-validation (untracked)
./run_rb32_contrast.sh run      # gen, then run all mirrors  (LONG — ~2k cells)
./run_rb32_contrast.sh clean    # remove the deployed shim + *_rb32.sh (result TSVs kept)
```

- **Resumable.** `run-cell.sh` skips a cell whose tagged TSV already exists, so
  re-running `run` continues after an interruption.
- **Parallelism.** Run individual `*_rb32.sh` mirrors concurrently as in the
  baseline campaign (they are independent); `run` executes them serially.
- **Prereqs (same as baseline).** lever-branch `systest` build, the rho CSVs in
  `/dev/shm/nes-input`, the SD spill mount (`/mnt/sd-spill`), Docker.
- **Budget.** ~2k cells, `CELL_TIMEOUT=600 s` each → order-of-days serial; far
  less with the parallel containers used for the baseline. Sanity-check against
  your measured baseline throughput before committing.

Override the fork location: `LAGVAL_DIR=/path/to/lag-validation ./run_rb32_contrast.sh run`.

### Running on a fresh machine (GitHub upload, multi-day run)

The canonical scripts are committed in this wrapper repo, so pushing it makes the
experiment portable. On the target machine:

1. Clone both repos: this wrapper repo (has `rb32-config-contrast/`) and the
   NebulaStream fork (has `lag-validation/` + `run-cell.sh` + `work/` templates).
2. Build the **lever-branch** `systest` (Docker `nebulastream/nes-development:local-rocksdb`,
   `cmake --build cmake-build-release --target systest`). The mirrors abort early
   if `strings $SYSTEST | grep pool_reserve_pct` fails.
3. Recreate the inputs the baseline used: the `rho_*.csv` files in
   `/dev/shm/nes-input` (RAM-backed) and the SD spill mount `/mnt/sd-spill`
   (or set `SPILL_HOST_DIR`).
4. `cd thesis/experiments/rb32-config-contrast` and run, pointing at the fork if
   the layout differs:
   ```bash
   LAGVAL_DIR=/abs/path/to/.../lag-validation ./run_rb32_contrast.sh run
   ```
5. The run is **resumable** (done cells are skipped), so it survives reboots
   across the multi-day window. When finished, copy `lag-validation/results/*_b32_*.tsv`
   back, then `./run_rb32_contrast.sh clean` on the runner.

Do **not** commit the generated `*_rb32.sh` / `run-cell-rb32.sh` files (they land
untracked in the fork). Only this wrapper-repo directory is meant for git.

---

## 6. Analysis plan → thesis integration

Outputs land in `lag-validation/results/*_b32_*.tsv` and load via the existing
`data.py` (campaign prefix unchanged; `b32` marks the arm).

1. **Pair** each `X_b32_…` with its baseline `X_…` on (cap, pool, ρ, arm).
2. **Completion** — Fisher's exact, `32/32` vs `16/8`, per cap. Test H1 (1024/2048)
   and H2 (512 crossover).
3. **Drain mechanism (H3)** — compare `io_w_b` / `spill_dir_mb` (bytes to SD) and
   the implied drain rate; expect ~35% fewer bytes at `32/32`.
4. **Secondary** — `tps`, `p99`.
5. **Deliverables** — feeds the planned Ch.4 RocksDB-characterization subsection
   (one in-text summary table) + Annex tables, and a paired completion figure
   per cap showing the crossover. Headline framing: **the optimal backend config
   is cap-dependent — memory-fit at the floor, compaction-friendly with headroom.**

---

## 7. Constraints & notes

- **Fork hygiene.** Canonical scripts + this doc live in the wrapper repo. The
  runner only ever writes **untracked** files into the fork's lag-validation dir;
  `clean` removes them. Do **not** commit them into the public NebulaStream fork.
- **inm-threshold cells are included.** `large_matrix` "in-mem" cells run with
  `spill.enabled=true` and very high thresholds, so RocksDB *is* opened (block
  cache reserved at the configured size). They are RocksDB-affected and kept;
  only `spill=off` cells are skipped.
- **Absolute numbers don't transfer from the microbench** (Docker SSD vs SD card,
  RocksDB in isolation). The microbench predicts the *direction*; this experiment
  measures the effect *inside NebulaStream under the cap*.
