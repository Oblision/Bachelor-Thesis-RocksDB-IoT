# PRE-FLIGHT — Phase 2 step 2c0 (handler-owned registry ownership refactor)

- branch: feat/ooc-phase1-spill-skeleton
- commit: 05c8520dbb (feat(spill): pace terminal flush — unspill one window at a time (Phase 1 L1))
- docker image: nebulastream/nes-development:local-rocksdb
- build dir: cmake-build-debug
- baseline build: `ninja` exit 0 ("no work to do" — warm, in sync with HEAD)
- baseline tests: `ctest -j8 -LE 'Systest|DockerCompose' --timeout 200` → **100% passed, 0 failed out of 835** (1 disabled), 87 s
- regression yardstick: **835 passing**, build green
- working dir for this run: `.planning/dev-eng-cpp-phase2/2c0/`
- locked brief: `.planning/dev-eng-cpp-phase2/DESIGN-BRIEF.md` (L1–L8, G3=Option 2; step 2c0 in §5; leak gate §7)
- supporting: `.planning/dev-eng-cpp-phase2/exploration/EMITTEDWINDOW-LIFETIME-AUDIT.md` (the RED audit this step fixes)
- push policy: manual (no push; present commits for review)
- strictness: strict (clear Critical+High+Medium)
- implementation_iteration=1 (max 3); fix_iteration=1 (max 5)
- timestamp: 2026-05-24
