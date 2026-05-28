# LEARNINGS — Increment C (emit-decouple)

## Decisions (with rationale)
- **Seam (a), inside the store override, not the handler.** `WindowBasedOperatorHandler::checkAndTriggerWindows` is
  join-shared; the spillable store's `getTriggerableWindowSlices` already holds both the true frontier (arg) and the
  quiescence signal (`lastSeenWatermark`) as adjacent statements — so the fix is one clamped subtraction with zero join
  blast radius and no new setter/virtual. (Resolved OPEN #1 + #4.)
- **emit_lag = event-time ms, default 0.** Default 0 ⇒ provable byte-identity (regression safety net); event-time fits
  the watermark arithmetic the mechanism uses.
- **OPEN #3 = "build a correct guard now" (user).** The architects' proactive unspill-only guard was shown unsound
  during implementation (re-spill race + JIT cache-HIT). The correct mechanism is unspill-on-write **+ pin until emit**.

## Patterns (reusable — persisted to nes_cpp_idioms)
- Lagged-emit at the store override: saturating `globalWatermark - emitLag` on the BASE emit call, TRUE watermark on
  the quiescence CAS. Base store unchanged; default 0 = identity.
- **Pin-don't-just-unspill** when re-residentizing a still-writable slice on the worker path: the worker re-caches the
  raw `HashMap*` and a later JIT SliceCache HIT is uninterceptable, so the slice must stay resident until it emits.
  Pin = `pinnedKeys` checked at the single chokepoint `pickAndReserveColdSlice`, dropped at emit/GC/deleteState.

## Pitfalls (root cause → fix)
- **`-Wunused-private-field` (`-Werror`)**: a stored member with no reader in the same compile is a hard build break →
  land the member and its reader (the seam) in one commit. (Hit on the first PR1 build; restructured the PR split.)
- **Naive unspill-on-write guard is racy**: unspilling a still-filling band slice can't mark it emitted, so the spiller
  re-spills before the worker writes → the same `std::terminate`. Root cause: spill-safety of a slice ≡ "its cached
  HashMap* is gone", which holds for cold slices only because their SliceCache entry was LRU-evicted; a late write
  re-establishes that entry. Fix: pin until emit.
- **Masked-effect testing**: `pinnedKeys`' only effect (skip in `pickAndReserveColdSlice`) is dominated by `emittedKeys`
  once the slice emits → black-box-untestable directly. Assert the gated transition instead, and say so in the test.

## Process
- Reused the existing `SPILL_LEAD` decoupling that the Increment-B concurrency test already simulated — Increment C
  turns that *manufactured* cold-but-unemitted regime into real pipeline behavior, so the threading was pre-validated.
- The reachability of late records into the store (the guard's necessity) is deferred to redo-E0 as an empirical check,
  not a code assumption — and the pin makes the increment robust regardless of the answer.

## Outcome
3 commits (7b8d5f0301, e64e085a25, 1f30a75822) on 5e4fd3cd9f; 828/828 under -j8 (820 + 8); 1 implementation
iteration, 1 fix iteration; super-review SHIP AS-IS. Not pushed (manual policy).
