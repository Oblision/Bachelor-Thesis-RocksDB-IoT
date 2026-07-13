# Through-line (roter Faden) — Chapters 5–7 (outline for the prose rewrite)

Each bullet = the one core claim of a paragraph (the future topic sentence).
Anything not listed here does not carry the through-line and gets cut or compressed in the rewrite.

The bullet form belongs to this outline only. The thesis text is written as prose paragraphs; one bullet becomes one short paragraph, and no bullet lists appear in the chapters.

## Rules from the meeting (July 13)

- No creative writing techniques of any form. This is the umbrella over the rules below: no metaphor, no personification, no analogy, no contrast clips, no dramatic fragments, no rhetorical setups, no coined imagery. Every sentence is a plain statement of fact or cause.
- Short paragraphs, one point per paragraph. State the main point directly, do not write around it.
- Factual, literal, simple. The shape is "X is Y. X is Y because Z." State the fact, then the cause, with all necessary context, and nothing else. No essayistic devices. Backward references to earlier findings are fine; the prose itself stays impersonal and dry, as in a scientific paper.
- No announcement sentences with a colon ("This section answers it in three moves: ..."). Avoid colons in general.
- Table 7 (tab:cost) caption is too wordy, tighten it.
- State the sizing rule as a rule: "Size the pool so that it holds the hot band and, with its overhead and the base footprint, stays below 90% of the cap." Not "lean pool" / "properly sized" as a description.
- Cut vague descriptors ("it is also lighter"); give the number directly.
- No em dashes; use commas, semicolons, parentheses, or "rather than". Keep one only where truly required.
- The "X, not Y" contrast clip never appears, in any variant ("The window is bounded, not universal", "the gap was granularity, not allocation", "recoverable, not implemented"). State the claim directly with its scope. Where the contrast is itself the finding, write it out ("completion depends on the pool geometry rather than on the total footprint").
- Prefer plain names over project jargon; write "buffer size" instead of "buffer granularity", and define any term that stays at first use. No coined compound terms without definition ("the anonymous-memory floor", "the footprint-versus-survival tradeoff"); state the fact the term compresses. "Lever" appears only as the defined name of the four pool-pressure levers from Chapter 4, never generically for "mechanism" or "option" ("no engine lever raises device bandwidth").
- No unexplained shorthand for a finding ("the b1374 vs. s1582 inversion"). Spell out what happened, with the numbers, so the sentence stands on its own. Verbs like "decides", "governs", "wins" also count as personification; use "depends on", "determines", "is set by".
- Captions are reading aid only (what the axes, markers, and lines mean). Explanations and arguments go in the body text, never only in a caption.
- No economic metaphors for tradeoffs ("pays a throughput cost for the memory discipline", "pay the round trip"). Name the mechanisms and the measured effect ("the mechanisms that make the bounded configuration slower are the same mechanisms that hold its memory under the cap").
- No investigation meta-narration ("A design review found why", "A high-cardinality workload settles it", "One incident shaped the method"). State the finding and its cause directly, in present tense where the fact still holds; the method appears in the same sentence only when it is needed.
- Every sentence has a subject and a verb. No verbless list fragments ("First, role-aware allocation, a drain-side reserve that ..."); write "The first item is role-aware allocation, a buffer reserve for ...". No follow-up sentences that start with a bare demonstrative ("This is the one mechanism ..."); name the subject ("The deadlock diagnosis isolates this reserve ..."). No "is what" clefts ("this targeting is what made ..."); use the plain verb ("this targeting made ...").
- One move per sentence. Do not pack claim, evidence, and interpretation into one colon/semicolon chain ("The failure model holds out-of-sample: 0 corruptions in 210 runs; every failure lands where the geometry places it"). Write the claim, then the evidence, then what it means, as separate sentences.
- No metaphor or personification for mechanisms ("lands where the geometry places it", "rides the spilling out", "the death moves", "wins", "blinds"). Name the mechanism literally, in words a reader outside the project can parse.
- Every section draft goes through stop-slop before delivery (no binary-contrast clips, no dramatic fragments, no punchy one-liners, no quotables), while keeping the technical and scientific register: statements that present information, nothing else. thesis-prose exceptions stand where academic register requires passive voice or hedging.

## Central thread of the evaluation

The OOC backend completes workloads whose state exceeds the memory budget and which the capped in-memory engine never finishes, but only inside an operating window. Two laws bound that window: the two-sided pool geometry (pool too small → abort, pool too large → OOM) and the ingest–drain race (ingest must stay below the drain rate; completion does not depend on state size alone). The RQ3 investigation explains why: one engine default kept all window state outside the pool, and the residual failure is an allocation deadlock.

---

# Chapter 5 — Experimental Evaluation

**Chapter intro:** central result (sentence above) + roadmap through the sections.

## 5.1 Setup

- Desktop + Docker cgroup cap (512/1024/2048 MB, no swap), 2 pinned cores, SD card as spill target. Docker reproduces edge-class resource limits; the host remains a desktop.
- Correctness via output checksums against the uncapped reference; the build containing the pool-pressure levers, with every lever off, behaves identically to the unmodified engine.
- Workload: synthetic Zipfian sensor stream (skew 1.0, 1.5 M keys), one tumbling-window SUM aggregation; out-of-order jitter D = L.
- ρ = steady footprint (uncapped) ÷ cap; ρ = 1 means the state exactly fills the budget.
- Workload labels: the number is the footprint in MB (s736 → ρ = 736/512 ≈ 1.44); s series small, b series big.
- Three variants: uncapped in-memory (reference), capped in-memory (the failure the backend must prevent), OOC under the cap.
- Matrix: 3 caps × pool sizes × ρ 0.45–3; ~460 cells, ~3,100 runs, 8–10 repetitions per cell.
- Outcome taxonomy: Complete / OOM kill / pool abort / timeout / crash; kept separate because the causes differ and the fixes are opposite (a smaller pool vs. a larger one).
- Fixed configuration = targeted subset of the design space; only cap, workload state, pool size, allocation type vary. Findings are conditional on it.
- Statistics: Fisher's exact for completion, Welch for means, McNemar for the paired E.6 contrast.
- Pre-registration + pinning rule (every knob explicit).

## 5.2 RocksDB backend analysis

- db_bench, 512 MB cap, 10,000 runs: compression dominates (lz4 4.4× throughput), the block cache has almost no effect on throughput, the write buffer lowers write amplification but costs memory.
- Fixed at lz4 / 16 MB write buffer / 8 MB block cache = memory-fit point at the tightest cap; held constant across all caps (design choice; 32/32 contrast → Appendix E.6).

## 5.3 Effectiveness (RQ1)

**Answer first:** At 512 and 1024 MB the backend completes workloads beyond the budget that the capped engine never finishes; at 2048 MB completion beyond the memory boundary is not demonstrated. Completion holds only inside the operating window this section maps.

### 5.3.1 Headline
- Primary finding (renamed from "flagship"): 512 MB, heap 14/24 vs. in-memory 0/18, p = 4.1e-5.
- Checksums match while spilling → every completion is a correct run.

### 5.3.2 Operating windows
- Heap windows are cap-dependent: 512 MB ρ ≈ 1.2–2.2; 1024 MB at least to ρ = 1.54; 2048 MB only below ρ ≈ 1.
- 512 MB: baseline completes nothing above ρ = 1; the pool size that completes differs per workload.
- 1024 MB: bounded completion rises with pool size in almost every row; heap shows no such order. b1374 fails everywhere because its pool demand exceeds every pool that fits under this cap; its total footprint fits.
- 2048 MB: bounded completes at the large-pool edge; heap completes in narrow pool bands that shift with the workload; s1582 fails because its ingest rate exceeds the storage-bandwidth ceiling (→ 5.3.5).

### 5.3.3 Two-sided pool geometry (bounded)
- Lower bound: the pool must hold the hot band (~N_w open windows), else pool abort.
- Upper bound: pool + induced overhead must fit under the cap, else OOM kill.
- The two failure modes are the two edges of the law; the three caps show it in complementary partial views (512: both edges; 1024/2048: only the abort edge reached).
- Heap is pool-sensitive too: the pool is a fixed up-front reservation; s327 sweep 10/10 → 6/10 → 0/10, all failures OOM.

### 5.3.4 Where each allocation type completes
- At geometry-correct pools (~40% of the cap), pooled covers nearly the full heap window and adds bounded-only cells.
- Division of the range: only heap completes at the smallest cap, at fast-ingest cells, and with the higher throughput at the heavy end; pooled provides the footprint guarantee, the bounded-only cells, and graceful degradation.
- Absolute edge: b2078 at 2048 MB fails under every configuration.
- Table 6 (tab:windows) summarizes the windows per cap; fig:effectiveness shows the headline (the capped baseline completes nothing above ρ = 1; the OOC backend completes cells past it).
- At 1024 MB, b1374 (1374 MB footprint) fails at every pool while s1582, with a larger footprint (1582 MB), completes 8/8. b1374 fails because its concurrently live state requires a 96k-buffer pool (~786 MB), and a pool of that size does not fit under the cap; s1582's live state fits through a 48k pool. Completion therefore depends on whether the required pool fits under the cap, and the total footprint is not the determining quantity.

### 5.3.5 Completion as a race
- Completion does not depend on ρ alone. At 2048 MB, s1582 (ρ ≈ 0.77, ingesting 2.8 M tuples/s) fails while the heavier b1676 (ρ ≈ 0.82, ingesting 1.1 M tuples/s) completes 10/10; the difference between the two is the ingest rate.
- Valley at 512 MB/16k: non-monotone 8/8 → 2/8 → 1/8 → 8/8. Mechanism 1: a full pool throttles ingest to the drain rate (admission-queue pacing), so workloads under more state pressure complete again. Mechanism 2: n = 8; the mechanism is robust while the exact fractions carry wide confidence intervals.
- Three valley figures: curve, pacing loop, run-time trace (anonymous set vs. total; the OOM kill is triggered by the anonymous set alone).
- Race plot: ingest rate vs. ρ; storage ceiling ~2.6 M tuples/s; above ρ = 1 and right of the ceiling no run completes.
- The backend's ρ < 1 failures (33 cells) are pool-geometry effects: both groups (slow-heavy and fast) recover at the geometry-correct pool, so the failures come from pool sizing rather than from insufficient memory.

### 5.3.6 Nexmark transfer
- Purpose: external validity. Different schema, in-order, canonical benchmark; q3/q7 isolate the aggregate function.
- Headline: at the one beyond-memory coordinate (C@1024) the bounded backend completes 8/8 and 7/8 vs. 0/8 in-memory.
- The fixed cap-derived spill threshold over-spills fitting cells (both OOC configurations complete 0/8 at B@512/q7); cost 42–61% for no benefit; state-aware threshold = future work.
- The two OOC configurations are not interchangeable. On fitting cells the heap configuration completes more runs; at the beyond-memory point only the pooled configuration completes reliably.
- The Nexmark runs also test the failure model on data it was not built from. The diagnosed corruption mechanism requires late writes, and the in-order stream produces zero corruption events in 210 runs. Every bounded failure occurs at the pool size the geometry law predicts.
- **RQ1 answer:** prevents OOM failures inside a characterized window; geometry + race bound it; transfers to Nexmark.

## 5.4 Cost (RQ2)

**Answer first:** −11–15% throughput at matched buffer size (~8 KB in every variant, so each comparison isolates the allocation choice); p99 1.1–1.6×; in exchange the pooled configuration reduces the resident footprint by up to 33%.

- Throughput: 11–15% where both types complete; grows with state, shrinks with headroom.
- Tail latency. Earlier comparisons showed a 2–5× gap because the variants used different buffer sizes; per-task latency scales with the buffer size in every variant, including the in-memory engine. With the buffer size matched, the OOC p99 is 1.1–1.6× the in-memory engine. The remaining gap comes from the spill-restore round trip and appears in the tail while mean throughput changes little.
- Standalone finding: raising the buffer size from the 4096-byte default to ~8 KB raises throughput at every tested cell; the in-memory engine alone gains up to 13%.
- Footprint: pooled −33% vs. in-memory at the widest point; 14–25% at the other comparison points.
- Table 7 (tab:cost): cost as bands per comparison class. [tighten caption]
- **RQ2 answer** + pointer: why a factor rather than an order of magnitude → 5.8.

## 5.5 Processor/Disk (RQ4)

**Answer first:** Utilization falls from near-full to disk-bound (62%) once spilling reaches the device; disk traffic scales with the spilled state volume and is bounded by the SD write bandwidth.

- Measurement basis: mean over the whole run; the one-off compilation phase lowers mean utilization on short in-memory runs to 84–97%.
- Below the memory boundary, restores are served from the page cache and device reads are near zero (22 of 24 spilling cells). Above the boundary, the spilled state exceeds the page cache and every cell reads from the device.
- Two idle mechanisms: disk wait (throttled regime, 62%) and pool-allocation stalls at the largest pools (down to 31%, independent of state pressure).

## 5.6 Storage efficiency (RQ5)

**Answer first:** Write amplification 1.0–1.8 (device-level proxy); footprint 51% headers / 34% payload / 14% directory; ~17–20% is structurally recoverable but left unimplemented.

- Headers dominate the state footprint (per-map gauge, 26 M entries).
- Recoverable 1: unused 8-byte vtable pointer per header (~17% of total state); needs a refactor.
- Recoverable 2: the bucket directory is larger than the workload needs. A one-line fix changed the bucket count at only one of the three sites that assign it, so two thirds of the maps kept their original directory size.
- On-disk spill volume depends on the workload alone; heap and bounded produce equal spill directories at the same cell.
- Pool-sizing rule: the pool holds the hot band; pool + overhead + base footprint ≤ 90% of the cap. [state as a rule, per meeting]

## 5.7 Memory Behavior and Stability (RQ3), the central investigation

**Answer first:** Two independent constraints (pool abort / OOM) form a feasibility corridor; inside it, completion additionally depends on the ingest-drain race; the residual failure is a diagnosed allocation deadlock. Told in investigation order because the refuted hypotheses are the evidence.

- Failures = two constraints; corridor with a tipping point (16k and 32k pools complete, 64k fails 0/10).
- Where the backend completes, it uses 14–22% less anonymous memory, because pooled pages return to the pool on spill. [cut "it is also lighter", give the number directly]
- The four pool-pressure levers (the defined mechanisms from Chapter 4) worked mechanically but converted no cell → "memory floor" conclusion → wrong.
- Design review: the 4096-byte buffer default kept every map page out of the pool, so the pool the levers acted on contained no window state.
- One flag (8192) engages pooling; attribution ladder at the decisive cell: 0/10 → 7/8 (levers within pooling p = 7.0e-4; pooled vs. unpooled levers 12/16 vs. 0/16, p = 8.1e-6).
- At the decisive cell, the pooled configuration with levers reaches the in-memory engine's throughput (3.38 vs. 3.44 M tuples/s) at 25% less anonymous memory (517 vs. 685 MB); its p99 latency is 2.2× higher.
- Pooling also recovers one stalled cell without any lever, from 3/10 to 23/24 (p = 1.6e-4).
- With pooling, the pool size sets the minimum memory footprint of a run, because the engine allocates the full pool at process start. A run with a large pool is therefore OOM killed by its own up-front reservation before the workload matters. A smaller pool lowers the footprint and at the same time raises completion, as long as it still holds the hot band. Choosing the pool size therefore no longer trades footprint against survival; the smaller pool is better on both counts.
- Three pre-registered alternative explanations refuted (pool-derived threshold, source-admission bound, task-queue bound).
- Allocation backpressure converts no cell; its instrumentation provides the diagnosis, since in 118/138 stalls no buffer returns for 2 s.
- Deadlock anatomy: emitted state pins the pool; every drain worker is blocked allocating; the pool has no separation between fill-side and drain-side allocations.
- Three-regime law: fits the pool / throttled / deadlock. Queue-pacing mechanism verified in code, quantitative share unmeasured (hypothesis, named as such).
- Recommended configuration: pooling + reserve + paced restore, backpressure off.
- The confirmation run settles three open points: the threshold stays cap-derived; the crash cluster occurs only in already-failing cells; a drain-side reserve is the one mechanism that would merge the two windows.

### 5.7.1 Disambiguation
- The 8128-byte control separates the allocation type from the buffer size (99.2% of the page size, so map pages stay on the heap at matched size).
- Confirmed: the ρ ≈ 2 completion at 512 MB is caused by the allocation type (pooled 8/8 vs. heap 0/8, p = 7.8e-5); the lever effect stands at fixed buffer size.
- One attribution stays mixed (buffer size alone recovers 3/8; the pairwise edge over the matched heap control has p = 0.15), stated openly.

### 5.7.2 Geometry, valley, crash cluster
- The all-zero column at 2048 occurred because the tested pool sizes lay on both sides of the working range and missed it, rather than from the deadlock; the 39% point completes.
- The geometry rule carries over across caps until the base footprint no longer fits beside the pool (at 512 MB the transplanted pool is OOM killed); this is the law in its final form.
- The valley recurs at every cap. Completing runs ingest below ~2.5 M tuples/s; runs whose demand exceeds ~2.7 M fail. The pattern appears 9 times across three caps and both allocation types.
- Crash cluster root-caused: the per-worker slice cache passes raw map pointers to compiled code and bypasses the spill checks; falsified in both directions (on: 3/3 crash, off: 4/4 clean).
- Disabling the cache is no mitigation. With jitter equal to the retention lag, every late write pins its slice, so no slice can spill (0/8 everywhere). The two defects mask each other; the headline data is unaffected.
- Separation: an allocator abort under pressure counts as a pressure failure, only the segfault counts as corruption; this separation confines the cluster.
- Architectural lesson: unmediated pointers into evictable state need an invalidation protocol (fix: epoch-stamped cache entry).

### 5.7.3 Growth signal and page release
- The same per-worker slice cache that causes the crashes (5.7.2) also prevents the growth signal from working. The growth signal is meant to detect when a single slice grows past a memory threshold, and its check sits inside the slice store. Compiled query code reads and writes map state through the cache and never passes through the store, so the check never runs. Placed inside the store, the signal records zero threshold crossings on a high-churn workload; placed on the per-record lookup path, which the cache cannot bypass, it records about 800. The cache defect therefore has two consequences with one root cause, the restore-path corruption and the undetected state growth.
- A single slice can grow to 127–157 MB and cross the hard memory threshold in the middle of a window. The existing check runs only when a slice is created at a window boundary, so it never observes this growth; the growth signal does.
- Evicting the growing slice when the signal fires holds it below the threshold and keeps the pool from filling, but converts no failing run. The failure changes form instead of disappearing, to an OOM kill where resident memory is the limit, or to an allocation stall caused by the synchronous eviction itself. The signal is necessary to observe intra-slice growth and insufficient to survive it.
- Page release cuts steady anonymous memory by 55% (peak 38%) at 6–8% throughput cost; it cannot reclaim the one live hot slice.
- **RQ3 answer:** two-sided law + race; three failure kinds; the pooled configuration keeps the footprint inside the corridor by recycling pages through the pool.

## 5.8 Summary of Results

- One verdict sentence per RQ (RQ0 by construction; RQ1 window; RQ2 11–15%; RQ3 ladder + deadlock; RQ4 62% disk-bound; RQ5 51% headers, WA 1.0–1.8) + Table 8 (tab:verdicts).
- fig:pvalues: all five headline contrasts have p < 1e-3.
- Resolution of the flagship-vs-allocation apparent contradiction: different baselines (spilling vs. not spilling; the matched-buffer-size control vs. the default heap).
- Cost interpretation: the measured loss stays a factor where the DRAM-to-SD gap suggests an order of magnitude; four measured mechanisms close the gap (reduce before store, hot band resident, page-cache restores, background drain); the moderate cost holds only while these keep the device byte stream below the drain bandwidth.

## 5.9 Threats to Validity

- Nine limits, one sentence each: emulation ≠ device (contrast directions transfer, absolute magnitudes stay host-specific); synthetic workloads (by design, Nexmark = transfer test); session drift 4–17% (contrasts within-session); ρ from uncapped runs; partial checksum coverage; confound resolved (5.7.1); corruption confined + root-caused; fixed parameter subset; n = 8–10 (the claims rest on the headline contrasts rather than on single cells).
- The incident that produced the pinning rule.

---

# Chapter 6 — Related Work

- Flink + RocksDB = the reference integration for state > memory.
- Two relevant differences: checkpoint-oriented + pull-based vs. push-based with one global buffer pool → Flink results do not transfer directly.
- Tuning literature (Jin, Pamarthi, Adão, Wei): parameters mitigate but do not remove memory/IO sensitivity → justifies the one fixed configuration.
- WindFlow and AION show larger-than-memory windows, but neither evaluates a push-based engine with one shared global pool under hard caps.
- Positioning: this thesis provides exactly that evaluation; the contribution is evaluation-first.

# Chapter 7 — Conclusion

## 7.1 Summary
- Answer to the core question: yes, within the evaluated scope; completion beyond the budget, correct outputs, bounded cost.
- Seven contributions, one sentence each (C1 integration, C2 harness/reproducibility, C3 windows + race, C4 allocation finding + parity, C5 three-regime law + deadlock, C6 gated branch, C7 growth signal + page release).
- Negative IoT finding: ingest rate and storage device are not fixed in the IoT; SD write bandwidth bounds the drain rate; once storage is the bottleneck the I/O path sets the limit, because no engine mechanism increases the write bandwidth of the storage device.

## 7.2 Limitations and Generalizability
- Four kinds of limits: scope (fixed subset, boundaries not transplantable), statistics (n = 8–10; the claims rest on the headline contrasts), system specificity (the law plausibly holds for any engine with one fixed shared pool, unverified), the ρ metric (uncapped reference mildly overstates pressure; the qualitative boundary is unaffected).

## 7.3 Practical Guidance
- One recipe, five lines: pooling on (buffer = page size); size the pool by the rule from 5.6; threshold cap-derived; reserve 15% + paced restore; backpressure off. [spell out the sizing rule here instead of "lean pool"]

## 7.4 Lessons Learned
- Five rules: pin everything; pre-register; report negative results in full; verify the allocation assumption behind every experiment; validate component benchmarks at system level.

## 7.5 Future Work
- Seven items in order of precision (1. drain-side reserve, merges the windows; 2. closed-loop admission; 3. header-pointer removal; 4. exception-safe restore; 5. epoch-stamped cache invalidation; 6. open-window spilling; 7. sliding windows + full Nexmark suite).
- Two sensitivity experiments: core count, storage device (SSD vs. SD).
- Three upstream recommendations: raise the buffer default; make the pooling boundary explicit; ship the levers gated.

---

## Cut candidates (do not carry the through-line, or only as detail)

- 5.3.4: the long s1034 ρ=1.01 excursus (measurement-boundary effect); compress to 1–2 sentences.
- 5.3.5: the two-group analysis of the ρ<1 failures (7.2.7b); keep the core claim (pool-geometry effects that recover at the correct pool), tighten the group details.
- 5.3.6: the q3-vs-q7 tipping-point paragraph; cut to the core claim (discrepancy = boundary sensitivity, headline untouched).
- 5.6: the failed bucket-directory fix; 2 sentences (what was tried, why ineffective).
- 5.8: the four-mechanism interpretation matters, but one sentence per mechanism.
- Throughout: cut announcement and meta sentences ("This section answers it in three moves:", "the next section turns to..." only where the transition carries content).
