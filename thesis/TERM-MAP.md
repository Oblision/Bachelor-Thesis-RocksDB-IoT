# Term Map — Bachelor Thesis (RocksDB-IoT / NebulaStream OOC)

Tracks every technical term/mechanism across all 7 chapters: where it is first
defined, how it is later reused, and where usage runs ahead of (or drifts from)
its definition. Built 2026-07-04 by reading `thesis/latex/chapters/1..7*.tex`
in document order (the order the reader actually encounters them), plus
`Template.tex` for the note macros and the vendored NES source for one
code-level check.

**How to use this file:**
- Before adding a new technical term to any chapter, check the glossary below —
  if it's already defined, cite that location instead of redefining it.
- If you introduce a new term, add a row here at the point of its first
  definition, then update this file when it's reused/aliased elsewhere.
- If you use a term before this file shows it defined, that's exactly the kind
  of gap Section "Flagged Issues" below tracks — add it there.

---

## Flagged Issues (need a fix or a deliberate decision)

Ordered by how much reader context they cost. File paths are relative to
`thesis/latex/chapters/`.

### 1. ~~Generator key-count / rows-per-window coupling~~ — FIXED
`5Experimental.tex:68-69`. Was a bare "scales X and Y together" with no reason
given. Rewritten 2026-07-04 to state the shared bottleneck (distinct keys held
open at once, capped by whichever of the two is smaller). No further action.

### 2. ~~"the map arena" — undefined term, single use~~ — FIXED
`5Experimental.tex:1070` (caption of `fig:rb32-compartments`):
> The *unpooled directories* are the bucket directories and the map arena;
> NebulaStream allocates these as tuple buffers too, but on the process heap
> rather than in the pool.

Never defined or mentioned anywhere else in the thesis.

**Correction to my first pass:** I initially (wrongly) matched this to
`nes-nautilus/include/Arena.hpp`, a generic per-pipeline-invocation operator
scratch pool — unrelated to hash-map internals specifically. Checked the
actual figure's data source instead
(`thesis/experiments/rb32-config-contrast/fig_rb32.py:86,94`): the
"unpooled directories" bar is a **single** measured gauge value (the TSV
column `unpooled`, = Ch.4's "unpooled gauge," §6.4) — the script does not
split it into sub-components; the caption's "(bucket dirs + map arena, on
heap)" is the script author's own descriptive gloss for what's presumably
inside that one number, not a measured breakdown.

Traced the likely real referent in the vendored source instead:
`ChainedHashMap.cpp` allocates **three** distinct unpooled-relevant regions:
1. `entrySpace`/`entries` (lines 173-192) — the bucket directory itself, always unpooled.
2. `storageSpace` (lines 194-217) — fixed-size entry pages, pooled *iff* page size ≤ buffer size (the Ch.4 §6.2 rule).
3. `varSizedSpace` (lines 149-166, `allocateSpaceForVarSized`) — a bulk-preallocated buffer for **variable-sized** entry data, always unpooled via `getUnpooledBuffer` regardless of the page/buffer-size rule.

(3) is "the map arena" — resolved with direct code evidence, not inference:

- **The gauge itself is engine-global, not hash-map-specific.** The
  "unpooled" TSV column comes from `UPOOLGAUGE`
  (`nes-memory/UnpooledChunksManager.cpp`), which increments on every call
  through the single shared entry point
  `UnpooledChunksManager::getUnpooledBuffer()`. That entry point is also
  called by non-hash-map code (`Nautilus/Arena.cpp`, `PagedVector.cpp`,
  `TupleBufferRef.cpp`). The harness's `hashmap_unpooled_peak` label
  (`.planning/eval-phase/lag-validation/run-cell.sh:206`) is a naming
  convention assuming only hash-map traffic hits this path for this
  workload — the instrumentation itself does not enforce that.
- **`varSizedSpace` ("the map arena") is only ever allocated when a field is
  `VARSIZED`-typed** — traced to
  `ChainedEntryMemoryProvider.cpp:166` (`if (type.isType(DataType::Type::VARSIZED))`
  gates the only call site of `storeVarSized` → `allocateSpaceForVarSized`).
  This thesis's evaluated schema is `key UINT64, val UINT64, ts UINT64`
  (Listing `lst:query`) — no `VARSIZED` field exists anywhere in the
  evaluation. The branch is never taken.
- **Conclusion: "the map arena" = `varSizedSpace` is definitionally
  correct** (a real, distinct, always-unpooled compartment in
  `ChainedHashMap`), **but it is provably zero bytes in this thesis's own
  data** — not "probably small," but never allocated at all for this
  schema. The RQ5 composition census (`fig:composition`) neither confirms
  nor refutes this either way: it comes from a separate instrumentation
  channel (`HMGAUGE`, logged per map destruction) that has no field for
  `varSizedSpace` and structurally cannot see it.

**Applied 2026-07-04** at `5Experimental.tex:1073-1077` (caption of
`fig:rb32-compartments`). Per your "rewrite to fit the data or remove"
instruction: removed the claim that "the map arena" is part of what this
compartment measures (it measures zero of it in this data), kept the
correctly-supported claim (bucket directories, always unpooled), and added
one sentence stating plainly that the variable-sized-entry region exists in
the engine but is never populated by this thesis's fixed-size schema:
> The \emph{unpooled directories} are the bucket directories, the per-map
> control-block table that is always unpooled regardless of page size
> (Section~\ref{sec:bg-windows}); NebulaStream allocates them as tuple
> buffers too, but on the process heap rather than in the pool.
> ChainedHashMap also supports a variable-sized-entry region allocated the
> same way, but this thesis's fixed-size UINT64 schema never populates it,
> so it contributes nothing to this compartment.

### 3. ~~Bucket-directory shrink failure — opaque mechanism~~ — FIXED
`5Experimental.tex:743-744`:
> A one-line shrink of the bucket directory did not work. Two-thirds of the
> maps obtain their directory size on code paths the change did not cover.

Found the exact source this sentence compresses:
`nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/FINDINGS-flagship-comparison.md:71-75`
(the "BUCKET-TWEAK variant" experiment, branch `ooc-bucket-tweak`). Quoting:
> Tried to shrink the ~30% bucket directory by targeting ~4 entries/bucket
> [by setting `numberOfBuckets = avg_keys/4` in `getCreateNewSlicesFunction`].
> **Did not work:** bucket_dir stayed ~34%... The maps show a MIX of bucket
> counts (16384/8192/4096 = tweaked, ~105 maps; but 32768/65536 = NOT tweaked,
> ~208 maps). ~2/3 of maps (the larger per-worker-build / unspill maps) size
> buckets via a DIFFERENT path the one-line change... does not cover.

So concretely: the fix touched only the bucket-count logic used at
**slice-creation** time. Maps built later via two *other* call sites
(**per-worker build**, and **unspill/merge**) size their own buckets
independently and never saw the change — hence ~2/3 of maps (the ones from
those two other paths) kept their original, larger bucket counts.
**Applied 2026-07-04** at `5Experimental.tex:744-747`: now names the
slice-creation-only fix and the two uncovered sites (per-worker build,
unspill/merge) explicitly, across 4 sentences instead of 2.

### 4. ~~"throttled regime" used ~180 lines before its own definition~~ — FIXED
`5Experimental.tex:707` (§RQ4, Processor and Disk Utilization) cited
`sec:rq3` for the definition, but the **three-regime feasibility law** —
where "regime two" (= the throttled regime: demand exceeds the pool, cap
throttles ingest, run completes at reduced speed) is actually stated —
doesn't appear until `5Experimental.tex:888-891`, ~180 lines later in the
same chapter. A reader hitting line 707 in first-read order had only a
forward pointer, not a definition. (Structural consequence of RQ3's section
being placed last in the chapter by design, per the chapter intro lines 9-10
— the forward-reference still landed before the payoff.)
**Applied 2026-07-04**: added an inline parenthetical gloss at first use
("demand exceeds the pool, but the cap throttles ingest instead of the run
deadlocking (regime two of the three-regime feasibility law,
Section~\ref{sec:rq3})") rather than reordering the chapter.

### 5. ~~watermark / lateness bound / emit lag~~ — FIXED (verified at the code level first: one real mechanism, two descriptions, never reconciled in prose)
- `2Sci_background.tex:107-108` defines **watermark**: the timestamp up to
  which all tuples are assumed arrived; a window completes once it passes.
- `4Solution.tex:58-62` (§5.3b) defines **emit lag $L$**: after a window
  completes, it's held for a configured lag $L$ before emission (creating the
  cold-slice population the spiller drains). Formula: $N_w = L/W$.
- `5Experimental.tex:66` introduces **"lateness bound $L$"**, describing the
  out-of-order jitter ceiling (max event-time displacement). "Watermark" is
  never used again anywhere in Ch.5.
- `5Experimental.tex:94` (Table `tab:workload`) labels one row **"Emit lag
  (lateness bound)"**, same symbol $L$, same value (4,000,000 ms).

**Checked the actual engine implementation** (not just prose) to settle
whether this is one mechanism or two:
`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`.
Confirmed it is genuinely **one** parameter (`emitLag`, a single constructor
field) serving both roles, not a coincidence:
- Lines 578-590 (`getTriggerableWindowSlices`): the store computes a
  **lagged** emit-trigger watermark = `globalWatermark - emitLag`, and only
  emits/evicts windows the lagged watermark has passed. This is exactly
  Ch.4's "held for a configured lag before emission."
- Lines 283-291 (the "late-write guard"): a tuple arriving late, whose window
  already looks complete by the *true* watermark, is still correctly
  incorporated **as long as it falls inside the retained band**
  `[globalWatermark - emitLag, globalWatermark)` — i.e. as long as its
  lateness is ≤ `emitLag`. This is exactly Ch.5's "lateness bound": data later
  than $L$ would land in an already-emitted, gone window and be lost.
- The generator's own comment confirms the intent from the tooling side
  (`gen_skew.py:12`): "Set the NES emit_lag (allowed lateness) >= LATENESS so
  late rows are not dropped."

So: **the "watermark" of Ch.2 is the true/base watermark** (unaffected by
$L$); **`emitLag` creates a second, lagged watermark used only to decide the
emit/evict cutoff**, and that same lag is, by construction, also the maximum
tolerable lateness before a late tuple's window is gone. Ch.4's "emit lag"
and Ch.5's "lateness bound" are one config value, correctly described from
two angles — this is **not** a bug, but the thesis never states the
equivalence, and never mentions that there are effectively two watermarks
(true vs. lagged) in play.
**Applied 2026-07-04** at `5Experimental.tex:67` (new sentence inserted
right after the lateness-bound definition): "This bound is the same
configured delay as the emit lag of Section~\ref{sec:architecture}: holding
a completed window back by $L$ before emission is exactly what lets a row up
to $L$ late still land in it correctly."

### 6. ~~"reserve lever" / "paced restore" — never explicitly tied to Lever 1a / 1c~~ — FIXED
`4Solution.tex:102,104` define **Lever 1a** ("lets the spiller also drain when
free buffers fall below a reserve") and **Lever 1c** ("paces restores: a
restore before emission proceeds only when the pool has headroom"). From
`5Experimental.tex:822` onward (and `7Conclusion.tex:51`), the text switched to
calling these **"the reserve lever"** and **"paced restore"** without ever
stating the correspondence. The wording was close enough (reserve↔reserve,
paces↔paced) that an attentive reader could infer it, so this was milder than
#2/#3/#5, but still an unstated alias switch.
**Applied 2026-07-04** at `5Experimental.tex:822-823`, first use of each term:
"Pooling with the reserve lever (Lever 1a) completes 5 of 8. Pooling with the
reserve lever and paced restore (Lever 1c) completes 7 of 8...". Left
`7Conclusion.tex:51` and the `fig:ladder` caption untouched — both come after
this first-use point in reading order, so the alias is already established by
the time the reader reaches them.

### 7. ~~Campaign label "L_" — never glossed in main text, only in Appendix E~~ — FIXED
`5Experimental.tex:1029-1030` glosses **W_** ("the pooled grid"), **N_** ("the
pooled lever grid"), and **U_** ("the matched-granularity heap control") at
first use, each with a short descriptor. **L_** first appeared at line 1040,
in the `fig:rb32-arch` caption — "heap arms (U\_, L\_) do not" — with no
descriptor of its own anywhere in the chapter body. Its only definition was in
`misc/Annex.tex:228-232` ("L\_ heap", default-granularity heap arm, distinct
from U_'s matched-8128-byte granularity), which the main text never pointed to.
**Applied 2026-07-04** at `5Experimental.tex:1041-1042` (the `fig:rb32-arch`
caption): "...the heap arms (U\_, the matched-granularity heap control, and
L\_, the default-granularity heap arm) do not."

### 8. ~~(Minor) Buffer-size-vs-page-size pooling rule not restated at reuse~~ — FIXED
`5Experimental.tex:924` ("A buffer size of 8128 bytes is 99.2% of the page
size but still below it, so maps stay on the heap at matched granularity")
relied on the pooling rule from `4Solution.tex:166-167` ("a page is pooled if
the configured page size fits into the engine's buffer size"), ~800 lines
earlier, without restating it. Lowest priority of the eight, but cheap to fix.
**Applied 2026-07-04** at `5Experimental.tex:924`: "...still below it: since
a page is pooled only when it fits inside the buffer
(Section~\ref{sec:integration}), maps stay on the heap at matched
granularity."

### 9. ~~"The heap arm is sensitive to the same knob" — finding stated without its mechanism~~ — FIXED

`5Experimental.tex:358-360` (§7.2.4b, right before Figure~\ref{fig:knife}):
> The heap arm is sensitive to the same knob.
> At 512\,MB, r75, the pool is the only variable, and completion falls from 20
> of 20 at a 16k-buffer pool through 11 of 20 at 32k to 0 of 20 at 64k; every
> failure is an OOM kill.

This reads as paradoxical on first encounter: the "heap arm" is defined two
paragraphs earlier (line 246) as the configuration where "hash map pages live
on the heap" — i.e. *not* in the buffer pool. So why would growing the *pool*
size push a heap-architecture run into OOM kills, if the pool holds none of
its window state?

**Verified mechanism** (present in the thesis, but ~490 lines later and never
linked back to this claim):
- `2Sci_background.tex:47-49` (§2.5): "All buffers come from one global
  buffer pool. The pool holds a fixed number of buffers. **The engine
  allocates the pool once at process start.**" — this is unconditional: the
  pool is reserved up front regardless of which architecture is in use.
- `4Solution.tex:135` (§5.12): "The pool is one fixed slab reserved at
  startup..." — same fact, restated in Ch.4.
- `5Experimental.tex:849` (§7.6h, RQ3 investigation): "The pool is allocated
  up front, so the pool is the anonymous-memory floor." — this is the exact
  causal sentence that would resolve the line-358 puzzle, but it appears in
  the *bounded*-architecture discussion, never explicitly connected back to
  the heap-arm finding.

So the resolution is: the buffer pool is always pre-allocated at process
start as a fixed reservation carved out of the memory cap, whether or not
hash-map pages are ever placed in it. Growing the configured pool size grows
that fixed reservation either way, leaving less of the cap free for the
heap-resident window state the heap arm actually uses — hence OOM kills as
the pool grows, even though the pool itself holds none of that state.

**Applied 2026-07-04** at `5Experimental.tex:358-360`, inserting a causal
clause: "The heap arm is sensitive to the same knob. The pool is a fixed
slab the engine reserves at process start regardless of whether hash-map
pages live in it (Section~\ref{sec:bg-nes}); growing it enlarges that
up-front reservation and leaves less of the cap for the heap-resident
window state, whose pages never enter the pool. At 512\,MB, r75, the pool
is the only variable, and completion falls from 20 of 20 at a 16k-buffer
pool through 11 of 20 at 32k to 0 of 20 at 64k; every failure is an OOM
kill."

### 10. ~~"A pool-sizing rule follows from the data" — the 90% figure does not, in fact, follow from the data~~ — FIXED

`5Experimental.tex:754-756` (§RQ5, opens the pool-sizing paragraph):
> A pool-sizing rule follows from the data. The pool must hold the hot band,
> the soft budget, the emission pipeline, and the I/O path. The pool plus its
> unpooled overhead plus the base footprint must stay below 90\% of the cap.

Checked the vendored planning docs to see where "90%" actually comes from:
- `nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/OPTIONS-ooc-stability.md:116`:
  the identical formula (`... ≤ 0.9×cap`), with its own risk column stating
  explicitly **"none — it's a sizing rule + the formula is a thesis
  contribution."** — i.e. self-labeled by the planning doc as *authored*,
  not fit to measurements.
- `.../lag-validation/ANALYSIS-RQ.md:102-103`: same formula, same framing.
- `thesis/OUTLINE.md:152`, `thesis/WRITING-POINTS.md:281`,
  `thesis/POINT-SEQUENCE.md:180`: all repeat "0.9·cap" verbatim — drafting
  artifacts of the same number, not independent derivations.
- No document anywhere justifies 90% specifically over, say, 85% or 95%.

So: the *shape* of the rule (hold the hot band as a lower bound; pool +
overhead + base under the cap as an upper bound) genuinely follows from the
data — it is exactly the two-sided geometry law already established at
`5Experimental.tex:326-334` (§7.2.4). But the specific "90%" margin is a
**designed safety headroom**, authored to leave slack for the soft budget,
the emission pipeline, and the I/O path (none of which the thesis measures
as a fraction of the cap on their own) — not a number the data produced or
fit. "A pool-sizing rule follows from the data" overstates this one figure.

**Applied 2026-07-04** at `5Experimental.tex:754-756`, reworked to separate
the data-grounded part (the geometry law) from the authored margin: "A
pool-sizing rule combines the geometry law above with a designed safety
margin. The pool must hold the hot band, the two-sided geometry law's lower
bound (Section~\ref{sec:rq1}). What the data does not itemize on its own,
the soft budget, the emission pipeline, and the I/O path, is covered instead
by headroom: the pool plus its unpooled overhead plus the base footprint
should stay below 90\% of the cap, a margin chosen to leave that headroom
rather than measured from it."

### 11. ~~"Buffer granularity ... speeds up even the in-memory engine" — asserted with no mechanism anywhere~~ — FIXED

`5Experimental.tex:646-649` (§RQ2, Cost):
> Buffer granularity is an engine-wide performance factor in its own right.
> Raising the default 4096-byte buffers to about 8{,}000 bytes speeds up
> even the in-memory engine by up to 13\%. The shipped default loses at
> every tested cell. This is an actionable finding for the engine itself,
> independent of out-of-core processing.

Checked planning docs and the vendored NES source for any explanation of
*why* larger buffers speed up the in-memory (non-spilling) engine:
- `.../lag-validation/EXPERIMENT-final-matrix.md:169-170`: "in-mem tps
  3.15→3.22 ... per-TASK latency scales with buffer size — affects every
  variant equally." This is the same restatement of the correlation that
  ended up in the thesis prose (`5Experimental.tex:637`, RQ2) — not a
  mechanism.
- Searched NES source near `operator_buffer_size`
  (`nes-query-compiler/interface/QueryExecutionConfiguration.hpp:36,74`),
  `BufferManager.hpp`, and `docs/technical/QueryEngine_TaskQueue.md`: no
  comment anywhere discusses buffer size against throughput or per-task
  overhead; the only nearby comments concern memory footprint.
- **No document, in the planning tree, the thesis, or the source comments,
  explains the mechanism.** It is reported purely as a measured correlation.

I first drafted a fix that connected this to `2Sci_background.tex:38-40`
("the work is split into tasks... one task per buffer") with a hedge like
"likely" — but that still invents a mechanism theory (task/queue round
trips) that no data in this thesis isolates or measures; a hedge word does
not make an unverified causal claim safe to publish. Per your instruction
("rewrite to fit the data or remove"), I dropped that theory instead of
softening it.

**Applied 2026-07-04** at `5Experimental.tex:646-649`. Rather than
inventing a mechanism, the fix ties the throughput claim to the one thing
the data actually establishes in this same section: the per-task latency
scaling with buffer size stated one paragraph earlier (§7.3.4,
`5Experimental.tex:643`). No new causal claim is added.
> Buffer granularity is an engine-wide performance factor in its own right,
> not specific to out-of-core processing: the same per-task latency scaling
> with buffer size noted above shows up in throughput too. Raising the
> default 4096-byte buffers to about 8{,}000 bytes speeds up even the
> in-memory engine by up to 13\%. The shipped default loses at every tested
> cell. This is an actionable finding for the engine itself, independent of
> out-of-core processing.

### 12. ~~"which must behave identically" — ambiguous referent in the levers-off regression-arm sentence~~ — FIXED

`5Experimental.tex:58` (§6.6, Correctness), original:
> The gated binary itself is verified with a levers-off regression arm,
> which must behave identically to the unmodified engine.

The relative clause left it unclear whether "which" refers to the gated
binary (run with levers off) or to the regression arm itself, and never
stated *why* this check exists.

**Applied 2026-07-05.**
> The gated binary, the engine build containing the levers, is verified
> separately: with every lever switched off, it must behave identically to
> the unmodified engine, confirming that the levers introduce no regression
> when inactive.

### 13. ~~"The workloads are synthetic sensor streams" — conflates the workload with its input data~~ — FIXED

`5Experimental.tex:61` (§7.1.1), original:
> The workloads are synthetic sensor streams. Each stream is a pre-generated
> CSV file of sensor readings that a NebulaStream file source replays into
> the query below.

Throughout the rest of the thesis, "workload" means the query executed
under a given parameter setting (e.g. "workload r75"), not the input data
feeding it (checked all 65 uses of "workload" across the seven chapters via
`grep -n -i workload thesis/latex/chapters/*.tex`) — this is the one place
the sentence states an identity between the two.

**Applied 2026-07-05.**
> Each workload runs the query below over a synthetic sensor stream that
> supplies its input. The stream is a pre-generated CSV file of sensor
> readings that a NebulaStream file source replays into the query.

### 14. ~~"drawn uniformly on $[0,D]$, so the stream arrives out of order" — imprecise about the distribution and overclaims disorder as a per-row guarantee~~ — FIXED

`5Experimental.tex:65` (§7.1.1), original:
> The generator adds a positive jitter to each timestamp, drawn uniformly on
> $[0, D]$, so the stream arrives out of order.

Checked the generator against source
(`nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/work/gen_skew.py:89`,
the only jitter-generating script in the repo):
```
jitter = rng.integers(0, a.lateness + 1, size=n, dtype=np.int64)
```
This is a discrete-uniform draw over the integers $[0, D]$ inclusive,
applied independently to every row (`size=n` = all rows), not a continuous
distribution and not applied selectively to "part of the stream" as
originally worried. But 0 is a valid draw for any single row, so no
individual row is guaranteed to be displaced — "the stream arrives out of
order" is an aggregate property (true for the sequence as a whole, given
millions of independent draws over $[0, D]$), not a per-row one. "Positive
jitter" in the original also risked being read as "strictly greater than
zero," which the code does not guarantee.

**Applied 2026-07-05.**
> The generator draws a jitter for every row's timestamp, independently and
> uniformly over the integers $[0, D]$: each displacement in that range,
> including 0, is equally likely. A single row can therefore land back in
> its own base-time slot, but the draw is over millions of possible values,
> so almost every row is displaced and the arrival sequence as a whole is
> out of order.

### 15. ~~"pool each grid cell with its backend-size twin ... so their cells carry 16 to 20 runs" — pooling stated with no justification~~ — FIXED

`5Experimental.tex:127` (§7.1.4, Setup) and the `fig:heatmap` caption
(§sec:rq1), original:
> The operating-window figures additionally pool each grid cell with its
> backend-size twin from Section~\ref{sec:rb32}, so their cells carry 16 to
> 20 runs; the headline statistics quoted in the text use the 16/8 baseline
> arms alone.

Stated the mechanic (pooling two arms for extra repetitions) without saying
why it is valid to treat a different RocksDB configuration's runs as
repetitions of the same condition. Checked `sec:rb32` (`5Experimental.tex:
1008-1087`, the backend-size contrast) for the justification: the 16/8 vs.
32/32 write-buffer/block-cache contrast is NOT statistically inert in
general — at the tight 512\,MB cap the pooled architecture's completion
rate moves 15-33 percentage points (McNemar $p=0.002$, $p<0.001$,
`RB32-2`). But `RB32-3` shows this is a wash with headroom (1024/2048\,MB:
+4pp/+0pp, neither significant) and, critically, a same-session control at
512\,MB (which removes the between-session drift confound) found the
difference itself not significant ($p=0.21$). So the completion behavior of
the two configurations does not differ enough, once drift is controlled
for, to distort a pooled visual count — which is exactly why the headline
statistics quoted in the running text deliberately keep the two arms
separate and never rely on the pooled number.

First fix (2026-07-05) added the justification but still described it as
"pooling a grid cell with its twin," which buried the plain mechanical fact
underneath the statistical argument. You flagged this as weird: the plain
fact is that two separate 8-to-10-run experiments were run, and only for
these figures are their per-cell counts added together and displayed as if
they were one 16-to-20-run dataset — say that first, directly, then justify
it.

**Applied 2026-07-05 (revised)** at `5Experimental.tex:127` (full
explanation, first mention) and the `fig:heatmap` caption (short pointer
back to it), now also citing the actual evidence figure
(`Figure~\ref{fig:rb32-arch}`) instead of only the section:
> The operating-window figures combine two separate experiments into one
> displayed count: the core grid (8 to 10 runs per cell) and its
> backend-size twin, the same cells rerun under the paired 32/32 RocksDB
> configuration (Section~\ref{sec:rb32}), giving each displayed cell 16 to
> 20 runs instead of 8 to 10. Treating the two experiments as one dataset
> here is justified by Figure~\ref{fig:rb32-arch}, which shows the two
> configurations complete at the same rate once between-session drift is
> controlled for. The headline statistics quoted in the text keep the two
> experiments separate and use the 16/8 grid alone.

### 16. ~~Figure 19 (fig:rb32-arch): "pooled grids (W\_, N\_)" — labels used without saying what they are~~ — FIXED

`5Experimental.tex:1030-1039`, the caption of Figure 19 (backend-size
effect on completion, by architecture), original:
> ...per campaign, paired by cell. The pooled grids (W\_, N\_) gain; the
> heap arms (U\_, the matched-granularity heap control, and L\_, the
> default-granularity heap arm) do not...

U\_ and L\_ already carried an inline descriptor (fixed under Flag #7), but
W\_ and N\_ did not — the caption named them without saying what they are,
so a reader looking at this figure alone (not the main-text sentence one
paragraph above it, `RB32-2`, line 1023) could not tell the two pooled
labels apart. Checked `misc/Annex.tex:207-236` (Appendix E.6, the full
per-campaign table this figure is drawn from) for the precise referents:
"W\_ pooled" and "N\_ pooled levers" — i.e. W\_ is the baseline pooled
architecture, N\_ is the same architecture with the pool-pressure levers
enabled.

**Applied 2026-07-05** at `5Experimental.tex:1033-1039`: gave W\_ and N\_
the same inline-descriptor treatment U\_/L\_ already had, and added a
pointer to the full per-campaign, per-cap table.
> ...The pooled grids, W\_ (the baseline pooled architecture) and N\_ (the
> pooled architecture with pool-pressure levers enabled), gain; the heap
> arms, U\_ (the matched-granularity heap control) and L\_ (the
> default-granularity heap arm), do not... Full per-campaign, per-cap
> numbers are in Appendix~E.6.

### 17. ~~"16/8" and "32/32" used in §Experimental Setup, ~100 lines before either is explained~~ — FIXED

`5Experimental.tex:127` (old location, §7.1.4, Setup section) forward-referenced
"the 16/8 grid" and "the paired 32/32 RocksDB configuration" while explaining
why the operating-window figures pool two experiments together. The problem:
this is the *first* section of the chapter. Nothing yet has said what a
RocksDB write buffer or block cache is, what "16/8" is short for, or that a
"32/32" alternative exists — that explanation is the entire next section,
§Experimental Analysis of the RocksDB Backend (`sec:rocksdb-eval`,
lines 158-241), which ends by walking through exactly this contrast in plain
prose (lines 234-241) before naming `Section~\ref{sec:rb32}` as the dedicated
test of it. So the Setup-section paragraph used shorthand and a forward
pointer for a concept the reader would only get ~100 lines later.

You gave two options: pull `Table~\ref{tab:rocksdb-char}` (Table 3) earlier,
or move the paragraph to after the RocksDB configuration is explained.
Moving Table 3 would disturb the chapter's opening logic (general
measurement-matrix setup, then RocksDB-specific tuning); moving the
paragraph is surgical and keeps the reading order need-to-know.

**Applied 2026-07-05.** Removed the paragraph from `5Experimental.tex:127`
(Setup), leaving only a one-line forward pointer:
> How the operating-window figures make use of those extra cells is
> explained in Section~\ref{sec:rq1}, once the RocksDB configuration behind
> them has been introduced.

Reinserted the full paragraph immediately before the `fig:heatmap` figure
in §Effectiveness (RQ1) (`5Experimental.tex`, right after the
cap-dependent-operating-windows paragraph), i.e. after `sec:rocksdb-eval`
has already explained 16 MB/8 MB, the 32/32 alternative, and previewed
`sec:rb32`'s finding:
> The figure below combines two separate experiments into one displayed
> count: the core grid (8 to 10 runs per cell) and its backend-size twin,
> the same cells rerun under the paired 32/32 RocksDB configuration of
> Section~\ref{sec:rb32}, giving each displayed cell 16 to 20 runs instead
> of 8 to 10. Treating the two experiments as one dataset here is justified
> by Figure~\ref{fig:rb32-arch}, which shows the two configurations
> complete at the same rate once between-session drift is controlled for.
> The headline statistics quoted throughout this chapter keep the two
> experiments separate and use the 16/8 grid alone.

Also trimmed the `fig:heatmap` caption's own restatement of this (now
redundant with the paragraph directly above it, and its
`Section~\ref{sec:setup}` pointer was left stale by the move) to "as
explained above."

### 18. ~~fig:pvalues: undefined CI method, a mislabeled contrast, and an axis-clipped annotation~~ — FIXED

Three separate problems in `figures/scripts/fig_pvalues.py` (Figure 5) and
its LaTeX caption, found while answering "what is the 95% CI here?":

1. **Undefined CI.** The caption said "95\% confidence interval" with no
   method named. The script's own docstring (`fig_pvalues.py:1-11`) says
   it is Newcombe's method built from Wilson score intervals on each arm's
   completion proportion — this was never surfaced in the thesis text;
   `style.caption()` is a documented no-op (`style.py:62-64`), so nothing
   in the script's docstring reaches the reader on its own.
   **Applied 2026-07-05** at `5Experimental.tex` (`fig:pvalues` caption):
   added "(Newcombe's method, built from Wilson score intervals on each
   arm's completion proportion)" after "95\% confidence interval."

2. **Mislabeled row.** `CONTRASTS[0]` (the flagship row) was labeled
   `"flagship\n(default vs corrected)"`, i.e. described as the
   heap-vs-pooled memory-architecture comparison. But its numbers (14/24
   vs.\ 0/18) are the OOC-backend-vs-capped-in-memory contrast — verified
   against the prose (`5Experimental.tex:252-254`) and `fig:flagship`'s own
   caption ("Out-of-core ... versus the capped in-memory engine"). The
   heap-vs-pooled comparison is a *different* row (`"allocation-type win
   @512\n(pooled vs heap)"`, 8/8 vs.\ 0/8). This is why the label read as
   backwards: "corrected" showing worse completion than "default" would
   contradict the thesis, because the label was for the wrong contrast
   entirely, not because the two numbers were swapped.
   **Applied 2026-07-05** at `fig_pvalues.py:23`: relabeled to
   `"flagship\n(OOC backend vs capped in-memory)"`.

3. **Axis-clipped label.** In panel (b) (log-scale $p$-axis, `xlim =
   (1e-6, 2e-1)`), the "pooled lever arms" point ($p = 8.1\times10^{-6}$)
   sits close enough to the left edge that its center-anchored text
   annotation extended past the left spine.
   **Applied 2026-07-05** at `fig_pvalues.py:79-89`: points within 0.6
   log-decades of the left edge are now left-anchored with a small
   rightward offset instead of centered; all other points are unaffected.

Regenerated `img/fig_pvalues.pdf` (isolated venv:
`scratchpad/figvenv`, matplotlib/numpy/scipy/pandas) and confirmed visually
(converted to PNG) that the flagship label and the pooled-lever-arms
annotation both render correctly.

### 19. ~~"the default architecture" reappears three times after the chapter renamed it "the heap architecture"~~ — FIXED

`5Experimental.tex:247` promises: "For brevity, this chapter also calls
the first the heap architecture and the second the pooled architecture."
But at lines 253, 298, and 392 the prose reverted to "the default
architecture" instead of using the promised shorthand — asked directly
("what is meant by default?"), confirming a reader hits this as a real
gap: nothing at those three points re-ties "default" back to "heap."
**Applied 2026-07-05.** Replaced all three with "the heap architecture,"
matching the chapter's own naming convention. `5Experimental.tex:317`
("heap architecture (engine-default, ...)") was already correct — it leads
with the shorthand and glosses the technical name parenthetically — and
was left unchanged as the model to follow.

---

## Full Glossary (by first-definition location, in reading order)

### Chapter 1 — Introduction (informal pre-definitions only, by design —
see the chapter's own comment: terms appear in plain-word form here and get
precise definitions in Ch.2)
| Term | First appears | Precise definition |
|---|---|---|
| state | 1.2 (informal: "these collected partial results are called state") | Ch.2 §2.8 |
| OOM | 1.2 | Ch.2 §2.17 |
| OOC state backend | 1.4 (informal) | Ch.4 (spillable slice store, §sec:architecture) |
| push-based (execution) | 1.5 (informal) | Ch.2 §2.2 |
| anonymous memory | 1.7 (parenthetical gloss + forward pointer) | Ch.2 §2.17b |

### Chapter 2 — Scientific Background (the thesis's glossary chapter)
| Term | Location | Definition |
|---|---|---|
| tuple | line 14 | one record (e.g. one sensor reading + timestamp) |
| DSPS | line 17 | computes results in real time from streams, no store-then-query |
| edge | line 24 | the part of the network made of small networked IoT devices |
| query / pipeline / operator | §2.1, lines 27-30 | user computation → compiled pipeline (chain of operators); operator = one processing step |
| push-based execution | §2.2, line 33 | sources push tuples through pipelines as data arrives; nothing polls |
| worker threads / tasks | §2.3, lines 38-40 | fixed thread set pulls tasks from a shared queue |
| buffer | §2.4, line 44 | fixed-size block of memory tuples travel/are processed in |
| (global) buffer pool | §2.5, lines 47-50 | fixed-size pool of buffers, allocated once at process start; **the central shared resource of the thesis** |
| pool exhaustion behavior | §2.6, lines 53-55 | thread waits ≤1s for a free buffer, else the query aborts |
| aggregation | line 62 | summarizes many tuples into one value |
| tumbling window | §2.7, line 68 | splits stream into fixed, non-overlapping time intervals; one aggregate per key per window |
| key | §2.7, line 71 | identifies one group (e.g. one sensor) |
| slice / state | §2.8, lines 74-83 | state = accumulated aggregation values; a slice is the time interval holding them; tumbling windows map 1:1 to slices |
| hash map / ChainedHashMap / page / bucket directory / entry (header+payload) | §2.9, lines 86-92 | slice state lives in a ChainedHashMap; entries live on fixed-size pages; a separate bucket-directory table points to entries; each entry = 24-byte header + payload |
| **bounded allocation** / **unbounded allocation** | §2.10, lines 94-100 ("binding vocabulary" per the source comment) | bounded = pages from the fixed global buffer pool, returned on release; unbounded = pages from the process heap. Choice depends on configured page size vs. engine buffer size (mechanism spelled out later, Ch.4 §6.2) |
| watermark | §2.11, lines 106-108 | timestamp up to which all tuples are assumed arrived; window completes once passed. *(See Flag #5 — later chapters introduce "lateness bound" and "emit lag" without linking back to this.)* |
| window phases: `WINDOW_FILLING`, `EMITTED_TO_PROBE` | §2.12, lines 111-114 | fill → emit-and-mark → GC frees slices after downstream consumption |
| KV store | line 123 | stores/reads/deletes a value under a unique key, quickly |
| RocksDB / LSM | §2.13, lines 127-130 | persistent embedded KV store from Facebook; Log-Structured-Merge design |
| memtable | §2.14, lines 133-135 | in-memory write structure; flushed to a sorted file when full |
| compaction / write amplification | §2.15, lines 138-141 | background merge of sorted files; write-amp = bytes written to storage ÷ bytes written by the application |
| RocksDB knobs: write buffer size, block cache size, compression algorithm, compaction style, column family | §2.15b, lines 145-149 | background survey; only the first three are actually varied in the evaluation (Ch.5 §sec:rocksdb-eval, line 163) — compaction style and column family are defined but never revisited, which is fine (harmless over-definition) |
| external-memory algorithms / hot data / cold data | §2.16, lines 161-165 | data exceeding memory; keep hot in memory, move cold to storage |
| cgroup / OOM killer | §2.17, lines 180-185 | Linux hard memory limit on a process group; kernel kills the process on breach. In this thesis, cgroup = the one Docker creates for the container (tied explicitly to Ch.5 §sec:reproducibility) |
| anonymous memory / file-backed memory | §2.17b, lines 188-194 | anonymous = unreclaimable working data (triggers OOM killer); file-backed = reclaimable page cache. **Load-bearing distinction**: every footprint number in the thesis is anonymous memory only |
| state pressure $\rho$ | §2.18, lines 197-199 | steady-state anonymous footprint ÷ memory cap; $\rho > 1$ means it doesn't fit |
| four run outcomes | §2.19, lines 202-206 | completes / OOM-killed / pool-exhaustion-abort / exceeds time limit |

### Chapter 3 — Research Problem
No new core vocabulary. Defines RQ0–RQ5 and the scope exclusions (fault
tolerance, durability, sliding windows, state migration, auto-tuning,
cross-system comparison).

### Chapter 4 — A Spillable Slice Store
| Term | Location | Definition |
|---|---|---|
| spillable slice store | §sec:solution-overview, lines 1-16 | replaces the in-memory slice store behind the same interface |
| (background) spiller / spiller thread | line 17; formalized §5.4 line 65 | thread waking every 100ms, moves cold slices to RocksDB |
| soft threshold / hard threshold | line 20; §5.1 lines 31-34; §5.4-5.5 lines 66-73 | soft = triggers preemptive background spilling; hard = forces synchronous spill on the allocating thread when soft-triggered spilling falls behind |
| pool-pressure levers (1a–1d) | §sec:levers, lines 93-106 | **1a**: spiller also drains when free buffers fall below a reserve (aliased "reserve lever" in Ch.5/7 — Flag #6); **1b**: immediate spill of one cold slice when an allocation finds the pool empty; **1c**: paces restores to when the pool has headroom (aliased "paced restore" — Flag #6); **1d**: counts bucket directories into the live slice budget |
| allocation backpressure / progress guard | §sec:backpressure, lines 108-126 | a would-abort allocation stalls instead, in bounded rounds, while the spiller frees space; guard aborts if no buffer returns across 2 consecutive rounds |
| growth signal / page release | §sec:growth-release, lines 128-152 | growth signal: re-evaluates thresholds on the existing-slice write path (closes the "only checked at creation" gap); page release: kernel-advises dropping a recycled buffer's physical pages once the free list holds a configured occupancy fraction |
| slice states: hot / cold / spilled / restored / emitted | §5.3, lines 47-52 | hot = still receiving data (`WINDOW_FILLING`); cold = complete, not yet emitted; spilled = in RocksDB; restored = read back for emission; emitted = delivered + freed |
| emit lag $L$ / $N_w = L/W$ | §5.3b, lines 58-62 | thesis-added: holds a completed window for configured lag $L$ before emission, creating the cold-slice population; $N_w$ = windows held concurrently as cold slices. *(See Flag #5.)* |
| serializer / byte-copy restore | §5.6, lines 76-78 | serializes pages into RocksDB KV pairs preserving exact hash-map memory layout; restore is a plain byte copy |
| spill controller visibility | §5.8, lines 84-86 | controller watches only live slice-data-page bytes; input/emission buffers invisible to it (stated as a deliberate design property) |
| page-pooling decision rule | §6.2, lines 166-170 | a page is pooled iff configured page size fits into engine buffer size; default buffer 4096B, thesis page size 8192B ⇒ default keeps every page unpooled |
| instrumentation: memory sampler (0.2s), pool gauge, unpooled gauge, map gauge, spill gauge, backpressure gauge | §sec:instrumentation, lines 180-192 | six probes total; separates working-set footprint from spill artifacts |

### Chapter 5 — Experimental Evaluation
| Term | Location | Definition |
|---|---|---|
| edge-profile container constraints | §sec:setup, lines 18-49 | memory cap (512/1024/2048MB), 2 pinned cores, SD-card spill target, tmpfs input |
| correctness verification | lines 57-58 | output checksums vs. in-memory baseline; levers-off regression arm must match unmodified engine |
| workload generator: Zipf skew, jitter, lateness bound, key-count/rows-per-window coupling | lines 61-72 | synthetic sensor stream generator. *(Flag #5 on "lateness bound"; the key-count/rows-per-window sentence was the one already fixed.)* |
| $\rho$ operationalized | §7.1.2, lines 110-114 | measured via one uncapped run's steady anon-memory footprint ÷ cap under test |
| three variants | §7.1.3, lines 116-120 | uncapped in-memory (correctness ref) / capped in-memory (the failure to prevent) / OOC backend |
| statistical tests | §7.1.8, lines 142-149 | Fisher's exact (completion), Welch's t-test (means), McNemar's (paired backend-size contrast) |
| heap architecture / pooled architecture | §sec:rq1, lines 244-247 | evaluation's short names for unbounded / bounded allocation (Ch.2 §2.10) |
| two-sided geometry law | §7.2.4, lines 326-334 | pool lower bound = must hold the hot band ($\approx N_w$ windows); upper bound = pool + induced directory overhead must fit under the cap |
| race model: ingest vs. drain, drain ceiling | §7.2.7, lines 471-499 | completion = race between ingest rate and spill-drain rate; empirical ceiling ≈2.6-2.7M tuples/s |
| fast-ingest valley vs. true capacity failure | §7.2.7b, lines 501-528 | valley = fails from mis-sized pool despite state fitting ($\rho<1$); capacity = state genuinely exceeds pool+cap |
| matched I/O granularity | §sec:rq2, lines 622-668 | ~8000-byte buffers in every variant incl. in-memory reference, to separate architecture cost from buffer-size cost |
| recycling mechanism | §7.6b, lines 795-799 | pooled pages return to the pool on spill — the source of the bounded architecture's footprint advantage |
| attribution ladder / disambiguation arm (8128B buffer) | §7.6e + §sec:disambiguation, lines 813-936 | separates allocation-type effect from I/O-granularity effect using a buffer size below the page size but at matched granularity. *(Flag #8 on restating the pooling rule here.)* |
| allocation deadlock | §7.6k-7.6l, lines 863-903 | emitted-but-unprocessed state pins the pool; the workers that would drain it are blocked allocating |
| three-regime feasibility law | §7.6l, lines 888-891 | regime 1 = fits, full speed; regime 2 = exceeds pool but cap throttles ingest (= "throttled regime" — **Flag #4**, this term is pre-used at line 706); regime 3 = exceeds pool, ingest unconstrained, deadlocks |
| geometry valley / crash cluster / slice-cache root cause | §sec:geometry-valley, lines 938-986 | corruption traced to the per-worker slice cache (defined Ch.4 line 141) bypassing spill fences with stale pointers |
| corruption vs. benign pressure-abort | §7.6v-bis, lines 972-979 | segfault (corruption, root-caused) counted separately from allocator-abort-under-cap (shares OOM-kill signature, counted as pressure failure) |
| growth signal + page release results, "synchronous eviction" | §sec:growth-release-results, lines 987-1013 | ties back to Ch.4's growth signal/page release. *(Minor: "synchronous eviction" here vs. "synchronous spill" in Ch.4 line 33 — same mechanism, drifted word choice; not separately flagged above but worth a consistency pass if you're doing a terminology sweep.)* |
| backend sizing (RB32): 16/8 vs. 32/32, campaign labels W_/N_/U_/L_ | §sec:rb32, lines 1015-1089 | single-variable RocksDB write-buffer/block-cache contrast. *(Flags #2 "map arena" at line 1070, #7 "L_" at line 1040.)* |
| Nexmark transfer: q3/q7, scenarios A/B/C, arms B2/U8 | §sec:nexmark, lines 530-620 | external-validity test; B2/U8 are spelled out inline at first use (line 551) as well as in the `\nexmarknotefull` macro (`Template.tex:88-94`) — no gap here |

### Chapter 6 — Related Work
No new core vocabulary; positions the thesis against Flink/RocksDB, WindFlow,
AION, and the evaluation-methodology literature.

### Chapter 7 — Conclusion
Recaps C1–C7, the three-regime law, and future work: role-aware allocation,
closed-loop ingest admission, epoch-stamped slice-cache invalidation,
open-window spilling. Uses "reserve lever" / "paced restore" (line 51) —
see Flag #6.

---

## Maintenance note
This file should be re-checked (or a section re-scanned) whenever prose is
added or reworded in any chapter — especially Ch.5, where most of the
thesis's mechanism-heavy explanation lives. When a flagged issue above is
fixed, move it under a `~~struck-through~~ — FIXED` heading (as done for
Flag #1) rather than deleting the row, so the history of what was checked
stays visible.
