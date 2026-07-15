---
title: Prose rule-set enforcement pass (paragraph sizing, figure floats, "flagship" rename, full-thesis sweep)
date: 2026-07-13
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: n/a (desktop session; transcript export attached on sync)
duration_wall_clock: one working session, 2026-07-13
artifacts:
  - thesis/latex/chapters/5Experimental.tex, 7Conclusion.tex (~30 oversized paragraph blocks split at their internal seams; rule-set fixes; "flagship" renamed to "primary finding" in all eight prose spots and the figure caption)
  - thesis/latex/chapters/1-4 *.tex, misc/Abstract.tex, misc/Annex.tex, four TikZ figure files (two-agent full-thesis rule sweep; 53 findings, 50 applied)
  - thesis/latex/figures/scripts/fig_pvalues.py + thesis/latex/img/fig_pvalues.pdf (rendered row-label strings renamed, figure regenerated)
  - Ch. 5 figure floats: 22 figures switched [H] -> [htbp]; fig_flagship scaled to 0.85, figure 19 to 0.95; figure 12 caption trimmed
  - commits deca257 and 38cddee (2026-07-13; matched to this session by content, the commit step itself is not visible in the reachable transcript window)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.

## Goal

Apply the agreed prose rule set (short paragraphs split at their internal
seams, no colon announcements, no coined imagery or economic metaphors, no
rhetorical setups, literal wording, arguments in the body rather than in
captions) across the whole thesis, and fix the figure page flow.

## Outcome

- Paragraph sizing: the block the author quoted was split, and a sweep split
  about 30 more oversized blocks across chapters 5 and 7, always at the
  internal seam (rho<1 two-group analysis, geometry law, Nexmark blocks, the
  nine threats, the IoT finding, the contributions list). At the author's
  correction, the seven-item future-work list was merged back into one
  paragraph.
- Figure flow: 22 chapter-5 figures switched from the [H] float specifier to
  [htbp] to remove half-empty pages; fig_flagship scaled to 0.85 and figure 19
  to 0.95; figure 12's caption cut to a marker legend.
- Rule-set sweep, round 1 (two agents, chapters 5-7): 43 candidate violations
  found, 36 applied, 7 rejected with reasons (defined operational terms
  "race" and "valley" kept; table placeholder notation kept).
- "Flagship" renamed to "primary finding" everywhere, including the rendered
  strings in `fig_pvalues.py`; the figure was regenerated so text and figure
  stay in sync. Internal LaTeX labels kept the old name (never rendered).
- Rule-set sweep, round 2 (two agents, everything outside chapters 5-7:
  chapters 1-4, both abstracts, the annex, four TikZ figure files): 53
  violations found, all applied except three borderline calls. The annex
  needed the most work, including renaming "matched-granularity" /
  "default-granularity" to buffer-size terms and removing seven rendered em
  dashes from the architecture-status figure.
- Verification greps across all files came back clean; the author was asked to
  recompile to confirm the figure band widths.

## Verbatim prompts

The transcript tool returns the most recent messages; this session's opening
(the session is titled "Meeting notes editing") is beyond the reachable
window, so the prompts below are excerpted from the transcript tail. The
visible work applies a rule set agreed earlier in the session ("our rule
set").

1. (pasted the seven-item future-work passage from the conclusion, ending:)
   "this cna be one paragrpah"
2. "make one more run through the paper per our rule set and find out if
   there is anything has been overlooked. Use two agents"
3. "yeah, but primary finding or something liek that is better than contrast"
4. "the rest of the thesis also needs to be checked as well per our
   guidelines in the annexes"
