---
title: Suitability of the Yahoo Streaming Benchmark, and the workload characterisation that replaced it
date: 2026-02-04
phase: 03-literature-research
level_of_use: 4
product: ChatGPT
manufacturer: OpenAI
version: GPT-5.2
purpose: Assessing whether the Yahoo Streaming Benchmark, as used in the 2020 NebulaStream evaluation, suited this thesis; and characterising the workload that replaced it.
record: pdf/2026-02-04-ysb-benchmark-suitability.pdf (browser capture)
---

## Purpose

The conversation began as a question about whether the Yahoo Streaming
Benchmark (YSB), used in the 2020 NebulaStream paper, was a suitable evaluation
vehicle for this thesis. It concluded that YSB alone does not exercise
out-of-core state under memory pressure, and moved on to how the evaluation
workload should instead be characterised.

## Outcome

This conversation substantially shaped the evaluation design of this thesis and
is declared at level 4 for that reason.

The model argued for describing workloads by their stress characteristics rather
than by a named benchmark, and proposed a parameterised synthetic generator over
key-space size, window size, event rate, and skew factor, with memory bounded by
a fixed container RAM limit and swap disabled for reproducibility. The
evaluation reported in this thesis follows that characterisation: synthetic
workloads, Zipf-skewed keys, varying key cardinality, and a memory budget
enforced through cgroup limits with swap disabled.

Not everything proposed was adopted. The bursty-ingestion phase, the explicit
hot/cold key split, and the suggested name "Parameterized Stateful Streaming
Workloads" do not appear in the thesis. YSB itself was not used. The concrete
parameter values, the choice of RocksDB's db_bench as the harness, and the knobs
swept were settled later against Jin et al. and are recorded in
`../05-testing-prototype/2026-03-02-rocksdb-microbenchmark-setup.md`.

## Record

[`pdf/2026-02-04-ysb-benchmark-suitability.pdf`](pdf/2026-02-04-ysb-benchmark-suitability.pdf)

> **What this capture is.** A full-page browser capture of the conversation,
> not a parsed export. Long user prompts are shown collapsed behind ChatGPT's
> "Show more" control and are therefore truncated in the capture. The version
> above is the model current in ChatGPT on the date of the conversation; the
> variant (Instant / Thinking / Pro) is not recorded by the tool.
