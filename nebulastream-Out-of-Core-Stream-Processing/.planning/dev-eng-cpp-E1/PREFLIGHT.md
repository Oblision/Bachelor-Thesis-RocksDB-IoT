# E1 PRE-FLIGHT

branch: feat/ooc-phase1-spill-skeleton
commit: ad5d2fbc8d   (Phase 2 2d HEAD — base for E1)
docker_image: nebulastream/nes-development:local-rocksdb
build_dir: cmake-build-debug

## Baseline build
ninja: exit 0 (green; ran under `set -e` before ctest)

## Baseline tests
ctest -j8 -LE 'Systest|DockerCompose' --timeout 200
result: 100% tests passed, 0 tests failed out of 875
log: /tmp/e1-baseline.log

## Regression yardstick
unit tests must stay >= 875. New E1 tests add to it.

timestamp: 2026-05-25 14:04 CEST
