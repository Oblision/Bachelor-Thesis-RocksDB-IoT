# Pre-Flight — Phase 1 OOC Skeleton

- **Repo:** nebulastream-Out-of-Core-Stream-Processing (own git repo)
- **Branch:** main
- **Commit:** cc754a94c9260d7873868c38b3f1e3f7d32cf234 (matches PHASE1_PLAN addendum HEAD)
- **Working tree:** clean (no uncommitted tracked changes)
- **Timestamp:** 2026-05-20

## Baseline build/test (verified this session, smoke test)
- Build: 577/577 ninja steps, exit 0, zero errors (Docker container)
- CTest: 752/752 unit tests passed, 0 failed (81.8s), labels excluded: Systest, DockerCompose

## Build/test mechanism (NOT native — Docker only)
- Configure dir: `cmake-build-debug` (container paths: /workspace, /vcpkg, triplet arm64-linux-none-local)
- Image: `nebulastream/nes-development:local`
- Build:  `docker run --rm --workdir /workspace -v "<repo>:/workspace" nebulastream/nes-development:local bash -c 'cd cmake-build-debug && ninja <targets>'`
- Test:   same container, `ctest -R <regex> --output-on-failure`

## CI gates (adapted note)
- This is a C++/CMake repo; the TS-centric CI gates in the skill do not apply.
- Upstream NebulaStream CI runs the full build + systest matrix via GitHub Actions (.github/ exists).
- No `tsc`/`jest`. Build gate = Docker ninja; test gate = Docker ctest.

## Strictness / policy
- strictness: strict (clear Critical+High+Medium)
- push: manual (thesis repo; do not push without user confirmation)
- frontend verification: SKIPPED (no UI in scope)
