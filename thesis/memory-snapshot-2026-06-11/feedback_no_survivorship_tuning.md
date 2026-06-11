---
name: feedback-no-survivorship-tuning
description: "For thesis eval — never tune the system's config on the same workloads you then evaluate on, even via a small \"pre-tune\" sweep. Pick configs by first principles + prior independent work; let the heatmaps show what they show."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

When designing an evaluation phase for the thesis, never run a "find the best config" sweep on the workloads we're then going to evaluate against. That's survivorship bias — the apparent OOC-vs-baseline gap is then a function of "ours got optimised, theirs didn't" rather than a real-system comparison.

**Why:** the user explicitly rejected my proposed pretune-phase (small sweep on uniform5m at scale=5M/10M to find best P, POOL, soft/hard before launching Nexmark). They want the Nexmark phase to evaluate the implementation at one principled, well-defended configuration, where every knob is justified by independent prior work (Microbenchmark-BA on RocksDB, RocksDB docs, the H4 OOM-ceiling formula) — not by overfitting against the eval dataset. If the resulting heatmaps show ooc failing at high state, that IS the finding, not a bug to engineer around.

**How to apply:**
1. For any "what config should we run the eval at?" decision, the answer must come from sources independent of the eval data: published prior work, the system's documentation, theoretical formulas (e.g., H4's `2·wbuf + cache + ~100 MB` ceiling → 70 %/90 % watermarks), or characterisations done on a different workload (Microbenchmark-BA tested RocksDB on db_bench, not on NES queries — those results are fair game).
2. Document the defence per knob inline — every `FROZEN_*` value carries a one-line justification that cites its source.
3. Baseline (in-mem) gets the same independent justification — never advantage one side over the other.
4. If the principled config produces "ooc fails everywhere at high state" heatmaps, present that honestly. The 10k characterisation already documents *why* (rc=141 buffer-pool starvation, see [[project-ooc-err-policy]]); the realistic-workload Nexmark heatmaps then say "and here's how that translates to canonical streaming queries."
