# Ch. 5 Prose-Improvement Analysis (base f4bd577, 2026-07-04 → current working copy, 2026-07-14)

**Method.** Base = `f4bd577` (`thesis/latex/chapters/5Experimental.tex`, 1146 lines), the commit just
before the prose arc began (c3cc050 "rigor pass", 2026-07-05). Current = the improved working copy
`Abschlussarbeit_396953/chapters/5Experimental.tex` (1335 lines), which is repo HEAD plus a light
final copyedit (~17 changed lines). Full word-diff sampled section by section (`ch5_worddiff.txt`,
`ch5_unified.diff` in this folder); cross-checked against `thesis/REVIEW-PROSE-2026-07-02.md` and the
commit messages of c3cc050, d24165b, d0e6138, 75a9fca, 62a8a71, 7beff01, fdebd30, deca257, 38cddee.
All examples below are quoted from the actual diff.

## Summary table of patterns

| # | Pattern | Mechanism of improvement | Scale in diff |
|---|---------|--------------------------|---------------|
| P1 | Answer-first section openings | Reader gets the result before the evidence; no suspense | Chapter intro + RQ1–RQ5 sections |
| P2 | "In answer to RQn" closings + one-line bridge to next section | Explicit through-line; each section provably discharges its question | 5 new closing paragraphs |
| P3 | De-evaluative renaming (flagship→primary finding, win→completion, dies→fails, corrected→second, knife edge→narrow) | Removes self-praise and drama; verdicts come from data, not adjectives | ~60 sites |
| P4 | One name per concept (arm→configuration, architecture→allocation type, granularity→buffer size, drain→storage-bandwidth ceiling) | Kills synonym drift; reader never reconciles vocabularies | 68+ sites (commit-counted) |
| P5 | Self-decoding labels & taxonomies (workload label = footprint in MB; exit-code outcome table) | Notation carries its own meaning; no lookup burden | new §7.1.2b + tab:outcomes |
| P6 | Scope and statistics attached to claims | Honesty: what was tested, on how many runs, what is *not* demonstrated | ~20 sites; threats 7→9 |
| P7 | Mechanism written as a stepwise causal chain (often replacing a colon-compressed clause) | Reader can verify each causal link; nothing asserted by rhythm | deadlock, valley, throttle, recycling paras |
| P8 | Caption slimming: caption = decode, prose = argument | Figure readable standalone; analysis lives where it can be cited | ~10 captions, some 20→3 lines |
| P9 | Kill forward references / introduce-before-use | Linear readability; fig:pvalues physically moved to the summary | fig:pvalues, rb32→Appendix E.6, 16/8 shorthand |
| P10 | Colon-announcement removal; one claim per sentence (but evidence lists *merged*, not split) | Sentences make claims; colons reserved for data | ~15 sites |
| P11 | Contradiction pre-emption: name the apparent conflict, then resolve it | Cold reader's objection answered at the moment it arises | 4 major sites |
| P12 | Expectation-vs-measurement framing with unattributed mechanism list | Surprising number contextualized without over-claiming | §5.9 cost paragraph |
| P13 | Numbers re-audited against raw data; honest rounding | Trust: every figure matches the run data | ~10 corrections |
| P14 | One evidence basis per figure (de-pooling mixed arms) | No footnote arithmetic ("counts read out of 16 or 20") | heatmap family |
| P15 | Structural: monolithic RQ1 split into 5 named subsections with a roadmap | Each finding gets an addressable home; navigation instead of scrolling | §5.3.1–5.3.5 |

---

## P1. Answer-first section openings

**Before (RQ2 opened with a methods caveat):**
> All headline cost comparisons in this section use matched \ac{IO} granularity: about 8{,}000-byte buffers in every variant, including the in-memory reference. The reason follows in the granularity finding below.

**After:**
> RQ2 asks what the backend costs are in terms of throughput and tail latency, and what spilling and restoring state cost on their own. At a matched buffer size, the answer is a throughput cost of 11 to 15 percent, a 99th-percentile latency of 1.1 to 1.6 times that of the in-memory engine, and a resident footprint up to 33 percent smaller under the pooled configuration.

**Also:** the chapter itself now opens with its verdict —
> Its central result is that, under the configurations tested, the \ac{OOC} backend completes workloads whose state exceeds the memory budget and which the capped in-memory engine aborts. This completion holds inside an operating window, and the chapter characterizes that window.

and RQ4/RQ5 gained parallel "RQx asks … The answer is …" openings.

**Why better:** the reader holds the conclusion while reading the evidence, so every subsequent number is read *against* a stated claim instead of accumulating toward an unknown one. Methods caveats demoted to the second sentence, where they qualify rather than lead.

## P2. Answer-close + bridge (the "roter Faden" seam)

**After (new, RQ1):**
> In answer to RQ1, the backend prevents the OOM failure the capped in-memory engine suffers, within a characterized window rather than unconditionally. … The next section measures the cost of this completion.

**After (new, RQ5):**
> In answer to RQ5, storage is used efficiently. … One question remains open after the four preceding sections: why runs fail at all; the next section investigates it.

**Why better:** each section provably discharges its research question (an examiner can check RQ-text against answer-text verbatim), and the bridge sentence makes the chapter's ordering feel necessary, not arbitrary. Commit 75a9fca states the intent: "replacing the previous lists of disconnected one-liners."

## P3. De-evaluative renaming

Verified corpus counts (base → current): `flagship` 8→0 in prose (survives only as file/label name), `win/winner/winning` 12→0, `knife` 7→0, `territory` 2→0, `corrected` 2→0, `survive/survivor` 8→2, `die/dies` as words 26→0.

- > Completion at the flagship cell (512\,MB) → **The primary finding at the 512\,MB cap** (and 38cddee renames it chapter-wide)
- > The winning cell pays 46--59\% → **The completing beyond-memory cell loses 50--63\%** (rename *and* data correction, P13)
- > r1000 at 2048\,MB … defeats every configuration → **b2078 … fails under every configuration tested**
- > The corridor has a knife edge: at one cell… → **The corridor is narrow. At one cell…** (figure `fig:knife` renamed `fig:tipping-point`)
- > Survivors complete below roughly 2.5 million tuples per second of ingest. Sprinters above roughly 2.7 million die. → **Completing runs ingest below roughly 2.5 million tuples per second. Runs whose demand exceeds roughly 2.7 million fail.**
- Verdict table: > RQ3: Met; the central investigation → **Met** (self-ranking removed)
- > The one-flag correction changes two things → **The one-flag change alters two things** ("correction" presumed the flag was right before the data said so)
- > the proven reason → **the demonstrated reason**; "The model passes." deleted; "outside its training data" → **on data it was not built from** (drops borrowed ML jargon)

**Why better:** evaluative words pre-judge for the reader and invite an examiner to push back on the adjective instead of the data. The neutral verb ("completes", "fails") is also more *precise*: "dies" hid the three distinct failure modes that the new outcome taxonomy separates.

## P4. One name per concept (terminology unification)

- `arm` → `configuration` (35 hits → 0; commit 7beff01 counts 68 sites incl. captions)
- `heap/unbounded/default architecture` → **configuration / allocation type** (review item B6: "Six synonyms across Ch. 5 … one sentence declaring the pairs")
- `granularity` → `buffer size`; `drain ceiling` → `storage-bandwidth ceiling`; `backpressure` (overloaded) split into `queue pacing` / `allocation stall` / the refuted `allocation-backpressure lever`
- The synonym-bridge sentence added where both vocabularies must coexist:
  > For brevity, this chapter calls the first the \emph{heap} configuration and the second the \emph{pooled} configuration; where the tables and appendices name the same pair, they label them \emph{unbounded} and \emph{bounded}. (cold-reader flag T3)

**Why better:** a reader tracking "arm", "architecture", "variant" and "configuration" must decide whether they differ; unification deletes that decision. The `backpressure` split is scope honesty too: one word had covered a working mechanism and a refuted lever.

## P5. Self-decoding labels and taxonomies

- Workload IDs redesigned so the notation computes: r150 → s736, and a new paragraph explains
  > The number in the label is the workload's measured memory footprint in MB … \texttt{s736} has a 736\,MB footprint, so $\rho = 736/512 \approx 1.44$ at the 512\,MB cap. (62a8a71: "label numbers now ARE the footprint")
- New outcome table `tab:outcomes` maps exit codes to five named outcomes; prose stops saying "status 141" and says **exit code 141 (pool abort)**; "6/8 means 6 of that cell's 8 repetitions completed."
- The odd "8128" ladder tick decoded in its caption: > just under the 8192-byte page, so map pages stay on the heap (flag T5).

**Why better:** every later figure reuses these labels; front-loading one decoding paragraph converts dozens of per-figure lookups into zero. The b1374-vs-s1582 anomaly (P11) only *became explainable in one clause* because the label is the footprint.

## P6. Scope and statistics attached to claims

- Chapter claim scoped: "under the configurations tested"; and negative space stated: > at 2048\,MB, completion beyond the memory boundary is not demonstrated.
- New paragraph [7.1.5b]: > This fixed configuration makes the evaluation a targeted subset of the design space rather than a sweep of the entire space. … The findings are conditional on the fixed values…
- Per-section evidence basis pinned: > All figures and statistics in this section use the 16/8 RocksDB configuration … the separate 32/32 contrast in Appendix~E.6 is not included in any counts here.
- Threats to validity grew from **"Seven limits"** to **"Nine limits"**, adding the fixed-subset scope and: > each cell carries only 8 to 10 repetitions, so per-cell completion fractions carry wide 95\% confidence intervals (8 of 8 spans 68 to 100 percent) and must be read as estimates…
- Designed vs measured separated: > the 90\% margin is chosen to leave headroom for them rather than measured from them.
- Definition vs observation separated: > The $\rho = 1$ boundary in the figure is exact by definition. The storage-bandwidth ceiling near 2.6 million tuples per second is empirical…
- Survivor bias named where it flatters the result: > the heap configuration's lower cost is partly due to survivor bias, because it is measured only over the 6 and 3 heap repetitions that finished.
- Untested space marked untested: > whether a still larger pool would complete it under this cap is untested. / > The quantitative attribution remains a hypothesis because the six probes record no per-thread scheduling.
- Test citations added: Fisher \cite{fisher1935}, McNemar \cite{mcnemar1947}, Newcombe/Wilson \cite{newcombe1998}.

**Why better:** each claim carries its own boundary conditions, so no sentence can be quoted beyond what was measured. The survivor-bias and hypothesis-vs-measurement admissions cost one clause each and pre-empt an examiner's strongest objections.

## P7. Mechanism as a stepwise causal chain

**Before (deadlock, colon-compressed):**
> The residual failure is an allocation deadlock: the emitted state pins the pool, and the workers that would drain it are all blocked allocating.

**After (each link its own sentence):**
> The residual failure is an allocation deadlock (Figure~\ref{fig:gridlock}). Emitted but unprocessed window state holds the pool's buffers. Draining that state back out requires worker threads. Every worker thread is itself blocked waiting to allocate. The pool has no separation between fill-side and drain-side allocations, so fill-side allocations consume the buffers the drain side needs.

**Valley (the chain now explains the paradox before the number):**
> The first mechanism is the fast-ingest valley, and it explains why the heaviest load completes again after the lighter ones fail. At the lower $\rho$ points, the working set fits, so the pool fills late or not at all, and the sources are not throttled. … From then on, every allocation waits for a freed buffer, and buffers are freed at the rate the spiller writes to the device, so ingest is forced down to the storage-bandwidth rate; the run finishes slowly instead of failing fast. … The measured rates confirm this; the completing run ingests about 1.98 million tuples per second…

**Micro-version:** > The mechanism is recycling: → **The reason is that pooled pages return to the pool on spill and are reused.**

**Why better:** the colon version asserts the mechanism as a slogan; the chain version lets the reader verify each implication and puts the confirming number *after* the mechanism it confirms (mechanism-before-number ordering). Note 62a8a71 also upgraded a chain from speculation to "code-verified mechanism + unmeasured attribution" — mechanism claims get the same evidence discipline as result claims.

## P8. Caption slimming: caption = decode, prose = argument

**Before (fig:race caption, ~20 lines of analysis, excerpt):**
> …The vertical $\rho = 1$ memory boundary is exact by definition: … no $\rho > 1$ cell whose free-running demand exceeds about 2.7\,M tuples/s survives (40 of 40 fail). Above the ceiling and past $\rho = 1$, ingest outruns the drain, the spiller falls behind, and the run fails. This is the race. The $\rho < 1$ failures split by demand rate and carry their own two markers…

**After (caption is a legend decode):**
> …Color and shape give the outcome (green circle = completes, orange square = pool-exhaustion abort, red cross = OOM kill); fill gives the allocation type (filled = heap, hollow = bounded). The horizontal line marks the $\rho = 1$ memory boundary, the vertical line the storage-bandwidth ceiling near 2.6\,M tuples/s.

The evicted analysis reappears — expanded and corrected (40→25 of 25) — as body paragraphs [7.2.7]/[7.2.7b]. Same move on fig:gridlock ("The deadlock is this: …" removed), fig:composition (commit 75a9fca: caption "~20→9 lines … figure went from 0 to 4 citations"), fig:cpu-io, and the geometry-law family, where sibling captions collapse to > Same format as Figure~\ref{fig:geometry-law-512}. Caption *titles* now state the finding ("The completion valley at 512\,MB", "The primary finding") rather than parameters — deca257: "prose carries the argument, caption is definition."

**Why better:** analysis in captions is unciteable, uncorrectable in flow, and duplicates the text (creating drift risk — the 40-vs-25 error lived in a caption). A decode-only caption makes the figure self-readable and forces the argument into prose where it joins the through-line.

## P9. Kill forward references / introduce-before-use

- **fig:pvalues physically relocated** from early RQ1 (where it cited five contrasts the reader had not yet seen) to the Summary, with the rationale left in a comment: > % Placed here so every row refers backward to a contrast already developed. Its prose now glosses every row: > The \emph{attribution ladder}, completion at the decisive cell as pooling and its levers are switched on one factor at a time, rises from 0/8 to 7/8… (cold-reader flag T2)
- The rb32 backend-sizing subsection (5 paragraphs + 2 figures) moved out to **Appendix E.6**; every in-chapter mention downgraded to "as additional information; they are not folded into any count in this chapter." The base's forward-leaning caption > Section~\ref{sec:rb32} tests whether a larger backend helps once the cap grows → **Appendix~E.6 examines a larger backend configuration as additional information.**
- "16/8"/"32/32" shorthand moved to its introduction point (commit c3cc050).

**Why better:** a forward reference forces either a leap of faith or a page-flip; both break the linear read. The fig:pvalues move converts five faith-leaps into five confirmations.

## P10. Colon-announcement removal; one claim per sentence — but evidence merged, not split

- > The fixed configuration: the window length $W$ = … → **Every remaining parameter is pinned to one value. The window length is $W$ = …**
- > Configuration discipline: every knob is pinned explicitly… → **Every configuration knob is pinned explicitly in every cell.**
- > Regime one: the demand fits the pool; the run completes at full speed. → **In regime one, the demand fits the pool, and the run completes at full speed.**
- Run-on splits (38cddee "one claim per sentence"); the review's 44-word sentence rule; d24165b "split the unreadable base-footprint sentence into four."

**The instructive counter-direction:** staccato *fragments* were merged when they were one claim:
> Entry headers take 51\%. Payload takes 34\%. The bucket directory takes 14\%. Slack is below 1\%. → **The per-map gauge, decomposing the state at 26 million entries, attributes 51\% to headers, 34\% to payload, 14\% to the bucket directory, and below 1\% to page slack (Figure~\ref{fig:composition}(a)).**

**Why better:** the rule is one *claim* per sentence, not one *number* per sentence. A colon-announcement ("Discipline:") is a heading pretending to be prose; a four-fragment list is a table pretending to be prose. Both get rewritten into sentences that state who measured what. Note the retained colon in > The five-point ladder is reported in full: 0/10, 3/8, 0/8, 5/8, 7/8. — colons that introduce data survive; colons that announce topics die.

## P11. Contradiction pre-emption (new pattern, not in the requested list)

The edited text repeatedly names an apparent conflict *as the reader would form it*, then resolves it:

- New §5.9 paragraph: > Three of these rows involve the heap configuration, and read side by side they appear to conflict; at the same workload and cap, the heap completes the primary-finding contrast yet fails the allocation-type contrast. The two rows measure different things. …the rows differ in baseline and configuration and do not contradict each other.
- > One cell in panel (b) breaks the left-to-right order of $\rho$. b1374 at 1024\,MB ($\rho \approx 1.34$) fails on both configurations, while s1582 immediately to its right ($\rho \approx 1.54$, a larger footprint) completes 8 of 8… b1374 fails because its concurrently live state requires a 96k-buffer pool…
- > One baseline cell, s1034 at 1024\,MB, completes 10 of 10 although its uncapped $\rho$ is 1.01, because the uncapped reference run … uses about ten percent more memory than the same workload under the cap…

**Why better:** anomalies a reader can see in a figure but not explain are read as errors. Naming the anomaly ("breaks the order", "appear to conflict") signals the author saw it too; the resolution converts a credibility leak into a demonstration of understanding. This grew directly out of review items B1/B2 and the cold-reader exercise.

## P12. Expectation-vs-measurement framing (new pattern)

> DRAM and a consumer SD card differ by orders of magnitude in bandwidth and access latency, so a backend that moves window state between them could be expected to lose an order-of-magnitude drop in throughput. The measured loss is 11 to 15 percent… Four measured mechanisms close this gap. First, the operator reduces before it stores… How much each mechanism contributes individually was not isolated; the list states the measured effects without an attribution among them. The moderate cost therefore holds only while reduction, skew, and caching keep the byte stream to the device below the drain bandwidth.

**Why better:** a surprisingly good number, unframed, looks like an error or a cherry-pick. Stating the naive expectation, the measurement, the mechanisms, the non-attribution disclaimer, *and* the conditions under which the good number dies makes the result maximally believable.

## P13. Numbers re-audited against data; honest rounding

From d24165b (each verified against run data): 63%→**62%**; −46/−59%→**−50/−63%**; 1387→**1388\,MB**; "19/21"→**22 of 24 spilling cells**; caption "40 of 40 fail"→**25 of 25 fail**; "210 cells"→**210 runs**; > Roughly 20 corrupted cells → **roughly a dozen** (12 verified — rounded *down* to a word that cannot over-claim). ρ for r150 corrected 2.2→1.44 (a genuine factual bug).

**Why better:** the correction direction is not cosmetic — several went *against* the thesis's favor (−46/−59 → −50/−63 is a worse cost). That asymmetry is what makes the remaining numbers trustworthy.

## P14. One evidence basis per figure

**Before (heatmap caption):** > Grid cells pool the 16/8 baseline with its 32/32 backend-size twin (Section~\ref{sec:rb32}) as additional repetitions, so counts read out of 16 or 20; the headline statistics in the text use the 16/8 arms alone.

**After:** every figure restricted to the 16/8 baseline (commit 4ef37eb "restrict all data figures to the 16/8 baseline"); the caveat sentence disappears because the inconsistency it papered over disappeared.

**Why better:** the base asked readers to hold two counting bases at once and trust that text and figure differed benignly. Fixing the data selection deleted a permanent caveat — the strongest form of prose editing is removing the condition that required the sentence.

## P15. Structure: named subsections + roadmap

RQ1 went from one 290-line section to five subsections (Headline Contrasts / Operating Windows / Two-Sided Geometry / Where Each Allocation Type Completes / Completion as a Race) plus Nexmark, introduced by a roadmap:
> The subsections that follow establish what bounds this result. Section~\ref{sec:rq1-windows} maps the operating window of each allocation type cap by cap. Section~\ref{sec:rq1-geometry} derives the two-sided pool geometry…

The single 3-cap heatmap figure split into per-cap figures, each with its own reading paragraph ("At the tightest cap (Figure…), the in-memory baseline completes only…"). Same for the geometry-law family and the new three-figure valley family (curve / mechanism diagram / run-time trace).

**Why better:** findings acquire citable addresses (later sections say "Section 5.3.3 isolates this mechanism" instead of "see above"), and each figure is discussed adjacent to itself, satisfying the no-forward-reference rule structurally rather than sentence by sentence.

---

## Patterns from the brief that do NOT appear in this diff (verified)

- **Em-dash removal:** neither version contains prose em-dashes; the only `---` are empty table cells and LaTeX comments. The ban was evidently enforced before f4bd577; this window only *maintained* it (new prose uses commas and parentheses where an em-dash would tempt).
- **'indicative' → 'qualitative':** no literal replacement (base has neither word). But the rule shaped *new* text: the valley caption says > the naive tapering expectation (dashed, **qualitative rather than a fitted model**).
- **Labels off data lines:** a figure-script concern; not visible in this .tex diff (the related caption-side moves — legend decodes, finding-stating titles — are covered in P8).

## Counter-examples: prose the nine sessions deliberately left alone

1. **Pre-registration statement** — untouched verbatim across the whole arc: > Every follow-up experiment was preceded by a written hypothesis. These pre-registration documents are versioned in the repository. Already plain, factual, scoped; nothing to fix.
2. **The strongest evidence sentence** — untouched: > In 118 of 138 stalls, no buffer returned anywhere in the pool for two full seconds. Concrete quantities with a mechanism-relevant unit; the editing style *aspires* to this sentence, so it never changed.
3. **Full negative reporting** — kept verbatim, colon and all: > The five-point ladder is reported in full: 0/10, 3/8, 0/8, 5/8, 7/8. (Shows the colon ban targets announcements, not data enumerations; and 3/8-with-p=0.069 honesty was already in place.)
4. **The three-variant definition block** — untouched: > Three variants run on identical workloads. The in-memory engine without a cap is the correctness reference. The in-memory engine under the cap is the failure the backend must prevent. Already one-claim-per-sentence and parallel.
5. **Blunt negative results kept blunt:** "Bounding source admission changes nothing. Bounding the task queue changes nothing." and "converted nothing" survive every pass — the tone edits removed self-*praise*, never self-*criticism*. Likewise the confession anecdote: > An early sweep produced a fake optimum because one binary default, the page size, differed from the baseline. (only its framing sentence was tightened).
6. **Headline statistics never softened:** 14/24 vs 0/18, $p = 4.1\times10^{-5}$ appears in identical form in base and current; the de-evaluative pass changed the *words around* the numbers, never the numbers' prominence.

## Note on the final (uncommitted) working-copy pass

HEAD → working copy is a light copyedit (~9 sentence-level changes): precision upgrades ("which the capped in-memory engine **cannot finish**" → "**aborts**"; "Section 5.2 **fixes** the RocksDB configuration" → "**defines**"), hyphenation ("fixed-parameter subset"), and comma insertions. One systematic artifact: `32\,GB` → `32\, GB` (a space after `\,`) throughout — a typographic regression, likely from an external grammar tool, worth reverting before submission.

---

## Addendum: the newest pass (repo working copy → newly shared folder, 2026-07-14)

The shared `Abschlussarbeit_396953` folder carries one further, uncommitted polish pass over the repo copy (~525 changed lines). Its patterns, and an honest assessment:

**Improvements (keep doing these):**
- Precision verbs: "cannot finish" → "aborts" (names the observed failure mode instead of a vague negative); "fixes the configuration" → "defines the configuration".
- Formal register without inflation: "named throughout" → "mentioned throughout", "seen at" → "observed at".
- Correct list punctuation: comma before a two-item apposition → colon ("complete the configuration: a two-core budget and an SD card").
- Tighter noun phrases: "measures how efficiently storage is used" → "measures storage efficiency".
- Citation honesty: dropped a second citation (shi2016) where one source (ojo2019) actually supports the claim, and narrowed the claim to match it ("edge-class gateway devices").

**Regressions to watch (tool-suggested edits accepted too eagerly):**
- Comma insertions that add pauses without meaning: "the central constraint, and the only one varied"; "few sensors are hot, and many are rare".
- Spacing artifact: `32\, GB` (space after `\,`) introduced in several places; `\,` must abut the unit.
- Inconsistent math-mode percentages: `$20\%$` next to prose-mode `20\%` elsewhere.

Lesson for the skill: a final language pass should be diffed and re-read sentence by sentence; grammar tools optimize for pause-density and hedging commas, not for the one-idea-per-sentence rhythm the chapter uses.
