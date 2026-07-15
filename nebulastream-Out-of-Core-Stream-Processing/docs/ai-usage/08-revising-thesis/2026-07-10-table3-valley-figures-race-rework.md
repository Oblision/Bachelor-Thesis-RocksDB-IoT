---
title: Table 3 removal, valley figures into 5.3.5, fig:race recolored by failure reason
date: 2026-07-10
date_confidence: approximate
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: "n/a (desktop session; transcript export attached on sync)"
duration_wall_clock: one working session, around 2026-07-10
artifacts:
  - thesis/latex/chapters/5Experimental.tex (valley figures relocated into 5.3.5; "free-run" replaced by "unthrottled" in nine prose spots; fig:race caption/prose rework; Table 3 removal)
  - thesis/latex/figures/scripts/fig_valley.py (label placements, "unthrottled" markers, legend/caption alignment)
  - thesis/latex/figures/scripts/fig_race.py (rebuilt: colour = failure reason, shape = architecture; legend split; ceiling regions; simplified x-axis)
  - fig:pacing-loop label rename and figure fixes
  - memory notes valley-figures-and-pacing and thesis-writing-and-figure-prefs (session learnings, saved on request)
  - commit fdebd30 (2026-07-10, "prose(thesis): finalize Ch.5 experimental writeup, new backpressure/valley/throttle figures"; committed/pushed via a separate session, see below)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.
> Desktop session: "Table 3 presentation fix" (local_ddf76ab1). The session
> is longer than the readable transcript window; its opening is not
> reachable, so the prompts below are excerpted from the transcript tail.
> The day's changes were committed and pushed through a companion desktop
> session ("Repository changes push", local_f2434563) that contained only
> routine git operations and is therefore not documented as its own entry.

## Goal

Fix the presentation of Table 3 and the completion-valley material, then
iterate on the two central RQ1 figures (fig:valley, fig:race) until the
failure mechanisms are read directly off the figures.

## Outcome

- The three completion-valley figures were moved into 5.3.5 "Completion as a
  Race", where the valley is discussed, with the sizing-law reference now
  pointing backward instead of forward; the internal label renamed to
  fig:pacing-loop; Table 3 was removed (recorded in the session's saved
  learnings as "the Table 3 removal").
- fig:valley label passes: the OOM annotation moved to the right margin with
  a leader arrow, clear of the completion line; ceiling label moved below its
  line; "qualitative" and "fits (rho<1)" labels repositioned off the data.
- Terminology: after the author rejected "free-run", the markers, legend
  title, caption, the fig:race axis label, and nine prose spots across 5.3
  and 5.7 were changed to "unthrottled" (Python comments left untouched).
- fig:race redesigned on the author's diagnosis that the triangle/plus
  marker split did not explain why rho<1 cells fail: colour now encodes the
  failure reason (green completes, orange pool abort, red OOM = "pool +
  overhead > cap"), shape encodes the architecture. A data check first
  confirmed the redesign: at rho<1 the capped in-memory baseline completes
  essentially all cells (17/18 full) while the out-of-core backend fails 33
  of those fitting cells (24 pool-abort, 9 OOM), and the caption/prose now
  lead with that backend-introduced-failure point.
- A pattern the author proposed (pool-abort left / OOM right along the
  ingest axis) was checked against the data and rejected: both classes have
  near-identical ingest distributions; the real separator is pool size (the
  geometry law). The thesis was deliberately not changed to claim the
  pattern.
- Final layout round: middle annotation removed, legend split into colours
  (top) and architecture (bottom), title spacing, throttled/exceeds-bandwidth
  regions meeting exactly at the 2.6 M/s ceiling, x-axis label simplified to
  "ingest rate (million tuples/s)" with the realized-vs-unthrottled
  convention explained in the caption.
- Session learnings were saved to the persistent memory notes
  (valley-figures-and-pacing; thesis-writing-and-figure-prefs: no forward
  references, no em-dashes, labels off data lines, outcome colour code).

## Verbatim prompts (excerpted from the transcript tail)

1. "OOM state exceeds cap is still crossed by the blue line"
2. "move the qualitative etc. label more towards the dotted line. It is a
   bit far"
3. "too close"
4. "free-run is not good. Change the prose"
5. "in Fig Race the distinct of the arrows and the plus' don't really make
   sense to me. The reasons they fail is not explicit. I think it should be
   pool too small vs oom or something to better represent why a rho under 1
   fails. Also we need to make sure that we mention that in our evaluation
   that there system also creates failures where the in-memory constrained
   version doesn't fail since it is sufficient enough. This needs to be
   clear as well"
6. "the text in the middle of the graph is not needed anymore"
7. "make the top three in the lgend be the colors only and the bottom two
   show the architecture distintion"
8. "I understand with the wrong analysis"
9. "recolor the other boundaries to meet at 2.6M/s"
10. "there is too much space between the title and the legend. move the
    title down"
11. "and what does realized for surviors, unthrottled demand for all-fail
    cells?"
12. "move explanation to caption and make it simple and clear"
