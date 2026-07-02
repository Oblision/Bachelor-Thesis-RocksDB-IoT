# Prose Review — Advisor + Lay-Reader Pass (2026-07-02)

Two-persona read of the full thesis on branch `thesis/advisor-revisions-2026-06-30`:
(a) a doctoral advisor checking logical soundness, unbiased result reporting, and
consistency against the data docs (DATA-AUDIT.md, EXPERIMENT-final-matrix.md,
ANALYSIS-RQ.md, memory snapshot 2026-06-11, and the harness scripts);
(b) a reader with no systems background checking the red thread and readability.

**Verdict.** The story arc is sound and the negative results are already reported in
most places (levers converted nothing, backpressure converted nothing, growth signal
insufficient, rb32 refutations, the 256 MB pilot, r1000). The fixes below are:
2 factual corrections against the data, ~6 internal consistency repairs, a tone pass
that removes self-evaluating labels, ~25 grammar/clarity repairs, EN-US spelling
normalization, one new figure, and the fork-point statement. All implemented in this
commit unless marked OPEN.

---

## A. Factual corrections (checked against data/harness)

### A1. Workload skew and key universe are wrong in §5.1 (setup)
- Thesis says: Zipf skew **1.1**, universe **100,000** ids (5Experimental.tex:19, tab:workload).
- These are the *defaults* of `gen_skew.py` (`--keys default=100_000`, `--zipf default=1.1`), not the campaign values.
- What actually ran: **s ≈ 1.0, 1.5 M-key universe, top-10 keys ≈ 20 % of traffic** — per
  (i) every figure's `\methodsnote`, (ii) memory snapshot `project_thesis_scope_anchor.md`
  ("Empirical recipe at W=50k: keys=1.5M, zipf=1.0 … top-10 ≈ 20 %"), and (iii) the
  HMGAUGE census (26 M entries is impossible with a 100 k universe: 100 k × 80 windows = 8 M max).
- Fix: §5.1 text + Table `tab:workload` → skew 1.0, universe 1.5 million.

### A2. W and L units: event-time milliseconds, not tuples
- Thesis says "window length W = 50,000 **tuples**, emit lag L = 4 million **tuples**" (5Experimental.tex:83); `\methodsnote` says "tumbling window 50k **tuples**"; Appendix D says "**tuples** per tumbling window".
- The harness disagrees: `genfile.test` — "`${WINDOW}` tumbling window size (event-time units / ms)"; the query itself: `WINDOW TUMBLING(ts, size ${WINDOW} ms)`; `run-cell.sh` computes `N_w = EMIT_LAG/WINDOW` on the same units.
- Fix: W = 50,000 ms of event time; L = 4,000,000 ms; D likewise. Applied in §5.1, `tab:workload`, `\methodsnote` (Template.tex), Appendix D.

### A3. "ten-million-key working sets" (§5.4, 7.3.3)
- Universe is 1.5 M keys, so 10⁷ *keys* is impossible; the quantity is key×window **entries** (census: 26 M entries). Fix: "ten-million-entry working sets".

---

## B. Internal consistency repairs

### B1. 12/16 vs 11/16 (§5.7 RQ3, [7.6e] vs [7.6q])
[7.6e]: "pooled lever arms complete 12 of 16" (= 5/8 + 7/8, the ladder arms).
[7.6q]: "Pooling with levers reaches 11 of 16" (= 4/8 + 7/8, the confirmation runs; per EXPERIMENT-final-matrix.md:86,143). Both true, different arm sets — but a reader sees a contradiction. Fix: [7.6q] now names both numbers and their sources.

### B2. Pooled-wins-at-ρ≈2 vs "high-ρ territory belongs to heap" ([7.6p])
Adjacent sentences look contradictory. The win is r200 (ρ ≈ 2.0); the regression is **r150 at the 16k pool (ρ ≈ 2.2)**, confirmed in EXPERIMENT-final-matrix.md ("512/16k/r150 (regression) | 8/10 | 1/8", U8 7/8). Fix: name the cell and ρ in both sentences.

### B3. Ladder text vs Figure fig:ladder ([7.6e])
Text lists 5 arms (0/10, 0/16, 0/8, 5/8, 7/8); the figure's second rung is the granularity-only arm (3/8), which the text omits until §disambiguation. Fix: one bridging sentence at [7.6e].

### B4. "At 1024 MB it reaches ρ ≥ 1.54" ([7.2.3] + tab:windows)
"≥" reads as "everything above 1.54 works", which is false (r1000). ANALYSIS-RQ.md: "up to ≥1.54 (r300, the largest tested that fits the envelope)". Fix: "extends at least to ρ = 1.54 (r300, the largest pressure tested at this cap)"; table: "up to ρ = 1.54 (largest tested)".

### B5. Knife edge [7.6a] vs heatmap caption
Text: "pools of 16k and 32k buffers complete"; caption: 10/10, **6/10**, 0/10. Fix: give the fractions in the text.

### B6. Architecture naming drift (heap/unbounded/default vs pooled/bounded/corrected)
Six synonyms across Ch. 5. Fix: one sentence at the start of RQ1 declaring the pairs; no other changes.

---

## C. Tone / unbiased-reporting pass

- "the **honest** edge / **honest** cost / **honest** caveat / **honestly** mixed" (4×, Ch. 5): self-evaluating labels; an advisor strikes these. → "the edge of the demonstrated result", "a real cost", "a caveat of its own", "remains mixed".
- "RQ3 … **the strongest chapter**" (§5.8 text + tab:verdicts): self-praise + wrong word (it is a section). → "the deepest investigation of the thesis" / "Met; the central investigation".
- Ch. 6 positioning claim "No published work evaluates…" → add "To the best of the author's knowledge," (standard hedge for a universal negative).
- Ch. 7 "one negative finding that an edge deployment **cannot design around**" contradicts the paragraph's own advice (choose a faster card…). → "that no engine-side mechanism can remove".
- Nexmark [7.2.8] "not merely an option but the only way" → "the coordinate where only out-of-core processing completes the workload".

## D. Clarity for the non-expert reader

- Ch. 1: gloss "anonymous memory" at first use; define OOC backend before naming it; split two double-idea sentences; "IoT Sensors" → `\ac{IoT}` sensors; "whilst" → "while"; C3/C7 jargon softened ("storage ingestion" → drain to storage; "intra-slice bloat" → growth of a still-open window's slice).
- Ch. 2: "Which of the two allocation types **are** chosen" → "is chosen"; KV-store definition tightened ("stores a value under a unique key"); "persistent KV store database" → "persistent KV store".
- Ch. 4: requirement 2 split (was a 44-word sentence); §levers now ends with a bridge to its empirical verdict in §5.7 (they convert nothing alone — negative result surfaced earlier); emit-lag sentence untangled.
- §7.2.4: "hot band" defined at first use (~N_w concurrently open windows).
- [7.3.7] "Spill and restore latencies are distributions, not single numbers." was a dangling stub (the distributions are never shown). → tied to what the tables do report (tail vs mean).
- Growth-signal [7.6x]: "and throughout when sampled" → "and fires throughout the run when sampled".
- Ch. 6: "Its existence shows that the problem is open beyond Flink" → "WindFlow shows that the problem is being worked on beyond Flink".

## E. Spelling normalization (EN-US, majority form already -ize)

behaviour→behavior (Ch. 4 ×3, Ch. 5 ×2), summarises→summarizes, analyses(verb)→analyzes, colour(ed)→color(ed) (2 captions), amortise(s)→amortize(s) (×2), artefacts→artifacts, whilst→while.

## F. Additions

### F1. Fork-point statement (user request)
Nowhere did the thesis say which NebulaStream state the work builds on. Per `.planning/dev-eng/PREFLIGHT.md`: fork `Oblision/nebulastream-Out-of-Core-Stream-Processing`, branch `main`, commit `cc754a94`, 2026-05-20, clean tree, 752/752 unit tests passing before any change. → Added to §4.7 (Integration), one sentence + provenance.

### F2. System-overview figure (user request)
New conceptual TikZ figure `fig:system-overview` in §4.1: the path of window state through the components — source → workers → slice store (hot/cold slices, hash-map pages) → buffer pool vs heap → spiller (soft/hard thresholds) → RocksDB (memtable/block cache) → SD card, restore → emission; drawn inside the cgroup cap boundary. Complements the class-level integration diagram (fig:ooc-integration) with a data-flow view; same color legend (grey = existing, blue = this thesis, orange = storage).

---

## G. Checked and left unchanged (flagged for the author)

1. **1,938 paired cells** ([7.1.4], [RB32-1]): Appendix E.6 rows sum to 1,226 pairs (+160 in E.7). If 1,938 counts something else (e.g. paired runs), consider stating the unit; number left untouched.
2. **Appendix F TODO** (`Annex.tex` comment): initial-draft folder link + public-link verification still pending — must be resolved before submission.
3. r500/r700@2048 fits-cells: correctly claimed as capacity, never as OOC wins (matches DATA-AUDIT transparency flag) — good as is.
4. Checksum coverage "two workload points per architecture" stated in threats ✓.
5. Session drift 4–17 % disclosed; all key contrasts within-session ✓.
6. German abstract content-equivalent ✓ (first sentence generalizes to "Stream-Processing-Systeme"; acceptable).
7. Repo hygiene: `Template_test.*`, `chk2.*`, `_verify.*`, `_compile_check.*` build leftovers in `thesis/latex/` — consider pruning before submission (not touched here).

---

# Addendum (same day): second round — figure fixes + resubmitted advisor list

## Round-2 fixes (user reports)

- **Figure "race"** decluttered: three in-plot prose boxes → three short region tags; legend moved above the axes (collision removed); ceiling label horizontal; suptitle made descriptive ("Cell outcomes across ingest rate and state pressure ρ").
- **Figure "heatmap" gap-fill restored**: the session-regenerated PDFs had lost the 32/32 gap-fill reps (data.py was already correct; the PDFs were stale). All 14 data figures regenerated from `make_all` scripts; heatmap cells read /16–/20 again. New clarifying sentence in the caption **and** §5.1 setup: figures pool the backend-size twins; headline statistics use the 16/8 arms alone.
- **Listing 1 (SQL query)** was overflowing the page unwrapped; global `\lstset` added (tt font, breaklines, fullflexible columns, upquote, thin frame).
- **New Ch. 2 figure** `fig:nes-components`: unmodified NES components (workers, slice store, pages, pool vs. heap, watermark, emission); referenced from §2.2; caption bridges to the §4.1 overview figure.

## Resubmitted advisor meeting list — status map

Already covered by the overhaul branch (verified this session): p-values figure (Fig. 4); OOM in abbreviation list + defined in §2.5; RQ4/5 no longer "additional"; "sweeps those knobs" gone; db_bench evaluation moved to §5.2 and runs first in Ch. 5; RocksDB parameters surveyed in §2.3 + justified selection table in §4.4; top-down solution presentation with iterations as alternatives (§4 intro); workload-as-query with LaTeX listing; SD/SSD device study + core-count experiment in future work (§7.6); non-monotone 6/8-vs-8/8 completion explained with small-sample variance + Wilson intervals (§5.3); heatmap carries per-cell failure counts ("5 ok / 20 OOM"), dual-mode cells hatched, old Figure 3 retired into it; ρ explained in captions with MB equivalents; drain-ceiling line documented as empirical, ρ=1 as exact by definition; composition caption clarifies control-block spill + names the census experiment (r150, 996 maps); IoT ingestion/SD-variability negative finding highlighted in §7.1; "role-blind" wording absent from Fig. captions (plain-language deadlock description); rb32 compartment figure explains directories-as-NES-buffers; §5.8 summary written out.

Done this session: statistics test definitions removed from Ch. 2 (now only §5.1 [7.1.8], next to what they test); "characterization" → experimental analysis (Appendix E title/intro, §5.1 reference); conclusion-style figure titles reworded (fig_effectiveness, fig_race); sentence added under requirement "Third" (byte-identical results + pointer to verification); JIT-probe reasoning added to fig:ooc-integration caption (compiled code reads raw pointers → state must be resident before the probe runs); JIT/CSV/SSD added to abbreviations; pooled-vs-heap heavy-end throughput mechanism named in §5.3 (allocation stalls, pointer to §5.5 idle share); system-graphic extension gap → two new diagrams (Ch. 2 baseline + §4.1 extended overview).

Open / author's call: Ch. 1 + Ch. 3 "1:1 an der Vorgabe" — template skeleton prescribes 1.1 Motivation / 1.2 Research Challenge; current Ch. 1 adds Results-in-Brief + Contributions + Structure (standard practice, and the skeleton itself says structure "should be customized"). Confirm with advisor whether stricter conformance is wanted. Ch. 4 forward references to Ch. 5 sections remain where they are pointers rather than dependencies ("kann auch": §4.8/§4.9 stay in Ch. 4). "Corrected flag" repetition reduced but not eliminated everywhere.

## Round 3: structural conformance + content reorganization (user decision)

- **Ch. 1 = skeleton 1:1**: §1.3 "Results in Brief" → "Novelty and Impact"; the novelty paragraph moved there from Motivation; the headline numbers stay as impact evidence.
- **Ch. 3 = skeleton 1:1**: §3.1 "Goal" → "Problem Statement", opening now states the problem before the goal.
- **Ch. 4 presents one final design, not a chronology**: "Design Iteration Two/Three/Four" → "Pool-Pressure Levers" / "Allocation Backpressure" / "The Growth Signal and Page Release"; intro and §4.1 reframed top-down (complete solution first, per-component detail after); each mechanism keeps its "the simpler design falls short because…" justification. All "iteration" cross-references in Ch. 1/5/7, Annex D, and the final-status figure updated.
- **No evaluation in Ch. 4**: RocksDB knob selection (incl. Table tab:rocksdb-config) merged into §5.2; Evaluation Harness & Reproducibility (incl. Table tab:edge-profile) moved to open §5.1. Labels `sec:rocksdb-config`/`sec:reproducibility` ride along, so every existing reference resolves. Ch. 4 ends with a bridge into the evaluation.
- Skeleton deviations that remain deliberate: Ch. 4 section titles are customized (template allows this); §4.1 "Solution at a Glance" has no skeleton counterpart but serves the top-down presentation.

## AI-usage / declaration updates

- Session archive entry: `nebulastream-…/docs/ai-usage/08-revising-thesis/2026-07-02-advisor-review-figures-pass.md` (verbatim prompts; transcript export to attach on sync).
- Declaration: Revising-phase level raised 3 → 4 (this session implements revisions directly) — **confirm before signing**; new Reflection section added.
- Appendix F TODO updated with the concrete pre-submission sync steps.
