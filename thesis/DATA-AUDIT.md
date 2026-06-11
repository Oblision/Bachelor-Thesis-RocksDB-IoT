# Final Data Audit — what the thesis claims, on which data, tested to what extent
2026-06-12. The pre-write-up assertion that each RQ is reliably answerable.

## Authoritative datasets (and what was demoted)
| dataset | labels | role |
|---|---|---|
| Flagship 512 three-variant | b_/p_ (~330 runs) | RQ1 headline, unbounded arch, checksum |
| 2D cap×pool matrix | L_ (~700) | unbounded-arch windows, in-mem baselines |
| Bounded final grid + geometry | W_, boundary, N1 N_ (~600) | bounded-arch windows (soft=CAP/10) |
| Matched-granularity controls | U8 U_, I8 (~150) | three-factor attribution, fair cost table |
| Investigation chain | S_, F_, B3_, P_, GA/GC, X_ (~700) | RQ3 mechanisms, falsifications |
| DEMOTED: V_ (282) | soft=pool/3 understates | threshold-sensitivity arm only |
| DEMOTED: S_ as lever verdict | pooling never engaged | architecture-ablation arm only |
Documented retractions: hot-band floor; pooled tail-latency 2–5×; lean-pool rule; "permanent 512 lock"; restore-bug-as-exception-safety.

## RQ-by-RQ verdicts

**RQ0 — integration design. MET (by construction + operation).** Spillable slice store behind the
unchanged store interface; no JIT-pipeline modifications; operated across ~3,100 cells.

**RQ1 — prevents OOM beyond the memory budget? MET, within an honestly drawn boundary.**
| cap | beyond-memory wins (in-mem capped = 0/10) | spilling real? | p |
|---|---|---|---|
| 512 | flagship 14/24 vs 0/18; r150 8/10 (unbounded); r200 8/8 (bounded) | yes: 289–530 MB written, io_r 277 MB (device restores exercised) | 4.1e-5 / 3.6e-4 / 2.3e-5 |
| 1024 | r150 11/16 (bounded); r300 8/10 (unbounded) + 8/8 (bounded@48k) | yes (180–230 MB written) | 5.7e-4 / 3.6e-4 / ~2e-5 |
| 2048 | r300 5/8 bounded@96k — the bounded-ONLY cell: in-mem@4096 0/10, in-mem@8128 0/8 (control closed 2026-06-12), heap@8128 0/8 | yes: 100 slices, 465 MB dir, 616 MB written | 0.026 vs 8-rep controls; ~1e-4 vs pooled controls |
| 2048 upper | r1000 (ρ≈1.03): fails under EVERY configuration | — | the honest limit |
Correctness: checksum MATCH 4/4 bounded-while-spilling + flagship MATCH at ρ=1.56.
TRANSPARENCY FLAGS: (a) r500/r700@2048 completions are FITS-cells (zero spilling measured) — claimed
as capacity, never as OOC wins; (b) demonstrated beyond-memory window: ρ≲2.0 @512, ≲1.54 @1024, one
ρ≈0.74 bounded-only point @2048; beyond ρ=1 at 2048 remains undemonstrated.

**RQ2 — at what cost? MET.** Matched granularity (~8K all variants, 3 coordinates): −11…−15% tps,
p99 1.1–1.6× in-mem. Default-arch band (own 4096 baselines, 6 coordinates): −3…−22%, p99 µs→ms at
10⁷-key sets (spill round-trip). Spill/restore latency distributions captured per cell. Caveats:
matched baseline exists at 3 coordinates; 4–17% between-session drift → all key contrasts within-session.

**RQ3 — memory behavior + why failures happen? MET — the strongest chapter.** Two-sided feasibility
law (band ≤ pool AND pool+overhead(pool)+base ≤ cap; overhead term causally confirmed via maxbuckets
8/8 unlock); three regimes (fits/throttled/deadlock); ingest-vs-drain race (causal at 2048: pacing
5/8→8/8; observational ladder at 9 cells); role-blind deadlock (118/138 gridlock); three-factor
attribution (granularity/allocation/levers, each isolated); bounded footprint −25…−33% vs matched
in-mem with recycling mechanism measured; twin-defect (cache bypass ↔ sticky pins) falsified both
directions. Every alternative explanation pre-registered and refuted.

**RQ4 (additional) — CPU/disk evolution? MET (pass completed 2026-06-12).** In-mem: 91–97% CPU, zero
I/O. Spilling winners: 89–92% CPU, write-mostly I/O, restores served from page cache (io_r=0).
Throttled regime: CPU 63%, io 277 R + 529 W MB — I/O-bound, the regime-2 signature. Heavy bounded
r700: CPU 54% (backpressure idle time visible).

**RQ5 (additional) — storage efficiency? MET with a stated proxy.** Composition (HMGAUGE, 26M
entries): header 51% / payload 34% / directory 14% / slack <1%; measured reduction options: vtable
−20% (documented), maxbuckets directory ÷4 (validated, unlocks 512/r150). Device-level write
amplification io_w/spill-dir = 1.0–1.8 (lz4; includes compaction). CAVEAT: device-level proxy, not
RocksDB's internal WA counter (not uniformly captured) — stated as such.

## Extent of testing
~3,100 cells; 9 pre-registered campaigns + probe sets; 8–10 reps/cell; saturated cells (0 or 8) carry
all headline claims, edge cells reported with rep counts; two architectures + matched-granularity
controls both directions; correctness via checksums; ~20 corrupted cells all confined to
already-failing regions (twin-defect, root-caused). All data, runners, pre-registrations, analysis
docs, and this audit versioned in the private repo.

## Residual soft spots (the complete list)
1. r500/r700@2048: capacity, not OOC (zero spill) — phrasing constraint.
2. 2048 above ρ=1: undemonstrated (r1000 universal failure) — stated limit.
3. Matched-granularity cost baseline at 3 coordinates only.
4. WA is a device-level proxy.
5. Tight-cap valley cells unconverted (blocked by the twin-defect; causal proof exists at 2048).
6. Checksum coverage: 2 workload points per architecture.
7. One machine, one SD card, synthetic workloads (in scope by design).
