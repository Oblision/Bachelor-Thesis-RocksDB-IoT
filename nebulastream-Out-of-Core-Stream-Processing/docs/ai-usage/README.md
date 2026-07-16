# AI Tool Usage

This folder archives conversations with AI tools from the bachelor's thesis
*Out-of-Core Stream Processing in NebulaStream: A RocksDB-Based State Backend
for Memory-Constrained Edge Devices* at TU Berlin (DIMA chair).

Two tools were used, in two distinct periods:

- **Phases 1-3 — ChatGPT (OpenAI), October 2025 to February 2026.** These belong
  to the accepted thesis *proposal*, not to the thesis itself, and predate this
  archive. The topic came from the DIMA topic list; the choice of RocksDB over
  LMDB as the storage backend followed the advisor's recommendation.
- **Phases 4-8 — Claude (Anthropic), March to July 2026,** via Claude Code and
  the Claude desktop application. This is the thesis proper.

Grammarly was used for spelling, grammar, and phrasing during writing and
revising. It is not a conversational tool and has no prompt or transcript to
archive in the format used here; it appears in `misc/declaration-of-aids.tex`
without a link.

`misc/declaration-of-aids.tex` in the thesis LaTeX project names the product,
manufacturer, software version, and purpose per phase, as the declaration of
authorship requires, and links to each folder below.

## Scope and completeness

Coverage is best-effort rather than guaranteed complete. Phase 4-8 entries were
written up from Claude Code session logs and desktop-app transcripts;
conversations for which no log or transcript survived are not represented, and
where a session's model identifier was not captured the entry records it as not
known rather than reconstructing it. The archive documents what could be
recovered, and each entry states plainly what it is.

Once committed, entries are not edited; corrections go in a follow-up entry that
links back to the original.

## Folder layout

| Phase | Tool | Folder |
|-------|------|--------|
| 1. Finding and nailing down the topic | ChatGPT | [`01-topic-finding/`](01-topic-finding) |
| 2. Understanding the topic | ChatGPT | [`02-understanding-topic/`](02-understanding-topic) |
| 3. Literature research | ChatGPT | [`03-literature-research/`](03-literature-research) |
| 4. Implementing the prototype | Claude | [`04-implementing-prototype/`](04-implementing-prototype) |
| 5. Testing the prototype | Claude | [`05-testing-prototype/`](05-testing-prototype) |
| 6. Planning the thesis | Claude | [`06-planning-thesis/`](06-planning-thesis) |
| 7. Writing the thesis | Claude | [`07-writing-thesis/`](07-writing-thesis) |
| 8. Revising the thesis | Claude | [`08-revising-thesis/`](08-revising-thesis) |

[`proposal-phase.md`](proposal-phase.md) records the split between the proposal
period and the thesis, and lists two claude.ai conversations from the proposal
period that are shared publicly but not yet placed in a phase folder.

## File naming

`YYYY-MM-DD-short-topic-kebab.md`

Examples: `2026-05-11-summary-rewrite.md`, `2026-05-12-uml-class-diagram.md`.

## Per-entry contents

Entries take one of two shapes, depending on what record survives.

**Phases 4-8** use [`_template.md`](_template.md) as a skeleton:

1. YAML frontmatter (date, phase, level of use, model, tool, share link if available).
2. Goal of the conversation.
3. Outcome or artifact produced.
4. The verbatim prompt.
5. The full transcript.

**Phases 1-3** have no transcript. ChatGPT publishes individual conversations
rather than whole projects, so there is no public link to the project these ran
in. Each entry therefore carries frontmatter (date, phase, level of use,
product, manufacturer, version, purpose) plus a browser capture of the
conversation in the folder's `pdf/` subdirectory. A capture shows the model's
replies in full, but ChatGPT collapses long prompts behind a "Show more"
control, so those prompts are truncated in the capture. Versions for these
phases are the model current in ChatGPT on the date of the conversation; the
variant (Instant / Thinking / Pro) is not recorded by the tool.

## Levels of use (DIMA scale)

| Level | Characterization | Examples |
|-------|------------------|----------|
| 1 | For inspiration only | Topic suggestions, wording, spell check |
| 2 | Supplementary | Proposed RQs, term explanations, summaries |
| 3 | Supportive | Iterative writing dialogue, code review, test writing |
| 4 | Content-shaping | Background generation, model fills outline, multi-line code |

Source: TU Berlin DIMA thesis template, adapted from [Leuphana](https://pubdata.leuphana.de/server/api/core/bitstreams/f4c7f7f2-3974-410c-a07b-4e545390561e/content) (pp. 20, 24).

> **A note on what the level of use captures.** The DIMA scale measures
> the *kind* of AI assistance, not the share of intellectual work in a
> session. Substantive contributions made by the author (decisions,
> corrections, source audits, restructures, methodology fixes) are
> documented in each entry's Goal and Outcome sections, and visible in
> the verbatim prompts.

## Adding a new entry

1. End the conversation. If on claude.ai, click Share and copy the public URL.
   ChatGPT project conversations have no public URL, so the record in the entry
   is the archive.
2. Copy the conversation as markdown ("Copy as markdown" on claude.ai, or paste
   from Claude Desktop). Where no transcript can be exported, capture the
   conversation and place the file in the phase folder's `pdf/`.
3. Save as `<phase>/YYYY-MM-DD-short-topic.md`, filled out from `_template.md`.
4. Commit and push: `git commit -m "ai-usage: <phase>: <topic>"` then `git push`.

`misc/declaration-of-aids.tex` already cites each phase folder, so no `.tex`
edit is needed when adding new entries inside an existing phase. Adding a new
*tool* to a phase does require a new line in that table.

---

Author: Benjamin Sainsanaa Steinbüchel · b.steinbuechel@campus.tu-berlin.de
