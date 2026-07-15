---
title: LaTeX abbreviations normalized to \ac{}; chapter intros, methods-note dedup, table/figure fixes
date: 2026-06-28
date_confidence: approximate
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: "n/a (desktop session; transcript export attached on sync)"
duration_wall_clock: one working session, around late June 2026
artifacts:
  - thesis/latex/chapters/*.tex (20 spots across 7 chapter files normalized to \ac{} usage; IoT, cgroup, OOM, JIT, I/O, ASAN, KV, MB/GB unit style)
  - thesis/latex/chapters/3Research_problem.tex, 7Conclusion.tex (two-sentence chapter lead-ins added)
  - thesis/latex/figures/system_overview.tex (missing cold-slices -> RocksDB spill arrow added)
  - thesis/latex/figures/spillable_slice_store.tex (caption condensed from ~12 to 7 sentences)
  - \methodsnote/\nexmarknote macros: one-line pointers under 13 figures, full text once each (new Methods Note subsection in 5.1; Nexmark note paragraph)
  - misc/Annex.tex (Appendix B longtable columns rebalanced, gauge names no longer overflow)
  - thesis/experiments/rb32-config-contrast/fig_rb32.py (ylim padding so value labels clear the frame)
  - thesis/latex/figures/scripts/fig_race.py (legend entries shortened, "the race" tag removed)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.
> Desktop session: "LaTeX abbreviations cleanup" (local_d6f1c55b). The
> session is longer than the readable transcript window; its opening prompt
> is not reachable, so the prompts below are excerpted from the transcript
> tail. The date is approximated from the repository history (the OOM-acronym
> normalization matches commit 1f26e6d, 2026-06-28) and the session's
> position in the session list. A later session revisited
> abbreviation/consistency items before submission; see
> `2026-07-15-abbreviation-consistency-figures-overleaf-export.md`.

## Goal

Make abbreviation usage consistent with the `acronym` package glossary
across all chapters (no spelled-out forms where an \ac{} exists, no
long/short mixing within a chapter), then fix a series of smaller defects
the author spotted: missing chapter lead-ins, a missing diagram edge,
repeated methods-note paragraphs, an overflowing appendix table, and two
figure-label collisions.

## Outcome

- 20 spots across the 7 chapter files corrected: "Internet of Things" ->
  \ac{IoT}; manual "control group, or cgroup" definitions -> \ac{cgroup};
  9 out-of-memory mentions normalized to OOM; 4 "just-in-time" mentions ->
  \ac{JIT}; "input/output (I/O)" -> \ac{IO}; "AddressSanitizer" ->
  \ac{ASAN}; "key-value" -> \ac{KV}; spelled-out megabytes/gigabytes ->
  512\,MB / 2\,GB unit style. The two abstracts were deliberately left
  untouched (typeset before the acronym list is defined). Verified in an
  isolated compile test; the full-thesis compile hit only the known
  sandbox-local ngerman.ldf gap.
- Two-sentence lead-ins added to Chapter 3 and Chapter 7, matching the
  pattern of Chapters 2, 4, and 6 (the thesis-prose writing skill was
  invoked for these).
- Figure 2: the missing spill path added as a direct arrow from the
  cold-slice box into RocksDB, mirroring the existing flush/restore pair.
- Figure 3 caption condensed from ~12 dense sentences to 7, keeping the
  load-bearing facts; a stray manual "just-in-time (JIT)" fixed to \ac{JIT}.
- \methodsnote/\nexmarknote no longer print the full provenance paragraph
  under all 13 figures; each prints a one-line pointer and the full text
  lives once (new "Methods Note for the Synthetic Grid" subsection at the
  end of 5.1; a "Methods note" paragraph at the top of the Nexmark
  subsection). Compile-tested in isolation.
- Appendix B gauge table: longtable columns rebalanced (2.6/5.2/2.4/3.2 cm
  to 3.1/4.6/2.3/2.7 cm) so unhyphenatable gauge names (UPOOLGAUGE,
  SPILLGAUGE, ...) stay in their column; also fixed a pre-existing overflow
  into the page margin. Zero overfull-hbox warnings after the fix.
- Figure 17 (fig_rb32_architecture): explicit ylim padding and larger label
  offset so the "+33"/"-38" value labels clear the axis frame; regenerated
  with identical numbers.
- Figure 8 (fig_race): "the race" removed from the two rho>1 legend entries
  (the in-panel annotation already explains it); regenerated with identical
  underlying data.

## Verbatim prompts (excerpted from the transcript tail)

1. "a short one/two sentence introduction in section 3 is missing"
2. "In figure 2 the path to rocksdb from cold slices being spilled is
   missing"
3. "Section 7 is lacking also the two sentence introduction"
4. "In appendix B, the table boundaries are not being kept, some of text in
   Gauge is spilling over into the next column"
5. "figure 17, there needs to be more vertical space since the bar chart
   values are intersecting with the boundaries"
