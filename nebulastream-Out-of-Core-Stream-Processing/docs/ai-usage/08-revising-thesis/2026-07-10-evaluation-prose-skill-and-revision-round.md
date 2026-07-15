---
title: Evaluation-prose writing skill; chapter-wide revision round, Fig 14/15 rework, reclaim mechanism
date: 2026-07-10
date_confidence: approximate
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: "n/a (desktop session; transcript export attached on sync)"
duration_wall_clock: one working session, around 2026-07-10
artifacts:
  - thesis/latex/chapters/7Conclusion.tex (answer-first opening; three-tier Limitations; device-variance closing sentence)
  - thesis/latex/chapters/5Experimental.tex (section bridges RQ1-RQ5, Kernsatz fixes, backpressure disambiguation, em-dashes out of captions, 5.9 interpretation paragraph, 5.1 cpuset/quota explanation)
  - thesis/latex/chapters/2Sci_background.tex (sec:bg-metrics; cgroup reclaim / OOM-kill mechanism, anonymous-vs-cache memory)
  - thesis/latex/chapters/3Research_problem.tex (Scope states the fixed parameter subset up front)
  - abstracts and appendix glosses ("corrected"/"Korrektur" wording removed; "within a characterized operating window")
  - Appendix E.6 (two rb32 figures)
  - figures: pacing-loop labels de-overlapped; throttle trace extended with total cgroup memory traces and saturation note
  - commits 5a95ab9, 62089a6 (2026-07-10)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.
> Desktop session: "Scientific writing skill development" (local_7de1aed5).
> The session is longer than the readable transcript window; its opening is
> not reachable, so the prompts below are excerpted from the transcript tail.
> Per its title, the session opened with the development of the
> evaluation-prose writing skill (structure rules for the results chapter:
> answer-first sections, claim-evidence-interpretation paragraphs, honest
> statistics); the reachable tail shows the revision round applying it
> ("Two things I kept deliberately careful, per the skill").

## Goal

Build a reusable scientific-writing skill for the evaluation chapter and
apply a full revision round from the author's notes: conclusion structure,
scope statements, section bridges, terminology, and two figure-mechanism
problems (pacing-loop label overlaps; how OOM kills relate to the plotted
memory).

## Outcome

- Four work packages executed and verified (no duplicate/missing labels,
  balanced environments, zero em-dashes in Ch. 5 captions, zero banned
  words): Ch. 7 answer-first opening plus a three-tier Limitations scope
  (fixed parameter subset, 8-10-rep statistical uncertainty, NebulaStream
  specificity); Ch. 5 bridges between all RQ sections and Kernsatz fixes;
  every ambiguous "backpressure" disambiguated to "pool-allocation stalls",
  "intrinsic queue pacing", or the "allocation-backpressure lever"; abstract
  and appendix glosses reworded; Ch. 3 Scope states the fixed-subset design
  up front; Appendix E.6 now carries the two rb32 figures.
- Figure 14 (pacing loop): arrow labels moved above the box row, feedback arc
  re-routed above the pipeline; overlaps gone.
- Figure 15 (throttle trace): now plots the anonymous set plus total charged
  cgroup memory per run, making the OOM mechanism visible. The author's
  follow-up ("is the 512 threshold really that close?") was answered from
  the raw traces: totals sit at exactly 512 MB for about half the runtime of
  the heaviest runs, so the saturation was made legible (solid cap line,
  in-figure note, exact numbers in the caption) rather than falsified apart.
- The why (page-cache charging, reclaim steady state, swap-off making
  anonymous memory unreclaimable) was written into Section 2's metrics
  definitions, with 5.3.5 and the Figure 15 caption citing it backward,
  after the author pressed "are we saying that anywhere?".
- New 5.9 interpretation paragraph: why the spilling cost is "a factor, not
  an order of magnitude" (reduction before storage, resident hot band,
  page-cache restores, asynchronous drain), with the honest scoping that the
  moderate cost is conditional, not intrinsic; RQ2 roadmap sentence and a
  Ch. 7 device-variance sentence close the loop.
- 5.1 now explains the --cpus=2 / --cpuset-cpus=0,1,8,9 split (cpuset fixes
  where, quota fixes how much; sibling hyperthreads claimed for isolation),
  with an explicit request that the author verify the sibling pairing on the
  real host via lscpu.

## Verbatim prompts (excerpted from the transcript tail)

1. "in figure 14 the there are overelaps between the items with the texts
   written in between them and in figure 15 it is not clear how the OOMs
   happen sice only the Pooled / allocation are presented and not the rest
   of the memory such as anonymus. Could you improve that?"
2. "In figure 15 it is hard to reconcile if the 512 threshold is hit. In the
   2 rho (green) one it seems like the threshold is almost or is reached.
   Can we make a better distinction or is it really so close?"
3. "so why are they riding the line so close to 512?"
4. "are we saying that anywhere? This is important to explain"
5. "when comparing the the throughput, one would a expect an order of
   magnitude lower, however we were able to still achieve one only about 50%
   on average slower. This is an achievement. What is this achievement likely
   due and do we establish that context given disk and memory vary
   extensively in their operating speeds?"
6. "why did we use --cpus=2 --cpuset-cpus=0,1,8,9 with a 8 core, Intel Core
   i7-10700K 8 Core, it is for the logical threads the second argument?"
