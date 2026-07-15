---
title: RQ-to-proposal coverage note; Ch.2 metrics prose, Ch.4 setup corrections, two experiment branches published
date: 2026-06-28
phase: 06-planning-thesis
level_of_use: 4
model: claude-sonnet-4-6 (dominant model id in the session log; substantial parts on claude-opus-4-8)
tool: Claude Code (CLI)
share_link: n/a (Claude Code session, no shareable URL)
duration_wall_clock: 2026-06-28 10:22 to 2026-06-29 13:49 (one session, compacted several times)
artifacts:
  - transcript: ../transcripts/claude-code/2026-06-28-user-answered-claude-s-questions-given-the-rqs-act.md (Claude Code session log, full verbatim)
  - thesis/latex/chapters/3Research_problem.tex (one-line RQ coverage note tracing each RQ to its proposal source, per the corrected mapping)
  - thesis/latex/chapters/2Sci_background.tex (metrics prose: naive totals / resident set vs cgroup memory.stat distinction; Fisher and McNemar formulas re-added)
  - thesis/latex/figures/scripts (fig_cost.py updates; per the in-log compaction summary, the 7 multi-panel figures were converted to vertical stacked layouts earlier in the same session)
  - thesis/latex/chapters/5Experimental.tex (Table 3 overflow fixed with tabularx)
  - thesis/latex/chapters/4Solution.tex (4.3 RocksDB knob attribution corrected to the thesis's own 10k-run characterization; 4.5 allocation backpressure, 4.7 instrumentation, and 4.8 evaluation harness rewritten; setup corrected from "two physical cores, pinned" to four cores per runs.tsv and HOME_SETUP.md)
  - GitHub branch thesis-setup-corrections at commit 68bd198 (three setup corrections, uploaded via the REST API after git pushes timed out)
  - GitHub branch ooc-growth-spill-signal (lever-1e handoff executed: local commit 5ce6155, remote 6c01e533; see the phase-04 entry 2026-06-28-growth-path-spill-signal.md)
---

> **Full verbatim transcript attached.** This entry links the complete
> Claude Code session log (user and assistant text verbatim; tool-call
> payloads omitted for size):
> [`../transcripts/claude-code/2026-06-28-user-answered-claude-s-questions-given-the-rqs-act.md`](../transcripts/claude-code/2026-06-28-user-answered-claude-s-questions-given-the-rqs-act.md).
> Source log `60f6963f-4a10-473a-8fcd-259ee96642a0.jsonl`, span
> 2026-06-28T10:22 to 2026-06-29T13:49 UTC. The session opens with the
> author's answer to the RQ-mapping question raised at the end of the
> 2026-06-27 resume session (see
> `../02-understanding-topic/2026-06-27-push-based-execution-buffer-pool-and-audit.md`);
> it is a direct continuation with a new session id.
> Phase note: the opening RQ-traceability work fits this folder; the
> continued session then moved into Chapter 2/4 revision and
> experiment-support git work that by content belongs to phases 08 and 04.

## Goal

Record how the thesis's research questions trace to the signed proposal
(the mapping had just been established: all five proposal questions are
covered, with Q2+Q3 merged into RQ2 and the goal and stability concern
promoted to RQ1 and RQ3), then continue the day's revision work and publish
two experiment-support branches.

## Outcome

- A one-line coverage note was added at the end of the Research Questions
  section, tightened to the author's corrected mapping (RQ0 to proposal
  2.2 Q1; RQ1 to the 2.1 goal / 3.3 success criterion; RQ2 to Q2+Q3; RQ3
  to the 3.2 stability concern; RQ4/RQ5 to Q4/Q5).
- Chapter 2 metrics prose reworked: why a naive total such as the resident
  set misleads for a spilling backend, and how cgroup memory.stat separates
  anonymous memory from page cache; the Fisher and McNemar formulas were
  re-added; stop-slop passes applied on request.
- Figure and table fixes: fig_cost.py updates, Table 3 overflow fixed with
  tabularx (the log's compaction summary also records the earlier
  conversion of all 7 multi-panel figures to vertical layouts within this
  session).
- Chapter 4: knob attribution in 4.3 corrected (compression, write buffer,
  block cache credited to the thesis's own 10k-run characterization at
  512 MB, not the literature); 4.5, 4.7, and 4.8 rewritten to a problem,
  mechanism, consequence structure; the harness description corrected to
  four cores after checking runs.tsv and HOME_SETUP.md (branch
  thesis-setup-corrections, commit 68bd198, published via the GitHub REST
  API because SSH and HTTPS pushes timed out).
- The growth-path spill-signal handoff from the Cowork session was
  executed: stale git locks cleaned, branch ooc-growth-spill-signal
  created, the four C++ files committed path-limited (5ce6155) with the 13
  staged thesis-prose files protected, and the branch published to GitHub
  (remote commit 6c01e533) for the SD-card machine to pull. The
  implementation itself is documented in
  `../04-implementing-prototype/2026-06-28-growth-path-spill-signal.md`.
- Two 4.8 placeholders (host RAM, SD-card model) were left for the author
  to fill in.

## Verbatim prompts (opening; full sequence in the transcript)

1. (2026-06-28T10:22) "User answered Claude's questions:
   ⎿  · Given the RQs actually map cleanly to the proposal, how do you want
   to proceed? → Add a one-line coverage note"
2. (2026-06-28T20:34) "In a claude cowork session we were able to prepare
   an experiment to be run to check if when eviction is trgigered as well
   with slice growth and not only when new slcies are created makes the
   remaining unsuccesful runs complete for the ooc construct. Please upload
   this to github and prepare the experiment on a seperate branch. Ill have
   the same computer run the experiment and also there the details of the
   system can be provided in the new claude code session: # Handoff — OOC
   growth-path spill signal (lever 1e) [...]" (the full pasted handoff is
   in the transcript)
