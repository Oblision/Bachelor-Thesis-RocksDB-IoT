---
name: project-quotex-incident-loop
description: QuoteX end-to-end self-healing pipeline — Vercel error logs → Claude investigator → PR → Vercel deploy → backend writeback closes the incident
metadata: 
  node_type: memory
  type: project
  originSessionId: 4320a1f4-7fa6-4af0-a75f-d71cb4e8ff41
---

The QuoteX backend (Express on Vercel, project `prj_O4uRIpi20FfNBPaQSv93VyHAX7Ka` in team `team_oRZUVxegTsOSNJiYg7ToeAbR`) implements an automated incident self-healing loop:

1. Vercel log drain POSTs errors to `/v1/incidents/ingest` (HMAC-SHA1) → row created with `status='triage_pending'`.
2. Admin clicks "investigate" in the magic-link UI → backend POSTs `workflow_dispatch` to the `incident-investigator.yml` GH Action → row transitions to `investigating`.
3. The investigator agent (Opus 4.7, `claude-code-action@v1`, 200-turn ceiling, runs on the self-hosted runner — see [[project-self-hosted-runner]]) checks out master, builds a prompt via `scripts/fetch-incident-and-build-prompt.ts`, fixes the bug, adds a regression test, opens a draft PR on the `incident/<uuid>` branch, and writes back to `/v1/incidents/<id>/investigation-complete` (HMAC-SHA256). Row → `pr_opened`.
4. Human reviews + merges the PR. Vercel's native GitHub integration auto-deploys master (NOT a GH Action — `deploy.yml` was vestigial and was removed in PR #17).
5. **NEW (PR #18, 2026-05-14):** `deployment-status-reporter.yml` listens for Vercel's `deployment_status` event, resolves the incident UUID from the deployed SHA (two strategies: regex on commit message, fallback to `gh api commits/<sha>/pulls` head ref), and writes back to `/v1/incidents/<id>/deployment-complete`. Row → `fix_deployed` or `deploy_failed`.

Status enum (DB CHECK constraint on `incidents.status`, migration 078): `triage_pending`, `investigating`, `pr_opened`, `investigation_failed`, `fix_deployed`, `deploy_failed`, `resolved`, `dismissed`, `false_positive`, `rate_limited`. The `resolved` transition from `fix_deployed` is NOT yet automated — manual or future follow-up.

Shared HMAC secret for both writeback endpoints is `INVESTIGATOR_WEBHOOK_SECRET`. Helper scripts: `scripts/post-investigator-writeback.sh` and `scripts/post-deployment-writeback.sh`.

**Why:** Avoid waking the founder for routine production errors; agent fixes bugs autonomously while a human only owns the merge decision.

**How to apply:** When changing the incidents controller, routes, or related schema, preserve the HMAC contract and the gated `.eq('status', <prior>)` transition pattern. When debugging stuck rows, check which terminal status they're in to identify which stage failed.
