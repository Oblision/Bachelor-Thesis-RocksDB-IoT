---
title: Advisor meeting notes round 5 implemented; workload names become footprint-MB
date: 2026-07-07
phase: 08-revising-thesis
level_of_use: 4
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: "n/a (desktop session; transcript export attached on sync)"
duration_wall_clock: one working session, 2026-07-07
artifacts:
  - thesis/latex/chapters/*.tex (round-5 meeting-note fixes; JIT removed thesis-wide; terminology arm -> configuration)
  - thesis/latex/chapters/5Experimental.tex (workload rename s327...b2078; tab:workloads with repository-id column; admission-queue paragraphs)
  - thesis/latex/figures/scripts/* (13 label-carrying figures regenerated via central data.wlabel lookup; fig_race relabeled)
  - Appendix A removed in favour of a GitHub reference; Table 5 compacted
  - commits 7beff01, 62a8a71 (both 2026-07-07; builds verified: 0 errors, 0 undefined references, 101 pages)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.
> Desktop session: "Thesis clarity and readability review" (local_bb762d6c),
> conducted largely in German. The session is longer than the readable
> transcript window; its opening (the round-5 meeting-notes list) is not
> reachable, so the prompt below is excerpted from the transcript tail.

## Goal

Implement the full list from the fifth advisor meeting-notes round
(clarity and readability items across the thesis), then make the workload
naming self-explanatory so that name, footprint, and rho line up at a glance.

## Outcome

- Round-5 notes implemented and committed as `7beff01`: JIT removed from all
  7 mentions plus the acronym list ("query-compilation pipeline" instead);
  listing caption explains `${FILECSV}` and `${WINDOW}`; workloads renamed
  into two letter families (s75-s300, b500-b1000) with footprint-MB and rho
  per cap in the table, a worked example, and the repository mapping
  documented (81 text spots plus all figure labels); 5.1.1 written out as a
  paragraph; fig_race switched to "storage-bandwidth ceiling" (9 text spots
  plus Ch. 7) with the marker scheme explained in the legend; the
  admission-queue explanation framed as an explicitly unmeasured hypothesis;
  SD-card result framing; "arm" replaced by "configuration" (68 spots) and
  "architecture" by configuration/allocation type; Appendix A replaced by a
  GitHub reference; Table 5 compacted to 3 rows; the longest Ch. 5 paragraph
  blocks split.
- Follow-up (the visible prompt) committed as `62a8a71`: workload numbers
  are now the measured footprints in MB themselves (s327, s458, s736, s1034,
  s1582 / b1374, b1676, b2078), changed in all 76 text spots, tab:workloads
  (new repository-id column), and all 13 label-carrying figures via a
  central lookup; verified that no old label survives anywhere in the PDF.
- The admission-queue hypothesis was hardened by checking the engine source:
  TaskQueue.hpp documents the bounded admission queue and
  addAdmissionTaskBlocking blocks the source until a slot frees; the text
  now grades the mechanism as code-verified, with only the quantitative
  attribution left as hypothesis.
- Non-document items (Grammarly pass, submission logistics) were left with
  the author as TODOs.

## Verbatim prompts (excerpted from the transcript tail)

1. "schreibe die Zahlreferenzen bei den workload familien auf den bezug auf
   die MB's nicht die randomly allocated damit das einheitlich auf einem
   blick klar ist und stelle sicher das überall die korrigiert angezeigt
   sind"
