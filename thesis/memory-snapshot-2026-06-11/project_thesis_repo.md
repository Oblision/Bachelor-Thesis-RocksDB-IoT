---
name: project-thesis-repo
description: "The canonical thesis repo is the private wrapper Bachelor-Thesis-RocksDB-IoT — vendor the NES OOC source, do not use the retired public fork"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

The bachelor thesis lives in the **private wrapper repo** `git@github.com:Oblision/Bachelor-Thesis-RocksDB-IoT.git` (default checkout: `/home/oblision/Desktop/Bachelor-Thesis-RocksDB-IoT`). NES OOC source is **vendored** under `nebulastream-Out-of-Core-Stream-Processing/` (plain files, not a submodule). The 10k OOC eval harness lives under `nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/e3big/` along with `phase0c/SpillSmokeVoid.test`. Top-level `HOME_SETUP.md` is the authoritative runbook.

The **public fork** `git@github.com:Oblision/nebulastream-Out-of-Core-Stream-Processing.git` (old checkout: `/home/oblision/Desktop/Bachelor Arbeit/code/nebulastream-Out-of-Core-Stream-Processing`) is **retired**. Its branches were tied to open upstream PRs; pushing there can leak to upstream. Do not commit, push, or rely on the fork's branches (e.g. `feat/ooc-phase1-spill-skeleton`) — the HOME_SETUP.md committed there is stale; the live one is in the wrapper repo.

**Why:** the wrapper is private and combines NES source + thesis docs + harness in one place; the fork is public and entangled with upstream.

**How to apply:** for any thesis / OOC eval work, default to the wrapper repo. If something looks missing in the old fork, it lives in the wrapper. Never `git push` from inside `nebulastream-Out-of-Core-Stream-Processing/` regardless of which clone you're in.
