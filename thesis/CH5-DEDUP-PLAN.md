# Chapter 5 — Repetition Audit and Redesign Plan

> **Status: EXECUTED 2026-07-14, design B** (RQ3 moved after RQ1; Nexmark cost →
> RQ2; failure-breakdown figures → RQ3 §geometry-valley). All R1–R16 actions and
> mechanical defects applied; R11 resolved as two distinct experiments (0/8 =
> falsification arm, 3/8 vs 8/8 = confirmation run) and disambiguated in text.
> Chapter: 1334 → 1281 lines; verified with two clean pdflatex passes (100-page
> PDF, no errors, no undefined refs). Pre-restructure backup kept in the session
> outputs as `5Experimental_pre-restructure.tex`.

Audited: `Abschlussarbeit_396953/chapters/5Experimental.tex` (1334 lines, 2026-07-14).
Line numbers below refer to this version.

## Diagnosis: one root cause

The chapter runs RQ1 → RQ2 → RQ4 → RQ5 → RQ3, but the explanatory machinery
(geometry law, ingest–drain race, three regimes, deadlock) was *discovered* in the
RQ3 investigation and is *needed* to explain RQ1. The current draft resolves this
by explaining every mechanism fully in RQ1 **and** re-deriving it in RQ3, then
telling each headline a third time in the Summary. Almost every repetition in the
chapter traces to this.

**Rule for the rewrite: every finding gets exactly one home. Every other mention
is one clause plus a backward reference.**

## Recommended design (A)

Keep the section order. Redefine the division of labor:

- **RQ1 (5.3)** owns the *what* and the *laws*: operating windows, the two-sided
  geometry (5.3.3), the ingest–drain race and its pacing mechanism (5.3.5).
  The admission-queue evidence currently in RQ3 [7.6l-bis] moves here, next to
  fig:pacing-loop, which already describes it informally.
- **RQ3 (5.7)** owns the *why* and the *how-we-know*: the attribution ladder,
  the pre-registered falsifications, the deadlock diagnosis, disambiguation,
  crash cluster, growth signal, page release. It opens by saying the two laws of
  5.3.3/5.3.5 are the *products* of this investigation, then narrates the
  discovery order (justified — keep that justification). It never restates a law.
- **RQ2/RQ4/RQ5** own their numbers; RQ3 references them instead of repeating.
- **Summary (5.8)** becomes: two-sentence verdict + Table verdicts + fig:pvalues
  with a 2–3 sentence intro + the tension-resolution paragraph [7.7.3] + the
  cost-interpretation paragraph [7.7.4]. No per-RQ prose narration.

Alternative (B): move RQ3 directly after RQ1. Kills the RQ1→RQ3 forward pointer
at its root and lets 5.3 stay lighter, but demotes RQ3 from chapter climax and
reorders the intro's roadmap. ~90 % of the actions below are identical either
way. A is less invasive; B is cleaner on forward references. Decide before
executing.

## The RQ1 diet (main length lever)

RQ1 (5.3, lines 317–786) is ~470 lines, ≈40 % of the chapter, and pre-answers
the other RQs. Shrink it by relocating, not deleting:

1. **Nexmark cost → RQ2.** [7.2.10] (754–778, incl. fig:nexmark-cost) is a cost
   analysis: 42–61 % overspill loss, 50–63 % at the beyond-memory point,
   survivor-bias caveat. tab:cost in RQ2 *already has the Nexmark row* — the
   full treatment should sit next to it, as a short "transfer cost" passage.
   The Nexmark subsection in RQ1 keeps completion and the failure-model
   transfer ([7.2.8]–[7.2.9], [7.2.8b], [7.2.8c]) only. ~30 lines out of RQ1.
2. **Failure-breakdown figures → RQ3 (option).** The two-bounds statement
   (431–439) stays in 5.3.3 because the heatmap readings need it, but the three
   per-cap failure-breakdown figures fig:geometry-law-512/1024/2048 and their
   cell-by-cell readings (441–484, ~45 lines) answer RQ3's question ("why the
   remaining failures happen"), not RQ1's ("does it complete, where"). Moving
   them gives RQ3's [7.6a] a real home instead of deleting it (adjusts R2
   below), and RQ1 keeps only fig:tipping-point as the pool-sensitivity
   illustration. Cost: heatmap captions then point forward to 5.7 for the
   breakdown (pointer-type). Decide together with A vs B.
3. **Compress the below-ρ=1 bookkeeping.** [7.2.7b] (674–683), the two-group
   accounting of the 33 fitting-cell failures, carries one message: all
   mis-sized-pool effects, all recover at the geometry-correct pool. Three
   sentences suffice; the per-group counts can go to the appendix.
4. **Rate quantification stays out of RQ1.** 5.3.5 needs ingest rates only to
   establish the race; the utilization and drain-bandwidth consequences are
   RQ4's (which already links back correctly at 888). Trim clauses like 515
   ("visible as allocation-stall idle time in Section rq4") to bare pointers.

With R6/R7/R13/R14 trims included, RQ1 drops to ~380 lines, or ~330 if the
breakdown figures move.

## Repetition inventory

| # | Finding | Tellings (lines) | Home | Action elsewhere |
|---|---------|------------------|------|------------------|
| R1 | 16/8 baseline; App. E.6 not in counts | 154, 306–307 (caption), 313–314, 328, 1216 | Setup [7.1.4] + one closing sentence of 5.2 | Delete 328 and 1216; trim caption 306–307 |
| R2 | Two-sided geometry law | 431–439 (home), 941–945, 976–981, 1031–1035, 1140–1143, 1213–1214 | 5.3.3 (statement); if RQ1-diet item 2 is taken, the per-cap breakdown moves to RQ3 and [7.6a] becomes its home there | Delete [7.6a] 976–981 (or expand it into the breakdown's home under diet item 2); compress [7.6h] 1031–1035 to one sentence (mechanism already at 486–490); [7.6s] keeps only the cross-cap carry-over, drops "final form" re-derivation; RQ5 sizing rule 941–945 references the law instead of restating both bounds; RQ3 answer may restate briefly (it is the answer) |
| R3 | Race / valley / pacing mechanism | 575–683 (home), 418–419, 1026–1028, 1077–1086, 1146–1149 | 5.3.5 | Move [7.6l-bis] queue-code evidence into 5.3.5 beside fig:pacing-loop, keep only the queue-shrink falsification in RQ3; [7.6t] 1146–1149 → one sentence ("the pattern recurs 9× across caps and allocation types"); 418–419 → clause + ref |
| R4 | Three-regime law [7.6l] | 1068–1073 | RQ3, as explicit composition | Keep fig:regimes; 3 sentences stating it composes 5.3.3 + 5.3.5; no re-derivation |
| R5 | Pool = up-front reservation competing with heap state | 486–490 (home), 416, 883–884, 1032–1033 | 5.3.3 [7.2.4b] | 416 clause + ref (already close); 883–884 ref only; 1032–1033 deleted with R2 |
| R6 | b1374 fails on pool demand, not footprint | 397–400 (home), 554–558 | 5.3.2, first encounter | Move the 786 MB / 96k numbers into 397–400; 554–558 → one sentence + ref |
| R7 | s1582 fast-ingest exception | 578–581 (home), 418–419, 682 | 5.3.5 | 418–419 → clause + ref (with R3); tighten 682 |
| R8 | Pooled footprint advantage (14–22 %, 25 %, 33 %) | 813–816 (home), 984–986, 1021–1023 | RQ2 [7.3.6] + tab:cost | Delete [7.6b] 984–986 except the mechanism sentence (page recycling); [7.6f] keeps throughput parity + latency, references Section 5.4 for the footprint numbers instead of restating 517 vs 685. Also removes the forward ref at 816 |
| R9 | Checksum correctness | 68, 344–345, 1223 | Setup (mechanism) + [7.2.2] (scoped claim) | Fine as is; 1223 survives only inside the verdict table |
| R10 | Small-n / Wilson CI caveat | 596–600, 1327 | Threats, ninth | Keep the local Wilson numbers at 596–599 (needed for the 2/8 cell); delete the generalizing sentence 600 — threats owns the general statement |
| R11 | Cap- vs pool-derived threshold | 732, 1039, 1104 | RQ3 falsifications | 732 → clause + ref. **Inconsistency: 1039 says pool-derived gives "0 of 8 everywhere"; 1104 and 732 say 3/8 vs 8/8. Check DATA-AUDIT and fix one of them.** |
| R12 | Crash confined to already-failing cells | 165, 183, 444, 751, 1105, 1164, 1215, 1322 | [7.6u–v] + taxonomy row | Trim 1105 (the [7.6n] row; [7.6u] follows two paragraphs later); 751 and 444 keep as one clause each; 1215 (answer) and 1322 (threats) are deliberate and stay |
| R13 | Summary triple-telling | 1221–1231 (prose), 1233–1255 (table), 1259–1267 (pvalues walk-through) | Table + figure | Cut [7.7.1] per-RQ narration to a two-sentence lead; cut [7.7.2] per-row re-narration to 2–3 sentences (rows are labeled on the plot); keep [7.7.3] and [7.7.4] unchanged — they are the only unique content in 5.8 |
| R14 | Answer paragraphs duplicating short sections | RQ1 780–786, RQ4 890–894, RQ5 947–954 | Section bodies | RQ4 and RQ5 are short enough that their answer-first openings suffice; replace closing answers with one transition sentence each. RQ1's answer keeps the Nexmark transfer sentence, drops the division-of-labor restatement (that is 5.3.4 + Table windows) |
| R15 | Label-convention re-derivation | 133–139 (home), 398 | Setup [7.1.2b] | 398: drop "because the number in each label is the footprint in MB" clause |
| R16 | "8 to 10 repetitions per cell" | 153, 158, 227, 328 | Setup + methods note | Drop from 328 (goes with R1) |

## Forward references (violate the no-forward-refs rule)

Dependency-type (must fix):

- 881 (RQ4): "regime two of the three-regime feasibility law, Section rq3" — an
  explanation that depends on unread material. Fix under design A: point backward
  to the throttled regime of 5.3.5 (it is the same regime).
- 816 (RQ2 → RQ3): "both reported in Section rq3" — fixed by R8.
- 860 (RQ2 → Summary): "Section eval-summary interprets why" — acceptable as a
  pointer, but consider moving [7.7.4] into RQ2 and deleting the pointer.

Pointer-type (tolerable, decide once):

- 165 (Setup → RQ3), 327 (RQ1 → RQ3, "are the subject of Section rq3"),
  375/419/557 (5.3.2 → 5.3.5, internal), 732 (Nexmark → RQ3).
  Design B removes 327 naturally. The internal 5.3.x pointers are roadmap-style
  and can stay.

## Defects found on the way (independent of the redesign)

1. **Unescaped `%`, silently eats text**: line 255 (`below 1%.`) and line 804
   (`by 36%.`) — the period and everything after it on the line is commented
   out in the PDF. Change to `1\%` / `36\%`.
2. Stray punctuation: 447 (`).,`) and 450 (`8 of 8.,`).
3. Broken sentence, 154: "…a separate backend-size contrast is reported as
   additional information in Appendix E.6, adds a further 1,938 paired cells…" —
   comma splice; also fixed by R1 rewrite.
4. Garbled clause order, 891: "utilization falls from near-full to disk-bound
   idle, with state fits and restores coming from the page cache, once spilling
   reaches the device, with a minimum of 62%" — disappears if R14 is executed.
5. fig:composition caption, 962: "20\% of memory is  unused by the 8-byte vtable
   pointer" — double space; should read "occupied by the unused 8-byte vtable
   pointer" (≈20 % of the record).
6. Ceiling phrasing drifts: "about 2.6 M" (591, 657, 669) vs "below roughly
   2.5 M / above roughly 2.7 M" (1147–1148). Standardize once in 5.3.5: ceiling
   ≈2.6 M, observed gap 2.5–2.7 M.
7. R11 numeric inconsistency (see table).

## What NOT to cut (deliberate repetition)

- Threats restating scope and small-n (eighth, ninth) — required restatement at
  chapter end.
- Answer-first openings of each RQ section.
- One-clause backward links at section boundaries.
- The RQ3 discovery-order justification (973) — it licenses the narrative form.
- The verdict table.

## Style baseline

The current prose is the style reference for the rewrite: answer-first openings,
scope clauses and p-values attached to claims, neutral configuration names
declared once (323–326), claim → figure → observation → mechanism →
qualification ordering, no em-dashes. The rewrite changes structure and
deduplicates; it does not re-style sentences.

## Expected effect

Dedup alone (R1–R16): roughly 60–80 source lines removed (≈1–1.5 rendered
pages). With the RQ1 diet: 100–150 lines, and RQ1 falls from ~40 % of the
chapter to ~30 %. No figure or table is cut; content only moves or collapses
into references. Biggest single wins: RQ1-diet items 1–2, R2 + R3 (RQ3 body),
R13 (Summary), R14 (answer paragraphs).

## Execution order

1. Decide design A vs B.
2. Fix the mechanical defects (1–5) and the R11 inconsistency first — they are
   independent of the restructure.
3. Execute R1–R16 section by section, RQ3 first (largest change), then Summary,
   then the small trims.
4. Verification pass: read only first sentences of every paragraph (argument must
   survive); grep for `Section~\ref{sec:rq3}` before its section; rebuild PDF.
