---
title: RocksDB microbenchmark setup (db_bench, Jin et al. parameter space)
date: 2026-03-02
date_confidence: approximate
phase: 05-testing-prototype
level_of_use: 3
model: not recorded in transcript (implementation-era desktop session; likely Claude Opus 4.7/4.8, predates the Fable 5 sessions)
tool: Claude desktop application (Cowork mode)
share_link: n/a (desktop session; transcript export attached on sync)
artifacts:
  - none confirmed within the captured transcript (the RocksDB/db_bench source build was still in progress when the transcript ends)
  - decision record - sweep harness around RocksDB's native db_bench instead of a custom C++ benchmark, over the Jin et al. knobs
  - follow-on, outside this transcript - the Microbenchmark-BA suite (run_benchmarks.py, config.py, parse_output.py, Dockerfile); its AI-assisted creation is logged in Microbenchmark-BA/RESEARCH_LOG.md, entry "2026-03-02 - Project Setup & Benchmark Architecture Design"
---

> **Reconstruction, not transcript.** This entry records the verbatim user
> prompts and the session's outcomes. The assistant's responses are not
> reproduced here; the exported desktop-app transcript is the primary record
> and is attached to this folder on the next sync to the public fork.

## Goal

Kick off the project's first practical step after the advisor meeting:
set up RocksDB and build a standalone microbenchmark to collect initial
data before touching NebulaStream (MS2 in the proposal timeline). The
uploaded thesis proposal (DIMA_Thesis_Proposal_V2.4_signed.pdf) provided
the context; the parameter space came from prior LSM-tree research
(Jin et al.).

## Outcome

- The assistant read the thesis proposal, explored the NebulaStream
  codebase via a subagent, and summarized the project state (all operator
  state in memory via HashMapSlice / WindowSlicesStoreInterface, no
  RocksDB integration yet).
- After the user supplied the Jin et al. knobs (min_level_to_compress,
  compression_type, level0_slowdown_writes_trigger, write_buffer_size,
  observed via compression_ratio per level) and answered a clarifying
  question, the approach was fixed: use RocksDB's native db_bench and
  build a scripting harness that sweeps the parameter space, rather than
  writing a custom C++ benchmark.
- The session then began building RocksDB from source to obtain db_bench.
  The parallel build ran out of memory and was restarted with reduced
  parallelism; the captured transcript ends at that point, with no
  closing summary. No created files are visible in the transcript itself.
- The completed benchmark framework (Python orchestrator around db_bench,
  Docker memory/CPU constraints, resume and OOM handling, CSV metric
  parsing) is documented in Microbenchmark-BA/RESEARCH_LOG.md under
  2026-03-02 as AI-assisted, with the student designing the parameter
  space and methodology. That log entry and the repo's initial commit
  (2026-03-02) anchor the date of this session; the transcript itself
  carries no timestamp, hence date_confidence: approximate.

## Verbatim prompts

1. (with uploaded file DIMA_Thesis_Proposal_V2.4_signed.pdf)
   "I had a recent talk with my advisor. We want to first start out with
   the project by building a microbenchmark in rocksdb to collect initial
   data. See my thesis for more information or what we are doing. I have
   linked the current nebulastream as a context. We will be working on
   there soon, after we once have the microbenchmark setup and a collected
   some data. I will need to some help on setting up rocksdb and creating
   such a micro benchmark. For the benchmark I will tell you which "knobs"
   we will want to turn to get a better feel for the system through some
   research I had done."
2. "The evaluation uses a parameter space indicated by prior research on
   LSM-Trees. Jin et al. found that the most important parameters
   aremin_level_to_compress and compression_type,
   level0_slowdown_writes_trigger, write_buffer_size  and their resulting
   effects are indicated by the compression_ratio per level."
3. An answer to an in-session clarifying question (settling on db_bench
   plus a sweep harness) is referenced by the assistant's replies but its
   verbatim text is not captured in the transcript export.
