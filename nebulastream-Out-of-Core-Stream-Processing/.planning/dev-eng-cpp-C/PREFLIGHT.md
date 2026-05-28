# PRE-FLIGHT — Increment C (Emit-Decouple)

branch: feat/ooc-phase1-spill-skeleton
commit: 5e4fd3cd9f

- **Docker image:** nebulastream/nes-development:local-rocksdb
- **Build dir:** cmake-build-debug
- **Working dir (this run):** .planning/dev-eng-cpp-C/
- **DESIGN_BRIEF:** .planning/dev-eng-cpp-C/DESIGN_BRIEF.md (LOCKED + OPEN threaded into all agents)

## Baseline build
`ninja` → exit 0, "ninja: no work to do" (HEAD is the already-built Increment B commit).

## Baseline tests (regression yardstick)
`ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`
- **100% passed, 0 failed, out of 820** (821 total, 1 disabled: SpecificSequenceTest.testDelimiterThatIsMoreThanOneCharacter).
- Total test time: ~87 s.
- **Contract:** this run may not finish with < 820 passing or a broken build.

## Working-tree note
The E0 smoke systest (`SpillSmoke.test`) was moved to `.planning/eval-phase/SpillSmoke.e0-original.test`
(gitignored) before this run to clean the tree — it targets the pre-decouple behavior and will be rewritten
for the redo-E0 step that follows this increment.

## Iteration counters
- implementation_iteration = 1 (max 3)
- fix_iteration = 1 (max 5)
- strictness = strict; push = manual

Timestamp: 2026-05-23
