---
title: Citation accuracy audit of all 35 cite sites, hand-edit list for Overleaf
date: 2026-07-14
phase: 08-revising-thesis
level_of_use: 3
model: Claude Fable 5
tool: Claude desktop application (Cowork mode)
share_link: "n/a (desktop session; transcript export attached on sync)"
duration_wall_clock: one working session, 2026-07-14
artifacts:
  - thesis/CITATION-AUDIT.md (full audit report, dated 2026-07-14 in its header)
  - hand-edit list (file + line + old/new text) for chapters 1, 2, 5, 6 and bibliography.bib, applied by the author manually in Overleaf
  - new BibTeX entries provided: dong2021, rocksdb-compression, fisher1935, mcnemar1947, vitter2001 (optional), storm886 (corrected)
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.
> Desktop session: "Citation accuracy review" (local_980c6d82).

## Goal

Check every `\cite` in the thesis (35 occurrences, 24 bibliography entries)
against what the cited work actually says, because some citations appeared to
be used haphazardly. Produce a verdict per cite site and concrete fixes.

## Outcome

- Full audit written to `thesis/CITATION-AUDIT.md`: 19 sites clean, 5 real
  mismatches, plus caveat-level overreaches. Sources were verified against
  full texts, abstracts, JIRA records, and DBLP (three parallel verification
  agents plus direct fetches).
- The five mismatches: horowitz2014 cited for cloud bandwidth (it is a
  chip-energy keynote); marcu2022 cited for a NebulaStream fact the paper
  never states; jin2022 cited twice for a CPU-cost claim it never measures;
  wei2025 mischaracterized in Related Work; fragkoulis2024 described as a
  survey of evaluation practice when it surveys functional evolution.
- Separate flag: pamarthi2024's venue (IJSRA) is unindexed and widely flagged
  as predatory; recommendation to delete the sentence and the bib entry.
- Because the author applied fixes by hand in Overleaf, the assistant
  delivered a per-file hand-edit list with line numbers and old/new text,
  tagged [must]/[should]/[opt], plus bib corrections (graefe page range,
  Scotty volume/pages/DOI, storm886 title) and verified new entries
  (dong2021 for the "industry standard" claim; rocksdb-compression, fetched
  and checked before recommending; fisher1935 and mcnemar1947 for the
  statistical tests, verified against Wiley/PubMed/Springer).
- Follow-up Q&A: what dong2021 and rocksdb-compression refer to, how to do a
  project-wide search for "pamarthi" in Overleaf, and whether the joint
  jin2022/wei2025 cites are legitimate (answer: yes, with the reasoning
  recorded in the transcript).
- All accept/reject decisions and the manual application of the edits were
  the author's.

## Verbatim prompts

1. "we need to go over the citations once more and check every single
   reference if they relate to their citation. It seems some are haphazardly
   used but dont directly relate to the citation. We need to make sure that
   is cristal clear. Please develve into each citation and determine if the
   sue is justified and why."
2. "can you give me the change with their location and line number? I'll have
   to do this by hand in overleaf"
3. "we don'T have dong 2021, what is it refering to?"
4. "no, that's good can you give me the bibtex entry as well?"
5. "what is rocksdb-compression citation?"
6. "I want to add the other two statistical references please give me the
   locations and bibtex insertions as well"
7. "can you give me the complete strom one?"
8. "how can I check if pamarthi is present anywhere through overleaf?"
9. (pasted the §2 write-amplification passage, then:) "Jin, H. and Wei R.
   etc. are used together a lot here. Is that fine? They do look at different
   aspects"
