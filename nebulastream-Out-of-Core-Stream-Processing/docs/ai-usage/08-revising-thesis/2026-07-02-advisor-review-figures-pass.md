---
title: Advisor-persona review pass, figure rework, advisor-list leftovers
date: 2026-07-02
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: n/a (desktop session; transcript export to be attached on sync)
duration_wall_clock: one working session, 2026-07-02
artifacts:
  - thesis/REVIEW-PROSE-2026-07-02.md (review notes, committed)
  - thesis/latex/chapters/*.tex (prose corrections per review notes)
  - thesis/latex/figures/system_overview.tex (new Fig.: solution data-flow overview)
  - thesis/latex/figures/nes_components.tex (new Fig.: Ch.2 NES components)
  - thesis/latex/figures/scripts/fig_race.py, fig_effectiveness.py (declutter, titles)
  - thesis/latex/img/*.pdf (all 14 data figures regenerated; gap-fill restored)
  - thesis/latex/Template.tex (lstset for SQL listing, acronyms, methodsnote units)
  - commits: af53a5b, fa3aa73, + the commit carrying this file
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.

## Goal

Full-thesis review on branch `thesis/advisor-revisions-2026-06-30` from two
personas (doctoral advisor; reader without systems background), with fixes
implemented and committed. Follow-ups within the same session: figure
readability (race plot), restoring the 32/32 gap-fill runs in the
operating-window figures, fixing the SQL listing rendering, a Background-chapter
component diagram, and the leftovers from the resubmitted advisor meeting list.

## Outcome

- Review notes with per-item status: `thesis/REVIEW-PROSE-2026-07-02.md`.
- Two factual corrections found by checking prose against the harness and data
  docs: workload skew/universe (generator defaults had leaked into §5.1) and
  the W/L units (event-time ms, not tuples).
- Consistency, tone, and clarity edits across all chapters; EN-US spelling.
- New figures: solution overview (§4.1) and NES components (§2.2), both TikZ.
- fig_race decluttered; fig_effectiveness/fig_race titles made descriptive;
  fig_heatmap (and the 5 sibling grid figures) regenerated with the 32/32
  gap-fill repetitions restored; rep-count pooling now explained in caption
  and setup.
- SQL listing (Listing 1) wrapped and typeset via a global lstset.
- Statistics test definitions removed from Ch. 2 (kept in §5.1 only);
  "characterization" renamed to experimental analysis for RocksDB; JIT-probe
  reasoning added to the integration figure caption; acronyms (JIT, CSV, SSD)
  added; NebulaStream fork provenance (commit cc754a94, 2026-05-20) stated in
  §4.7.
- All design decisions and final wording reviewed by the author; build verified
  (pdflatex + bibtex, 0 errors, 0 undefined references).

## Verbatim prompts

1. "We made a general overhaul after getting the surface level feedback from
   out advisor. We made the changes on a seperate git branch in a different
   chat. I would like you to now act as an advisor or a doctorate reading
   this, as well as a normal person reading this without a lot of context, and
   see if the statements are actually logically succinct or make sense. It's
   easily seen what the story is being told by the red line or rota faden, and
   basically all the data is accurate. The graphs are properly layered and
   rendered and have a complete check of the entire thesis, so it is
   submission-ready. If there are any points that are missing or anything that
   needs to be adapted as well, once you have the notes ready, create an md of
   the words that would need to be changed and implement that as well, and
   push that as the next commit on this branch. Keep everything very clear.
   The whole idea is that the entire presentation of the ideas is completely
   easily readable. Also, I thought about the idea of maybe having a complete
   graph or maybe a Mervin diagram of some sort, something very visually
   distinct and nice to see how the different components, such as a buffer
   manager, the buffers themselves, pages, and all the different parts of the
   whole thesis for the technical parts, how they interact with each other.
   That maybe will be interesting to see as well, rather like a general system
   overview in that sense. Please evaluate everything and make sure that, as
   an advisor and as well as a doctorate or someone reading this paper, it
   would be useful in the fines that they have. The important part is being
   very clear about the results and not biasing them towards some sections.
   It's fine to say that something didn't work out. It's just better to say
   what didn't work out specifically in that section. We didn't say that in a
   lot of parts, but some of the writing or prose was just not there yet, and
   that's basically what I want to improve upon now and make sure that that's
   taken care of."
2. "we also need to include the repo state that we forked off from the date or
   version. I don't think that is mentioned anywhere."
3. "the png image should be a svg or a latex graph instead so it renders
   better"
4. (relayed `git push` output confirming the branch was pushed)
5. "Figure 7 is quite packed and it is hard to read"
6. "In figure 5 the addtional runs we had recently made to fill in the gaps
   are not present"
7. "the SQL queery is not being displayed properly"
8. Resubmitted advisor meeting notes (German, abridged here; full text in the
   exported transcript): items on Ch.1/Ch.3 template conformance, p-values
   figure, missing abbreviations such as OOM, moving the p-value material out
   of the scientific background, RQ4/5 naming, sentence under "Third",
   JIT-probe reasoning, §4.3 wording ("sweeps those knobs", characterization
   vs. experimental analysis), top-down solution presentation with iterations
   as alternatives, forward references, system graphic extensions, workload =
   query with LaTeX source, SD/SSD device study, heap-with-8K control, rho
   presentation, heatmap failure counts per cell, descriptive figure titles,
   drain-ceiling justification, Ch.5 rework, pooled-vs-heap throughput
   mechanism, CPU utilization labeling, control-block spill clarification,
   IoT ingestion/SD-card variability as negative finding, role-blind wording,
   Figure 16 compartment naming, writing out §5.7.
9. "und der neue graph für die stream processing bestandteile in NES die wir
   so eben erstellt haben in Latex sollte in den Background noch mit rein"
10. "as a last task, we also need to update our ai usage and reflection of
    work on the github and the declaration."
