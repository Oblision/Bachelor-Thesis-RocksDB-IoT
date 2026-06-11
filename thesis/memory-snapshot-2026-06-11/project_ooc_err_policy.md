---
name: project-ooc-err-policy
description: "In the OOC 10k sweep, status=err (rc=141, NES buffer-pool exhaustion) is treated as legitimate failure-mode data alongside oom — not a harness bug"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

For the OOC 10k sweep ([[project-thesis-repo]]), `status=err` with `rc=141` is **expected and kept as data**, not an error to fix. It indicates NES's global buffer pool (`POOL=16000` in `.planning/eval-phase/e3big/gen-matrix.sh`) could not allocate a buffer within 1 s — i.e. the in-memory pipeline backpressure outran spill release.

**Observed structural pattern at state=5M tuples (Sweep A, r1):**
- `cap=320`: oom for p∈{1,2,8,16}; ok for p=4
- `cap=512`: oom for p∈{1,16}; **err (rc=141) for p∈{2,4,8}**

So the failure mode flips from OS-OOM (rc=137) to NES-pool-starvation (rc=141) in a narrow strip where the cap is generous enough to avoid SIGKILL but not generous enough for the pool to keep up. Over the full 54-rep sweep this produces ~3 × 54 = **162 err cells (~1.6 %)** — all `A_p{2,4,8}_s5000k_c512_uniform_r*`.

**Why:** this is real RQ1/H4 evidence — the survival ceiling has two distinct failure modes, not one — so the err cells carry information. The HOME_SETUP §11 hand-off criterion (\"no status=error\") was written before this boundary was characterized and should be relaxed to \"no unexplained status=error\".

**How to apply:**
1. Don't treat the rc=141 cells as defects — they are signal, not noise.
2. In `analyze.py`, group `err` (rc=141) with `oom` (rc=137) for survival/residency plots — both mean \"did not survive at this config.\" Keep them distinct in any failure-mode breakdown.
3. Decision made 2026-05-28 19:23 sweep (PID 618064); confirmed by user.
4. If a future sweep changes `POOL` or adds a partition-watermark fix, this memory may be stale — the err count is sensitive to those knobs.
