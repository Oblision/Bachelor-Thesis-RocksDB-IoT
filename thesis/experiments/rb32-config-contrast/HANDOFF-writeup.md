# HANDOFF — RB32 (32/32) contrast: analysis complete, thesis write-up pending

**For the next session.** Everything needed to write the RB32 results into the thesis. Run
data is final; one small control re-run is finishing (§6). Companion files in this dir:
`DESIGN.md` (why/how), `analyze_rb32.py` + `rb32-analysis.txt` (paired analysis), `drift_control.sh`
(the control re-run), `TODO-after-runs.md` (original post-run plan).

## 1. The experiment (second experiment) — what & why
A/B on ONE variable: RocksDB `write_buffer`/`block_cache` **32/32** vs the thesis baseline **16/8**
(partitions, levers, pools, workloads all held identical). The thesis fixed 16/8 as the *memory-fit*
point for the tight 512 MB cap; the microbenchmark said 32/32 is throughput/compaction-optimal once
there's headroom. Question: does 32/32 help at the expanded caps (1024/2048)? Pre-registered
hypotheses (DESIGN.md): H1 (32/32 wins at 1024/2048), H2 (32/32 loses at 512 — the expected
crossover), H3 (mechanism = ~35% fewer bytes to SD). Run COMPLETE 2026-06-24, 1954 cells, 1938 paired.

## 2. Verdict — results are NOT what we expected (the interesting part)
The 32/32 effect is **architecture-dependent**, not a simple cap-crossover. Sign flips with whether
the window maps live **in the buffer pool** (N1 fix, `operator_buffer_size`=8192) or **on the heap**:

| cap=512 completion (paired, McNemar) | Δ(32/32 − 16/8) | p |
|---|---|---|
| W_ final_matrix (POOLED) | **+15%** | 0.002 |
| N_ lever_matrix_n1 (POOLED) | **+33%** | <0.001 |
| U_ u8_arm (HEAP control, opbuf 8128) | **−38%** | <0.001 |

- **H2 REFUTED** for the pooled (intended) store: 32/32 *wins* at the tight 512 cap (opposite of the
  prediction). H2's tax-mechanism holds only in the HEAP arms (U_/L_), where 32/32 loses.
- **H1 not significant** in W_ (1024 +4% ns, 2048 +0% ns) — 32/32 is a wash once there's headroom.
- **H3 partial/weak**: bytes-to-SD (`io_w_b`, both-ok W_) only ~11% fewer at 1024 (0.89×, p<.001),
  *contradicted* at 512 (1.60×), neutral at 2048 — far below the microbench's ~35%. Component-level
  gains did not transfer to system-level under the cap (DESIGN §7 anticipated this).
- Secondary: 32/32 ≈ 9% lower tps (0.91×), p99 unchanged.

## 3. Narrative framing (user's, sharpened — KEEP TWO KNOBS DISTINCT)
The heap misconfiguration (maps-on-heap; the N1 finding that the pooled-pages mechanism never engaged
until `operator_buffer_size`=8192) created the memory pressure that made shrinking RocksDB to 16/8
look necessary. Once the store is actually pooled, the original 32/32 proposal is vindicated — but
**only when memory is tight**; with headroom it's neutral (matches the data). Two independent knobs,
never conflate: (a) RocksDB sizing 16/8↔32/32; (b) buffer-page architecture heap↔pool. Headline claim:
**optimal RocksDB backend config is cap- AND architecture-dependent** — compaction-friendly sizing
helps the pooled store even under a tight cap, while its +40 MB fixed tax penalizes the heap store.

## 4. What MUST go into the thesis (gap list)
Content/analyses:
- The N1 architecture discovery as a first-class methodological finding (early runs were heap-only).
- A direct pooled-vs-heap contrast holding RocksDB config fixed (data: W_ 346, U_ 112, L_ 630).
- H3 honest partial/negative (microbench 35% did not transfer) — a component-vs-system integrity point.
- Pre-registration → refutation of H2 stated explicitly (makes the honest refutation credible).
- Nexmark (NX_) realistic-workload check: 32/32 neutral (68 vs 69%, ns) → reinforces "no help w/ headroom".
- Stats methodology: paired design, McNemar (paired) vs Fisher (pre-registered, underpowered); err-policy
  (rc=141 pool-exhaustion / rc=137 OOM cells are COUNTED, not dropped → no survivorship bias).
- **Tables of the runs used** (the analysis tables in `rb32-analysis.txt`) + a run-inventory table.

Memory-overhead story (user wants graphics):
- Compartment decomposition (pool floor + UNPOOLED bucket-directory "pointer bloat" = (chains+1)×8 B
  + Arena + RocksDB anon + base + file cache) — buildable NOW from TSV summary cols
  (`pool_used_peak_mb`, `unpooled_peak_mb`, `rocksdb_rss_mb`, `peak_anon_mb`, `peak_file_mb`), per cap,
  did-work vs didn't-work.
- Overhead-CLIMB-over-time line charts — RB32 run had SAMPLE OFF (no traces); the drift-control re-run
  (§6) captures them with SAMPLE=on for representative cells at 512/1024 (worked vs OOM).

For the "system that works" claim — the correctness journey:
- The bugs found & fixed (SliceCache raw-pointer spill-fence bypass / twin-defect, the rc134/rc11
  corruption) — the working config depends on these.
- Governor-v1 refutation (never-fires at valleys, self-deadlock) → motivates the stability levers.
- Stability levers 1a–1d — active in N_, explain N_'s 99% completion at 512.

Future work ("how to improve"): pool/bound the unpooled compartment (pool bucket dirs;
lower `max_number_of_buckets`; return-to-OS for chunks); demand-formula pool sizing.

## 5. Threats to validity (two NEW disclosures)
1. **Cross-session A/B.** 16/8 baseline ran 2026-06-12; 32/32 ran 2026-06-23/24 (≈11 days, different
   sessions). The usual within-session-paired mitigation does NOT apply across arms; session drift
   (the thesis's own 4–17%) is a cross-arm confound. (SD-card *state* is controlled — protocol is to
   fresh-format the card before each run — so that specific source is handled; the residual is
   everything else: thermal/freq scaling, background load, page-cache warmth, fragmentation.)
2. **Card was reformatted between arms** (same physical 128 GB card; it failed/disconnected and was
   reformatted ext4 on 06-23). Disclose; relevant mainly to H3 drain bytes. Mitigated by §6.

## 6. Drift-control + trace re-run (IN PROGRESS, started 2026-06-26 14:48)
`drift_control.sh`: 6 outcome-blind W_ points (512 + 1024 reference) × 3 reps × {16/8 (label `D16_`),
32/32 (`D32_`)}, interleaved per rep, SAME session + freshly-formatted card, `SAMPLE=on`. Gives:
(1) DRIFT: pair `D16_…` with the 06-12 `W_…` twin → how much of the gap is drift, not config.
(2) IN-SESSION A/B: `D16_` vs `D32_` → config effect free of the cross-session confound.
(3) TRACES: `work/rendered/D*.trace` for the overhead-climb graphics.
**RESULT (2026-06-26, `drift_analyze.py`):** drift is REAL and MATERIAL. At 512 the old-06-12 16/8
baseline completed only 1/12 of these cells; fresh-16/8 (same session/card) completes 4/12 — 3 cells
flipped (old failed, fresh ok), 0 reverse. So the old baseline was disadvantaged by session state; part
of the original cross-session "+15% at 512" was drift, not config. IN-SESSION 32/32 still edges 16/8
(6/12 vs 4/12, 2 flips to 32/32, 0 reverse) but McNemar p=0.50 — UNDERPOWERED (12 cells/cap, 2 discordant).
Footprint STABLE across sessions (peak_anon −0.4%, peak_mb −0.3%) → memory mechanism robust; only marginal
completion + tps drift. CONSEQUENCE: soften the absolute 512 claim; lean on (a) the architecture sign-flip,
which is a diff-in-diff (both b32 arms same 06-23 session, both old arms 06-12 → drift largely cancels in
the CONTRAST, so it is far more robust than the absolute number) and (b) the stable footprint mechanism.
Traces captured in `lag-validation/traces/D{16,32}_*.trace` (36 files, ~50 rows each) → graphics unblocked.
**POWERED RESULT (in-session 512 full grid, n=80, 2026-06-26 19:11; `insession_512.sh` → `rb32-drift-insession.txt`):**
DRIFT is SMALL — old-06-12 16/8 = 22/80 (28%) vs fresh 16/8 = 26/80 (32%), 6 fresh-wins / 2 old-wins,
McNemar p=0.29 ns (the pilot's 1-vs-4 was noise). Footprint stable (peak_anon −0.3%, peak_mb 0%); only
throughput drifts hard (tps −49%). IN-SESSION 16/8 vs 32/32 @ 512: 26/80 (32%) vs 32/80 (40%), 11 cells
flip to 32/32 vs 5 to 16/8, McNemar **p=0.21 — DIRECTIONAL but NOT significant**.
**FINAL CLAIM (downgraded, honest):** drop "32/32 significantly wins at 512"; state instead that 32/32
*consistently trends* toward better completion under tight memory in the POOLED store (cross-session +15%
and in-session +8% agree in direction), but is **not individually significant** once session is controlled.
ROBUST claims unchanged: the architecture sign-flip (pooled benefits / heap penalized — diff-in-diff,
drift cancels), neutral-with-headroom (1024/2048), and the stable footprint mechanism. Deliberately did
NOT run a larger A/B to chase p<0.05 (that would be confirmation bias).

## Figures (built here 2026-06-26, `fig_rb32.py`, venv `~/.venv-thesis-figs`) — in `thesis/latex/img/`
`fig_rb32_insession512.pdf` (drift-free 512 A/B), `fig_rb32_architecture.pdf` (sign-flip W_/N_/U_/L_),
`fig_rb32_compartments.pdf` (pool/unpooled/RocksDB/base per cap, ok vs fail), `fig_rb32_trace512.pdf`
(anon-over-time 16/8 vs 32/32), `fig_rb32_drain.pdf` (io_w_b H3). Rebuild: `~/.venv-thesis-figs/bin/python fig_rb32.py`.
REFINE on the other machine: add a POOLED-vs-HEAP compartment panel (U_/L_) to *show* the unpooled "pointer
bloat" — in W_ (pooled) unpooled is small by design; the bloat is visible in the heap arms.

## 7. Thesis rewrite map (additive, localized — NOT a from-scratch rewrite)
Substantive changes from **Ch.4 §4.3 onward**; light one-line touches earlier.
- Ch.1 §Contributions + §Results in Brief: one line each on the cap-AND-architecture-dependent finding.
- Ch.3 §Research Questions / §Scope: note RB32 as an additional characterization (keep RQ1–3 core).
- **Ch.4 §4.3 "Configuring the RocksDB Backend"**: replace the "left to future work" sentence at the
  marker `% RB32 contrast (32/32 vs 16/8) result slots in here once run` with the measured result;
  this is the main config-characterization home.
- **Ch.5**: new results subsection for the RB32 paired analysis (+ run tables); add the memory-compartment
  decomposition + over-time graphics; extend §Threats to Validity (the two items in §5 above); state the
  stats methodology + err-policy; back-ref the fixed-config statement (Ch.5 setup) to the §4.3 characterization.
- Ch.7 §Lessons Learned + §Future Work: the architecture-dependence lesson + the unpooled-compartment improvements.
- Annex (Appendix E: RocksDB Characterization Tables): add the NES-level paired numbers.

## 8. Decisions (user, 2026-06-26)
- Match-baseline-per-driver (heap arms stay heap) — DONE in the run.
- Focus on key objectives: RQ1/RQ2 core; the architecture finding is a **characterization subsection**
  (Ch.4 §4.3 config + the Ch.5 memory-footprint mechanism), not a headline that overshadows the core.
- Do the drift-control + SAMPLE re-run — DONE/in progress (§6).

## 9. Pending
- [ ] Analyze drift-control (D16 vs 06-12 W_; D16 vs D32 in-session) → drift magnitude + confirmed verdict.
- [ ] Build memory-compartment decomposition (static, from TSVs) + over-time climbs (from D*.trace), per cap.
- [ ] Write Ch.4 §4.3 result + Ch.5 results subsection + threats + Annex E (per §7 map).
- [ ] Add the correctness-journey + levers + future-work paragraphs (§4).
