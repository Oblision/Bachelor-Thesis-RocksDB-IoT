# TODO — after the RB32 (32/32) runs complete

**Persisted here (wrapper repo → GitHub → all devices).** Also mirrored in local
Claude memory (`rocksdb-characterization-and-rb32-contrast`). If you want it on
phone/iPad too, relaunch a session with `cv` and ask to copy it into the Obsidian
vault under `projects/Bachelor Thesis/`.

**Status (2026-06-22):** experiment designed + built + tested. Runs are PENDING —
to be executed on another machine over a few days (`./run_rb32_contrast.sh run`).

---

## The remaining task (do this once the runs finish)

1. **Collect results.** Copy `lag-validation/results/*_b32_*.tsv` back from the
   runner. Confirm cell count and reps (skip-log reports executed count).

2. **Pair + analyze (16/8 vs 32/32).** Load via `data.py`; pair `X_b32_…` with
   `X_…` on (cap, pool, ρ, arm). Compute:
   - completion fraction + Fisher's exact, per cap (test H1 at 1024/2048, H2 at 512);
   - drain mechanism: `io_w_b` / `spill_dir_mb` (SD bytes) and drain rate (H3, expect ~35% fewer bytes at 32/32);
   - secondary: `tps`, `p99`.

3. **Slot the RB32 results into the EXISTING Ch.4 subsection.** The subsection is
   already WRITTEN (2026-06-22) from the microbench alone:
   `4Solution.tex` §4.3 `\section{Configuring the RocksDB Backend}` (sec:rocksdb-config),
   in-text summary `tab:rocksdb-char`, and Annex "Appendix E: RocksDB Characterization
   Tables". It covers the 256MB pilot → 512MB sweep findings, the memory-fit rationale
   for 16/8 at the floor, and the cap-dependent story — but the larger-cap gain is
   currently framed as **future work** (marked LaTeX comment slot: search
   `% RB32 contrast (32/32 vs 16/8) result slots in here once run`).
   Remaining once runs finish:
   - replace the "left to future work" sentence with the measured 32/32-vs-16/8 result
     (Fisher per cap: H1 at 1024/2048, H2 crossover at 512);
   - add a paired completion figure per cap + the drain-bytes (H3) evidence;
   - extend Annex Appendix E (or add a sub-table) with the NES-level paired numbers.

4. **Cross-references.** §2.3 is now open (no config stated); point the Ch.4
   subsection at the open prior-studies framing, and reconcile with Ch.5:38
   (which states the fixed eval config) — add a back-ref so Ch.5's config is
   justified by the Ch.4 characterization.

5. **Writing style:** concise, one idea per sentence, present tense (thesis voice).

---

## Pointers

- Design + how to run: `DESIGN.md` (same dir).
- Runner: `run_rb32_contrast.sh` ({list|gen|run|clean}); shim: `run-cell-rb32.sh`.
- Baseline data: `nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/results/`.
- Microbench source: `Microbenchmark-BA/` (RESEARCH_LOG.md, constrained_512mb/PRELIMINARY_REPORT.md).
