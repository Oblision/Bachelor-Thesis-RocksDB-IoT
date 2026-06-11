---
name: project-self-hosted-runner
description: "GitHub Actions self-hosted runner on the user's local Ubuntu desktop, registered to Oblision/QuoteX, running as systemd service"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4320a1f4-7fa6-4af0-a75f-d71cb4e8ff41
---

A self-hosted GitHub Actions runner is installed in `~/actions-runner/` on `oblision-MS-7C75` (Ubuntu 24.04, x86_64) and registered to the **Oblision/QuoteX** repo only. Runs as systemd unit `actions.runner.Oblision-QuoteX.oblision-MS-7C75-claude.service` with `Restart=always`. Labels: `self-hosted,linux,x64,claude`. Set up 2026-05-14 via PR #16.

All three repo workflows use `runs-on: self-hosted` and execute on this machine: `incident-investigator.yml`, `test.yml`, and (until PR #17 lands) the vestigial `deploy.yml`. Sleep is disabled via `systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target` so the runner is always reachable.

Jobs serialize on the single runner — `test.yml`'s two parallel jobs run back-to-back instead of concurrently. Acceptable for current volume; a second runner directory could be added if it becomes a bottleneck.

**Why:** Eliminate GitHub Actions minute consumption for an Opus-heavy investigator agent. Solar + battery makes the always-on cost negligible — see [[user-solar-battery]].

**How to apply:** When debugging CI runs on this repo, check the local runner's logs at `~/actions-runner/_diag/` and `journalctl -u actions.runner.Oblision-QuoteX*` rather than assuming GitHub-hosted infra. When suggesting changes to GH-hosted-runner-specific features (e.g. tool cache paths, preinstalled software), verify they work on a self-hosted Ubuntu box.
