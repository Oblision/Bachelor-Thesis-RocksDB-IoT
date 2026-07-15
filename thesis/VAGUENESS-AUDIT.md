# Vagueness Audit — Referent-Free Prose

Date: 2026-07-15. Scope: `Abschlussarbeit_396953/chapters/1–7*.tex`, `misc/Abstract.tex`, `misc/Abstract_deutsch.tex`, `misc/Annex.tex`, plus the captions of the four `\input` TikZ figures (`nes_components`, `system_overview`, `spillable_slice_store`, `final_status_uml`). Read in full, line by line.

Defect classes: (1) unanchored location metaphor, (2) coined shorthand for a specific experimental object, (3) abstract process noun where a concrete claim fits, (4) dangling deixis, (5) unfalsifiable filler, (6) orphaned comparative.

Convention followed: defined terms used consistently are **not** flagged (memory boundary = ρ=1, small-pool/abort edge and OOM edge from the geometry law, hot band, operating window, primary finding, attribution ladder, completion valley, beyond-memory point, feasibility corridor, three-regime law). Doubtful cases went to the borderline list.

## Summary table

| File | Cl. 1 | Cl. 2 | Cl. 3 | Cl. 4 | Cl. 5 | Cl. 6 | Main total | Borderline |
|---|---|---|---|---|---|---|---|---|
| Abstract (EN) | – | – | – | – | – | – | 0 | 1 |
| Abstract (DE) | – | – | – | – | – | – | 0 | 1 |
| Ch. 1 Introduction | – | – | 1 | – | – | 1 | 2 | 3 |
| Ch. 2 Background | – | – | 1 | – | – | – | 1 | 1 |
| Ch. 3 Research Problem | – | – | – | – | – | – | 0 | 2 |
| Ch. 4 Solution | – | – | – | – | – | – | 0 | 3 |
| Ch. 5 Evaluation | 1 | – | 3 | 2 | – | 2 | 7* | 5 |
| Ch. 6 Related Work | – | – | – | – | – | – | 0 | 1 |
| Ch. 7 Conclusion | – | – | – | 1 | – | 1 | 2 | 1 |
| Annex | – | – | – | – | – | – | 0 | 0 |
| **Total** | **1** | **0** | **5** | **3** | **0** | **4** | **12** | **18** |

\* One Ch. 5 finding (F5) mixes classes 1 and 2; it is counted once, under class 1.

Class 2 (coined object names) has no survivors: no "decisive/arbitration/ladder cell" or equivalents remain; all workload references use s/b labels or coordinates. Class 5 has no clean hits; the near-misses are quantified in an adjacent sentence and were set aside as borderline.

---

## Main findings, in document order

### Chapter 1 — `chapters/1Introduction.tex`

**F1 · line 50 · class 3 (abstract process noun)**
> "This thesis provides that evaluation, and its impact is measured."

Why it fails: "its impact is measured" names no quantity, and "its" is ambiguous (the evaluation's impact? the thesis's?). The concrete numbers arrive only in the next paragraph.
Rewrite: delete the second clause — "This thesis provides that evaluation." — or replace with the referent: "This thesis provides that evaluation; the next paragraph states its measured results: the operating windows, the 11–15% throughput cost, and the allocation finding."

**F2 · line 88 · class 6 (orphaned comparative)**
> "A page-release extension cuts steady anonymous memory by a further 55\% on draining workloads."

Why it fails: "a further 55%" has no base in C7 or the preceding sentence (the growth signal reduces nothing); the nearest candidate base, C4's 25% advantage, is three items away and a different comparison (versus the in-memory engine).
Rewrite: "A page-release extension cuts steady anonymous memory on draining workloads by 55\%, below what pooling alone retains after a drain." (Ch. 5, §5.4.2 states the mechanism this way: pooling recycles, page release returns pages to the OS.)

### Chapter 2 — `chapters/2Sci_background.tex`

**F3 · line 160 · class 3 (with a class-5 flavor)**
> "Careful configuration is therefore necessary under a tight memory budget, and the evaluation is set up informed by these studies."

Why it fails: "careful" and "set up informed by" carry no content — the reader cannot tell what was taken from the studies.
Rewrite: "Under a tight memory budget these parameters must therefore be set deliberately; Section~\ref{sec:rocksdb-config} fixes the three the studies identify (compression, write buffer size, block cache size) by measurement."

### Chapter 5 — `chapters/5Experimental.tex`

**F4 · line 462 · class 4 (unnamed specific cell)**
> "At 2048\,MB, the pooled configuration completes the s1034 through b1676 workloads, with s1582 marginal in 5 of 8 runs, and a bounded-only cell where both heap configurations complete 0 of 8 runs."

Why it fails: "a bounded-only cell" is a specific cell with no coordinates; the reader cannot tell whether it is the s1582 cell just mentioned or another one (Table~tab:windows shows it IS s1582 at 96k).
Rewrite: "…with s1582 marginal at 5 of 8; that cell (s1582, 96k pool) is bounded-only, since both heap configurations complete 0 of 8 there."

**F5 · line 469 · class 1 (unanchored location/set descriptors; class-2 flavor)**
> "Only the heap configuration completes at the smallest cap and at the fast-ingest cells, and it reaches higher throughput at the heavy end; at those heavy cells, the pooled configuration is slower due to allocation stalls on the shared pool (Section~\ref{sec:rq4})."

Why it fails: "the fast-ingest cells" and "the heavy end / those heavy cells" name cell sets that are never enumerated, and "fast-ingest" is not yet defined at this point (the ingest-drain race is introduced one subsection later, §5.3.5 — a forward reference).
Rewrite: "Only the heap configuration completes at the 512\,MB cap and at cells whose unthrottled demand approaches the storage ceiling (Section~\ref{sec:rq1-race}; e.g.\ s736 at 512\,MB/16k: heap 7 of 8, pooled 1 of 8), and it reaches higher throughput at the highest tested pressures, where the pooled configuration loses time to allocation stalls on the shared pool (Section~\ref{sec:rq4})."

**F6 · line 709 · class 3 (process noun where the claim fits)**
> "The result transfers beyond the synthetic grid; on a realistic Nexmark bid stream, the bounded backend demonstrates the ability to complete where the in-memory engine cannot (Figure~\ref{fig:nexmark-sweep})."

Why it fails: "demonstrates the ability to complete" is the same pattern as "demonstrates the viability" — the concrete claim is simply that it completes, and the counts exist (line 665).
Rewrite: "…on a realistic Nexmark bid stream, the bounded backend completes where the capped in-memory engine cannot (8 of 8 and 7 of 8 versus 0 of 8; Figure~\ref{fig:nexmark-sweep})."

**F7 · line 727 · class 3 (ambiguous abstract claim)**
> "The four pool-pressure levers (Section~\ref{sec:levers}) operated mechanically and did not fail a cell."

Why it fails: "operated mechanically" says nothing concrete, and "did not fail a cell" is readable two opposite ways — caused no cell to fail, or converted no failing cell. The intended meaning (from [7.6c–d] and the negative-results ledger) is the latter.
Rewrite: "The four pool-pressure levers (Section~\ref{sec:levers}) executed their spills as designed but converted no failing cell (levers without pooling: 0 of 16)."

**F8 · line 855 · class 6 (double orphaned comparative)**
> "At 1024\, MB, the same relative pool size completes two additional workloads."

Why it fails: "the same relative pool size" — same as the 39%-of-cap pool of the previous paragraph, but an intervening sentence breaks the anaphora; "two additional workloads" — additional to what baseline is unstated (the smaller pools at 1024? the 2048 result?).
Rewrite (assuming the 48k pool and the geometry-law figure; confirm intent): "At 1024\,MB, the corresponding 48k pool ($\approx$39\% of the cap) completes two workloads that no smaller pool completes there, s736 and s1582 (8 of 8 each; Figure~\ref{fig:geometry-law-1024})."

**F9 · line 856 · class 6 (orphaned comparative, same base)**
> "At 512\, MB, the same relative pool size is OOM-killed because the directory overhead the pool induces exceeds the cap."

Why it fails: same missing base as F8, and "is OOM-killed" lacks the workloads it is killed on.
Rewrite: "At 512\,MB, the corresponding 24k pool ($\approx$39\% of the cap) is OOM-killed on every workload above s327 (Figure~\ref{fig:geometry-law-512}), because the directory overhead the pool induces exceeds the cap."

**F10 · line 967 · class 4 (distant, unnamed referent)**
> "This result also clarifies the recycling advantage reported earlier."

Why it fails: "the recycling advantage reported earlier" points ~240 lines back (the 14–22% footprint advantage at the opening of §5.4) with no section reference or number; the reader must reconstruct which of several footprint advantages is meant (14–22%, 25%, 33% all appear in this chapter).
Rewrite: "This result also clarifies the 14--22\% anonymous-memory advantage of pooling reported at the start of this section."

### Chapter 7 — `chapters/7Conclusion.tex`

**F11 · line 24 · class 6 (orphaned comparative)**
> "…the page-release extension cuts steady anonymous memory usage by a further 55\% on draining workloads; neither resolves the residual deadlock…"

Why it fails: same defect as F2 — "a further" with no base in C7; C4's 25% is three items earlier and a different comparison.
Rewrite: as F2 — "…cuts steady anonymous memory on draining workloads by 55\% below what pooling alone retains…"

**F12 · line 27 · class 4 (dangling deixis — the worst single instance)**
> "The evaluation also yields one negative finding: no engine-side mechanism can remove it."

Why it fails: "it" has no antecedent at all; the referent (the storage-bandwidth limit of the I/O path) is only assembled over the following three paragraphs (lines 28–41).
Rewrite: "The evaluation also yields one negative finding: once the storage device is the bottleneck, no engine-side mechanism can remove that limit."

---

## Borderline cases (set aside, 18)

Listed for the author's judgment; each is either anchored in an adjacent sentence, a declared/consistent coinage, or a word-choice slip rather than a missing referent.

1. **Abstract.tex, line 11** — "…it recovers most of the failing workloads and matches the in-memory engine's throughput at 25\% less anonymous memory." The parity claim holds at one measured cell (1024 MB / 32k / s736); the Intro and Ch. 7 both carry that scope, the abstract drops it. Suggest "…and, at one measured operating point, matches…". ("Most" is acceptable abstract compression.)
2. **Abstract_deutsch.tex, line 11** — same sentence in German ("…erreicht den Durchsatz der In-Memory-Engine bei 25\,\% weniger anonymem Speicher"); same missing scope.
3. **Ch. 1, line 38** — "…alleviate memory pressure while maintaining stable execution…": "stable execution" undefined here; tolerable as a challenge statement, later grounded by the outcome taxonomy (Table tab:outcomes).
4. **Ch. 1, line 57** — "at one measured operating point": scoped but unnamed; Ch. 7 line 19 names it (1024 MB cap, 32k pool, s736) — the Intro could too.
5. **Ch. 1, line 89** — "…and the minimum resident memory remains": definite noun phrase for a concept first established in §5.4.2; a first-time reader has no referent (also violates the no-forward-refs rule).
6. **Ch. 2, line 22** — "Its execution engine processes data streams, on which this thesis builds." Ambiguous antecedent of "which" (engine or streams). Suggest: "This thesis builds on its execution engine."
7. **Ch. 3, line 5** — "It then fixes the scope of the thesis in a way that answers them." The trailing clause is contentless; "It then fixes the scope of the thesis." suffices.
8. **Ch. 3, line 13 (and RQ2, line 31)** — "configured in a research-informed manner" / "a research-informed RocksDB configuration": recurring coinage, used consistently and anchored by refs to §2.3/§5.2, but never crisply defined. If kept, define once: "research-informed = the three parameters prior work identifies, fixed by the db\_bench sweep of Section 5.2."
9. **Ch. 4, line 22** — "The spiller responds only to the slice budget": term used before its definition (line 84–86, §4.3). Move the definition or gloss it inline.
10. **Ch. 4, line 31** — "…allowing cold state to leave memory while the system is still healthy": "healthy" unquantified; the soft/hard threshold pair in the same paragraph gives it approximate content.
11. **Ch. 4, line 199** — "The next chapter sets up and evaluates the evaluation harness." Referent error rather than vagueness: Ch. 5 evaluates the backend using the harness, not the harness itself.
12. **Ch. 5, line 286** — "The three values are the smallest that still perform well under the tightest 512\,MB cap." "Perform well" is quantified only in the preceding sentence; same-sentence rule would want "…that keep write stalls below 1\% and throughput within X\% of the best."
13. **Ch. 5, line 465** — "This small-cap limit is permanent and follows from the two bounds above." "Permanent" is the wrong axis (it means structural, not temporal); suggest "structural: it follows from the two bounds, not from tuning."
14. **Ch. 5, line 736** — "One flag (buffer size 8192) matches the intended memory configuration, and key results are shown in Figure~\ref{fig:ladder}." "Matches" should be "engages"/"restores"; "key results are shown" is a contentless lead-in before the concrete ladder counts.
15. **Ch. 5, line 818** — "The allocation-backpressure level reduces throughput by 10\% at the operating window and converts nothing." "level" is a typo for "lever"; "at the operating window" is an odd location ("near the small-pool edge of the operating window" per the negative-results ledger).
16. **Ch. 5, line 989** — "Earlier comparisons showed a 2--5$\times$ gap because the variants used different buffer sizes." "Earlier" means earlier in the campaign, not earlier in the text; not locatable. Suggest "Comparisons at unmatched buffer sizes (the default-configuration rows of Table~\ref{tab:cost}) show a 2--5$\times$ gap…"
17. **Ch. 6, line 15** — "Flink's runtime differs from NebulaStream's in two ways that matter here." Topic-sentence "matter" fulfilled two sentences later ("Results from Flink therefore do not transfer directly"); acceptable, but "…in two ways that block direct transfer of its results" would carry the content in-sentence.
18. **Ch. 7, line 109** — "…epoch-stamped slice-cache invalidation, the upstream fix for the diagnosed corruption and the hidden-state growth signal exposed (Section…)": garbled apposition; presumably "…and for the hidden state growth the signal exposed."

## Side notes (not vagueness, caught in passing)

- **Ch. 1, line 11**: malformed citation `\cite{,zeuch2020}` (leading comma / empty key).
- **Ch. 5, line 128**: ρ glossed as "working-set state divided by the cap" while §2.5 defines it as steady-state *anonymous-memory footprint* divided by the cap — wording drift on a load-bearing metric.
- **Ch. 5, line 913**: "The diagnosis is falsified in both directions" — "falsified" is misused; the diagnosis was *tested* in both directions and confirmed (3/3 crash with cache on, 4/4 clean with cache off).

## Clean areas

Ch. 3, Ch. 4, Ch. 6, the Annex (incl. Appendix E.6), and all four TikZ figure captions produced no main-list findings. Ch. 5's post-revision text is largely clean; its seven hits cluster in §5.3.4 (division of the operating range) and §5.4.x (geometry/valley and page-release subsections), which the dedup pass touched least.
