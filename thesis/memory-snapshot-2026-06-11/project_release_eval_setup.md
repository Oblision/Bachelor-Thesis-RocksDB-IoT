---
name: project-release-eval-setup
description: Release-build evaluation setup for the OOC thesis final experiment — how to build/run it, the rate-limit-vs-CPU-cap pivot, the 2-core sweet-spot finding, and the working RQ1/RQ2 contrast config (2026-06-08).
metadata:
  node_type: memory
  type: project
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

Final-experiment infrastructure for [[project-thesis-scope-anchor]] (the 5 RQs). Built 2026-06-08 in `.planning/eval-phase/lag-validation/`.

**Release build (REQUIRED for perf/RQ2-5; debug only proves the mechanism).**
- `cmake-build-release` = RelWithDebInfo, `-DNES_LOG_LEVEL=INFO`. Build in the dev container: `docker run --workdir /workspace -v $REPO:/workspace nebulastream/nes-development:local-rocksdb cmake -G Ninja -B /workspace/cmake-build-release -S /workspace -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_TOOLCHAIN_FILE=/vcpkg/scripts/buildsystems/vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-linux-none-local -DLLVM_DIR=/clang/lib/cmake/llvm -DMLIR_DIR=/clang/lib/cmake/mlir -DNES_LOG_LEVEL=INFO ...` then `cmake --build ... --target systest`. ccache makes incremental rebuilds ~minutes. LLVM/MLIR prebuilt at `/clang` (NOT rebuilt). Binary: `cmake-build-release/nes-systests/systest/systest`.
- **Impact vs debug: base RSS 195→124 MB; throughput ~19k→~1.5-3M tps (~80x).** The `-O0` AND the per-tuple `NES_LOG_LEVEL=TRACE` were BOTH huge killers. INFO keeps the RQ metric lines (they are `NES_INFO`).

**Ingest-rate control: file-source rate-limit ABANDONED → use CPU cap.**
- Added a byte-rate pacing param to the File source (`SOURCE`.EMIT_RATE_BPS) but it is UNSTABLE: sleeping inside `fillTupleBuffer` holds a TupleBuffer, and under OOC spill back-pressure that DEADLOCKS the pool (a container hung idle 6h at 368MB) + intermittent heap corruption (`rc=134`). Keep `EMITBPS=0` (pacing off; binary is stable that way). User pivoted to CPU-cap 2026-06-08.
- **Throttle ingest via Docker `--cpus` (DCPUS env in run-cell.sh).** Proposal mandates strict CPU limits anyway; a dual-core cap is edge-faithful.
- **🔑 NON-MONOTONIC FINDING:** OOC survival vs CPU is NOT monotonic. `--cpus=4` → OOM (ingest ~6M tps outpaces SD spill drain); `--cpus=1`/1-thread → OOM (the spiller is CPU-starved — it competes with ingest for the one core); **`--cpus=2`/2-threads → SURVIVES** (ingest slow enough AND the spiller gets its own core). Sweet spot = 2 cores. This is an RQ3/RQ4 result, not just calibration.

**WORKING RQ1/RQ2 contrast (release, cap=512, SD card), 2026-06-08:** `--cpus=2`, 2 worker threads, W=50k, emit_lag=2M (N_w=40), pool=14000, soft=50/hard=100, P=4, lz4 wbuf16/cache8, tmpfs input, SD spill. At ρ≈1 (cal_rho, peak state ~445-531MB): **OOC survives rc=0 (55 slices, store 70MB, tps 3.0M, per-task p99 157µs); in-mem-capped OOMs rc=137; uncapped baseline tps 3.46M, p99 124µs.** ⇒ RQ2 ≈ 13% throughput overhead, ~27% tail-latency overhead.

**Data path = RAM-in → SD-out (no HDD).** Input CSVs on tmpfs `/dev/shm/nes-input/` (bind-mounted `/input:ro`); spill on SD card `/mnt/sd-spill`. (User caught HDD usage 2026-06-08.) Caveat: the systest binary still loads from `/workspace` (HDD) at startup (one-time, cached) — RQ4/RQ5 disk metrics use RocksDB counters (`bytes_written` etc.), not aggregate cgroup io.stat, to stay clean.

**Metrics (all in run-cell.sh output TSV, cols 16-22 = cpu_usec, io_rbytes, io_wbytes, lat_p50/p95/p99_us, tps):**
- Per-task latency: built-in `PercentileTaskLatencyListener` (always-on, `NES_INFO`, "Task latency us: p50.. (per-task)"). FREE. CAVEAT: task-granularity not end-to-end (NES authors document this); OOC-vs-baseline delta = spill-stall overhead.
- CPU: cgroup `cpu.stat usage_usec` delta. Disk: cgroup `io.stat` + RocksDB stats line. Correctness: Checksum sink variant `work/genfile_checksum.test`.
- `run-cell.sh` envs: TEMPLATE, FILECSV (tmpfs path), EMITBPS (keep 0), DCPUS, SYSTEST, CELL_TIMEOUT (default 900s guard — added after the 6h hang).

**ρ axis = per-window CARDINALITY** (NOT emit_lag — that hits the ~300-buffer/window pool-floor wall). ρ measured = uncapped-arm `store_peak`/cap. CSVs via `work/gen_skew.py` (Zipfian s=1.0, small lateness D=100k, bursts via --burst-width). Matrix driver: `matrix.sh` (ρ∈{0.5,1,2} × 3 arms, 3 reps on perf arms). See [[project-thesis-scope-anchor]] for the design + [[project-ooc-mechanics]].
