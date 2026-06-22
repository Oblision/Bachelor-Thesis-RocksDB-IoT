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

3. **Write the Ch.4 RocksDB-characterization subsection** (placement already
   agreed: Ch.4 subsection + Annex tables, ONE in-text summary table). Cover:
   - the microbench characterization (256MB pilot → 512MB sweep; verified findings);
   - the memory-fit rationale for 16/8 at the floor (`SpillConfig.hpp:56`, E0);
   - **the RB32 contrast: config is cap-dependent** — memory-fit at 512, compaction-friendly (32/32) wins at 1024/2048 via lower SD drain load.
   - Full per-parameter tables → Annex.

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
