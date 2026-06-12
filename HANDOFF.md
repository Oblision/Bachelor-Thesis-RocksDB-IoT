# HANDOFF — Bachelor Thesis: Out-of-Core Stream Processing in NebulaStream
**State: evaluation COMPLETE (~3,300 cells, all RQs answerable). Next step: prose write-up.**
This file is the entry point for the writing session. 2026-06-12.

## Read in this order
1. `thesis/OUTLINE.md` — chapter structure mapped to the DIMA proposal (page budget, figures, appendices)
2. `thesis/WRITING-POINTS.md` — **the draft's spine**: ~140 ordered claims, one per paragraph,
   with evidence tags and the binding construction rules (one point per sentence; declarative;
   no concept used before its Background introduction; negatives stated plainly)
3. `thesis/DATA-AUDIT.md` — **the evidence contract**: per-RQ verdicts, authoritative datasets,
   the 7 residual soft spots. RULE: if a sentence can't be traced to this audit, it doesn't go in.
4. `thesis/figures/geometry-feasibility.png` — figure 1 of 9 (the rest are listed in OUTLINE §6 and
   produced from the data in `…/lag-validation/results|traces|logs`)
5. Deep references as needed: `nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/
   lag-validation/` — EXPERIMENT-stability-levers.md (§1–§9), EXPERIMENT-final-matrix.md (the
   geometry/attribution/twin-defect saga), EXPERIMENT-NEXMARK-V2.md, ANALYSIS-RQ.md,
   OPTIONS-ooc-stability.md (root-cause analyses)

## Writing rules (user-set, binding)
- Simple structured sentences; each sentence carries exactly one point; declarative, science-style.
- Use the /stop-slop skill in the drafting environment (not installed here).
- DIMA requirements: Declaration of Authorship first page; "Further Use of AI Tools" section +
  Appendix: AI Generated Content (Claude was used for implementation assistance, experiment
  orchestration, statistical analysis — document per policy); bibliography [1–14] from the proposal.
- Vocabulary is fixed in WRITING-POINTS 2.10: "bounded allocation" vs "unbounded allocation".
  Older internal docs use "bounded-OOC" loosely — translate, don't copy.

## The result in five sentences
The backend prevents OOM crashes beyond the memory budget, proven at three caps (p = 10⁻⁴…10⁻⁶)
and transferred to a realistic Nexmark workload (8/8 vs 0/8, p = 7.8×10⁻⁵). Three hidden engine
defaults were discovered and measured (buffer granularity; the disabled pooling architecture; the
unsafe slice cache). Failures are not anecdotes but a predictive model: the two-sided feasibility
law, three survival regimes, the ingest-vs-drain race (causally proven), and a formally diagnosed
allocation deadlock — the model passed its out-of-distribution test on Nexmark exactly. The honest
counterweights are documented: cost ranges, the in-order round-trip cost the synthetic jitter
masked, five retracted conclusions, two demoted datasets. Two precisely-specified upstream
mechanisms (epoch-stamped cache invalidation; role-aware allocation) separate today's system from
a single dominant configuration.

## Remaining work (in order)
1. Figure-production pass (8 remaining figures named in OUTLINE §6; data all present)
2. LaTeX skeleton (TU Berlin / DIMA class) + port OUTLINE structure
3. Draft chapters following WRITING-POINTS order; Background (Ch.2) first — it fixes the vocabulary
4. Submission anchor: 2026-07-17

## Repo facts
- `main` = the ONLY branch; canonical, complete history. Historical lines preserved as tags:
  archive/ooc-stability-levers and archive/ooc-ingest-governor (ancestors of main),
  archive/ooc-pooled-state, archive/ooc-pooled-bounded, archive/ooc-bucket-tweak (early
  prototypes/negative results, deliberately NOT merged — they conflict with main by design,
  being obsolete versions of code main evolved further).
- NEVER push to the retired public fork. This private repo is the only remote.
- Raw workload CSVs are NOT in git (size; regenerable): `work/gen_skew.py` + `work/gen_nexmark.py`
  with parameters recorded in the experiment docs; tmpfs (`/dev/shm/nes-input`) clears on reboot.
- To re-run any cell: `lag-validation/run-cell.sh` (env hooks documented in-file); release binary
  builds via the docker one-liner in EXPERIMENT-stability-levers.md §0.
