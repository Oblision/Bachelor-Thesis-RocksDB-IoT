---
name: feedback-match-baseline-config
description: "When extending a sweep, explicitly pin every config knob to match the baseline — never trust binary defaults to be unchanged"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c56aafd1-c2ba-4076-a294-e96674067178
---

When adding new cells (sweeps, ablations, follow-ups) on top of an existing matrix, **explicitly pass every config knob** that was originally set — even ones the baseline got "implicitly". Binary defaults can change across rebuilds (especially after branch swaps), silently moving a baseline reference under your new cells. A single mismatched knob (e.g., page_size 8192 vs 1024 → 8× pool pressure) can produce a clean-looking, fully wrong "finding" — singular-optimum curves, knife-edge thresholds — that survives review until someone notices the gauge output.

**Why:** The thesis OOC soft-threshold sweep on 2026-06-09 produced an apparent "soft=50 is the unique survival point with two-sided failure" result; refuted within an hour after the user pushed back. Root cause: the b_ baseline ran with `PAGESIZE=8192` (explicit at launch); the new sweep cells used `PAGESIZE=""` and inherited the rebuilt binary's default of 1024.

**How to apply:**
- Before launching a follow-up sweep, dump every `--worker.*` arg from a baseline log and reproduce them verbatim in the new run-cell args, even when they look like defaults.
- After the first rep, sanity-check at least one diagnostic (HMGAUGE pageSize, POOLGAUGE, RDB stats) matches the baseline — same workload should produce the same gauge.
- If users push back ("this doesn't make sense"), treat that as a strong prior to check for confounders before re-explaining the mechanism. [[user-skeptical-eye]]
