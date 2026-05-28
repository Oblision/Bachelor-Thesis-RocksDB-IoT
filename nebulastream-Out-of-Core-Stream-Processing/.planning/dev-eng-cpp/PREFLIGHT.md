# PREFLIGHT — Phase 1 (L1 paced terminal-flush emit, Option 1: bound the unspill)

branch: feat/ooc-phase1-spill-skeleton
commit: 08a107ca38
image: nebulastream/nes-development:local-rocksdb
build_dir: cmake-build-debug

## Git
Clean (`git status --porcelain` empty; Phase-0c harness/results live under gitignored `.planning/`).

## Baseline build
Green. Last full build `brin198s4` (Phase-0b validation, same commit, tree unchanged since): `ninja` exit 0 (BUILD_OK, unpiped under set -e).

## Baseline tests (the regression yardstick)
**831 passed / 0 failed** out of 831 (`ctest -j8 -LE 'Systest|DockerCompose' --timeout 200`, REG_EXIT=0).
Only skip: SpecificSequenceTest.testDelimiterThatIsMoreThanOneCharacter (Disabled). Known flake to absorb with --timeout 200: offline-cli-test.

## Regression contract
Run must finish with >= 831 passing and a clean build. New Phase-1 unit tests add to this.

## Strictness / push
--strictness=strict (clear Critical+High+Medium) · --push=manual (no push, no co-author trailer; user reviews).

## Locked context (from the task brief)
- HOST-ONLY: no Nautilus/JIT/codegen edits; JIT probe keeps receiving one EmittedAggregationWindow per window; no sliding/varsized.
- Option 1 scope: bound the UNSPILL to one window at a time; KNOWINGLY do NOT bound the async in-flight emitted set (that's the Option-2 escalation, only if measurement still OOMs). Do NOT build probe-wait/backpressure this increment.
- Preserve async-probe-drain invariant (emittedKeys, mark-emitted before exposing HashMap*, freed only by base GC after watermark).
- Preserve eviction-mutex discipline (RocksDB I/O outside evictionMutex under spillInProgress+ReservationGuard; metrics latency locks not nested under evictionMutex).
- Terminal path still flushes the ENTIRE backlog (all windows/keys/correct SUM); emit_lag=0 byte-identical.
- Phase-0b SpillMetrics must keep working; peakResidentBytes is the key metric this phase flattens.
- Likely seam (architects confirm/refine): a base virtual e.g. ensureResident(slices) (no-op in Default, per-window unspill+markEmitted in Spillable) called by triggerSlices just before each window is dereferenced; getAllNonTriggeredSlices() drops the eager whole-band unspill. Base virtual is in-bounds (Increment A already un-final'd the base + added virtuals).

## Post-orchestra manual validation (NOT a ctest gate)
`.planning/eval-phase/phase0c/run-rss.sh F20_on320 320 on 16000 4 8 2000000 1 0 0` → success = rc 137→0, process peak ~310-315 MB.

## CI
No external CI; binding gate = Docker ninja + ctest.

## Timestamp
2026-05-23 (post Phase-0c baseline).
