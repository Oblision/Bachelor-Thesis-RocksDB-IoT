# E2/E3/E4 — execution plan (post-E1 instrumentation)

Goal: finish all technical eval work up to (not including) the thesis prose → raw data + figures answering
RQ1/RQ2/RQ3 + the §3.3 OOM-survival gate. Then a 5-page HTML summary.

## E2 — harness (extend, don't rewrite)
Base: `phase2e/run-rss-part.sh` (capped systest + cgroup memory.peak + `Spill metrics:` grep + TSV).
Extend with:
- grep the NEW E1 lines: `RocksDB stats:` (write_amp, sst_footprint) and `Task latency us:` (p50/p95/p99) +
  the now-populated `restored … bytes` on P>1. (Finalize field names against E1 impl done-notes.)
- a workload generator: parameterize `state_size` via distinct-keys-per-window (and the `emit_lag` band).
  ~41 B/key store-internal, ~150–170 B/key RSS (from E0-REDO calibration).
- an E3 driver (`run-matrix.sh`) = rep loop over the matrix below, append one TSV row/run, status incl. oom (rc 137).
- throughput computed EXTERNALLY in the driver: known_generated_tuples / elapsed (E1-PR4, no SUT code).

## E3 — matrix (SCOPED; the plan sanctions trimming threshold-sensitivity + W2 first)
Fixed RocksDB config (microbench-recommended): lz4 / write_buffer 32 MB / block_cache 32 MB.
Fixed env: `--memory=<cap> --memory-swap=<cap> --cpus=<threads>`; pool 16000; soft 4 / hard 8; threads 4;
emit_lag large (retain a wide band). COMPILER mode.

**Axis A (core — answers §3.3, RQ1, RQ2 overhead, RQ3 scaling):**
- `state_size` ladder via keys/window: small → above-ceiling (final points from a quick E3 calibration probe).
- `variant` ∈ { **OOC** = spill on + P=8 + memory-capped ; **in-mem baseline** = spill off + NO cap, same cpus }.
  Baseline run at each state point capped at the SAME ceiling to find its OOM point (rc 137) → the §3.3.1 result.
- reps = 3 (bump noisy cells; CV check).
Caps: a primary cap (320 MB, the proven 2e demo cell) + 512 MB (the proposal's headline ceiling).

**Trimmed (documented as future/limited):** Axis B threshold-sensitivity, W2 skewed keys — run ONLY if Axis A
completes with time to spare. Bursty input = documented limitation (sequential Generator timestamps).

Rough core count: ~5 state pts × 2 caps × 2 variants × 3 reps ≈ 60 runs (baseline-OOM cells fail fast).

## E4 — analysis + figures
Small dedicated `analyze_ooc.py` (pandas + matplotlib) over the E3 TSV — the microbench `analyze.py` targets
db_bench's schema; concept-reuse (CV/variance check, percentile handling, the OOM-survival matrix) but write
fresh for our columns. Figures:
1. **OOM-survival matrix** (state × variant → completed/OOM) — the §3.3 headline (baseline dies where OOC lives).
2. **store_peak & process_peak vs state** (OOC) — the 1/P residency bound.
3. **throughput vs state** (OOC vs baseline where baseline fits) — RQ2 overhead.
4. **tail latency p95/p99 vs state** — RQ2.
5. **write-amp + on-disk footprint vs state** (OOC) — RQ3.
6. spill/restore bytes + latency vs state — RQ2 cost / RQ3.
All figures: generous spacing/margins, no overlapping labels.

## Deliverable
5-page HTML summary of the whole journey (Phases 0–2 + eval E0–E4) via `/frontend-design`, with well-spaced
diagrams + code blocks and simple, iterative explanations.
