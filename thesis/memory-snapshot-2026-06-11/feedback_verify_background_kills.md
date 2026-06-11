---
name: feedback-verify-background-kills
description: "When stopping a background sweep/launcher, VERIFY it actually died (pgrep + docker ps) — never trust `kill $(cat pidfile)` alone; nohup/fork can mismatch the saved PID."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

When asked to stop a long-running background job (sweep launcher, etc.), **kill it and then verify it's actually dead** before reporting it stopped or starting anything else on the same machine.

**Why:** On 2026-06-06 a Nexmark sweep launched 2026-06-01 with `nohup bash launch.sh 4 & echo $! > launch.pid` had its real launcher fork to a different PID than the one captured in `launch.pid` (saved 3379721, actual 3379726). When the user said "stop and reassess," `kill $(cat launch.pid)` + a one-time `docker stop` of the then-current cells looked successful — but the real launcher kept running for ~5 days as a phantom, spawning cells, and silently shared pinned CPU cores with a later lag-validation measurement run, contaminating it. The contamination only surfaced when `docker ps` showed 5 containers during a run that should have had 1.

**How to apply:**
1. After any stop, run a positive check: `pgrep -af launch.sh`, `docker ps --filter ancestor=...`, check `uptime` load. Don't declare "stopped" until these are empty.
2. Distrust `kill $(cat pidfile)` for nohup'd jobs — the pidfile may hold a wrapper PID. Prefer killing by matched command (`pkill -f`), by process group, or by enumerated PIDs from `ps`.
3. **`pkill -f "launch.sh"` self-matches the killing shell** (its own command line contains the pattern) and SIGKILLs itself mid-run. Use the bracket trick (`pkill -f "[l]aunch.sh"`) or collect explicit PIDs via `ps | grep -v grep | awk '{print $1}'` then `kill -9`.
4. Before a CPU-pinned measurement run, confirm `docker ps` shows ONLY your cell(s) — a stray container on the same cpuset silently corrupts timing/throughput/memory numbers.
5. When launching something you'll later need to kill, capture the PID robustly (e.g. write it from inside the launched script via `echo $$`), not from the parent's `$!` after nohup.
