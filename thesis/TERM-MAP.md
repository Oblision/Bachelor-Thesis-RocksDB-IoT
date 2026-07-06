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

### 17. ~~"16/8" and "32/32" used in §Experimental Setup, ~100 lines before either is explained~~ — FIXED (relocation stands; its "safe to pool" claim was WRONG — see #20)

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

### 20. ~~fig:heatmap silently summed 16/8 and 32/32 into one count — the pooling was NOT statistically safe~~ — FIXED (retracts part of #17)

Issue #17 (above) claimed the fig:heatmap pooling was "justified by Figure
`fig:rb32-arch`, which shows the two configurations complete at the same
rate once between-session drift is controlled for." You pushed back:
looking at Figure 19 and Appendix E.6 directly, the McNemar $p$-values
*don't* support that — several cap/campaign rows are significant (W\_ pooled
at 512\,MB: $+15$pp, $p=0.002$; L\_ heap at 2048\,MB: $-6$pp, $p=0.004$),
meaning the two configurations' completion demonstrably differs in exactly
the cells that matter. My error: I had conflated the reassuring
*same-session drift control* (a separate, smaller $n=80$ re-run, Appendix
E.7) with the actual cross-session data that gets pooled into the heatmap's
cells — the control's $p=0.21$ never applied to the real pooled counts.

**Checked the underlying data directly** (`figures/scripts/data.py`: the
16/8 and 32/32 runs share a `campaign` prefix and get merged by
`gc.iter_cells`/`arm_frames` purely because they match on
cap/pool/workload, with no check that their completion behavior agrees).
Cross-tabulating `is_b32` per cell (pooled W\_ and heap L\_ campaigns, all
three caps) surfaces divergences far starker than the McNemar table alone
suggested, e.g.:
- cap 512, W\_, pool 16k, r150: 16/8 = 1/8, 32/32 = 8/8
- cap 512, L\_, pool 32k, r100: 16/8 = 9/10, 32/32 = 0/10 (a full reversal)
- cap 1024, W\_, pool 32k, r300: 16/8 = 0/8, 32/32 = 7/8

A merged count (e.g. "9/16") would hide exactly this — the disagreement is
the informative part, not noise to average away.

**Applied 2026-07-05.** Per your instruction ("keep one figure ... separate
the runs ... so we can see ... where both work and where only some work and
which one"): `figures/scripts/fig_heatmap.py` now splits any cell that has
a 32/32 twin into two half-cells (left = 16/8, right = 32/32), each
independently colored and labeled with its own completion count; cells with
no twin stay a single block (`_draw_cell` vs. `_draw_split_cell`). No
figure or data is summed anymore. Regenerated `img/fig_heatmap.pdf`
(isolated venv) and confirmed visually. Rewrote the §7.2.3 paragraph and the
`fig:heatmap` caption (`5Experimental.tex`) to describe the split instead of
asserting the retracted "safe to pool" claim.

**Left as an open item, not fixed:** `fig_geometry_law.py` and
`fig_knife.py` (Figures~\ref{fig:geometry-law},~\ref{fig:knife}) pool the
same two configurations via the same shared `gridcells.py` helpers and were
NOT redesigned — `fig:geometry-law`'s per-cell failure-reason/hatching
encoding has no obvious two-configuration layout, and `fig:knife` is a bar
chart, not a grid. Their captions no longer claim consistency with
`fig:heatmap` (which was the only thing fixed there): `fig:geometry-law`'s
caption now says plainly that its cells still sum the two configurations;
`fig:knife`'s caption now gives the 16/8-alone and 32/32-alone numbers
side by side (verified against the data: 16k = 10/10 both, 32k = 6/10 vs.
5/10, 64k = 0/10 both — here the two configurations agree closely, unlike
the heatmap's worst cells).

### 21. ~~"an allocation finds it empty and the run aborts" reworded; Figure 7 split into three; fig:heatmap split-cell readability and an in-mem labeling bug~~ — FIXED

Three separate requests handled together:

**a) `5Experimental.tex:330` reworded.** Checked against the mechanism
Ch.4 already establishes (`4Solution.tex:112`, §Allocation Backpressure:
"When it does and all buffers are in flight, NebulaStream aborts the next
allocation"; `4Solution.tex:103`, Lever 1b: "an allocation that finds the
pool empty") — the Ch.5 sentence was factually consistent with this but
vague about whose allocation, and didn't cite where the mechanism was
established. Reworded: "When the pool cannot supply the concurrently open
hot band, a worker's buffer request finds the pool empty and NebulaStream
aborts that allocation (Section~\ref{sec:backpressure}), which kills the
run."

**b) Figure 7 (`fig:geometry-law`) split into three.** You asked for this
because the three cap panels (512/1024/2048\,MB) tell different stories
and one caption could not honestly cover all three. Checked what each
panel actually shows before writing captions: 512\,MB is the clean
textbook two-sided law (abort / sweet spot / OOM, cleanly separated by
column); 1024\,MB is transitional — even the largest pool tested (48k)
still fully aborts the heaviest workload (r500, 0/16), so the OOM edge is
never reached for it; 2048\,MB is abort-dominated almost everywhere, because
the workloads tested there are heavier (r200-r1000 vs. 512\,MB's r75-r300)
and only the largest pool or lightest workload complete.
**Applied 2026-07-05.** `figures/scripts/fig_geometry_law.py` now saves
three standalone figures (`fig_geometry_law_512/1024/2048.pdf`) instead of
one 3-panel figure; `5Experimental.tex` now has three `\begin{figure}`
blocks with labels `fig:geometry-law-512/1024/2048` and per-cap captions,
with the shared color/hatch encoding explained once in the prose
beforehand. Updated all four `\ref{fig:geometry-law}` call sites elsewhere
in the chapter to a `Figures~\ref{...-512}--\ref{...-2048}` range.

**c) fig:heatmap (Figure 6) split-cell readability, raised after #20's fix.**
Three things, addressed together:
- The "split cells: left = 16/8..." annotation was a `fig.text()` at a
  fixed figure coordinate, which `constrained_layout` (this project's
  standard, `style.py`) does not account for, so it could collide with the
  suptitle/panel titles. Merged it into `fig.suptitle()`'s second line
  instead, which constrained_layout does lay out correctly.
- The only thing distinguishing a split cell's two halves was a black
  divider line at nearly the same weight as the ordinary white
  cell-to-cell grid lines — easy to miss. Per your instruction not to use
  color (already spoken for by the completion fraction), the right
  (32/32) half now carries a dot hatch, and the divider line is heavier
  (0.7 → 1.4pt); the legend gained an entry showing this.
- **Checked the data behind this again and found a real bug**: the in-mem
  reference column also carries a `b32`-tagged duplicate for exactly half
  its rows, at every cap (confirmed: 50 of 100 rows, all three caps). But
  the in-memory reference engine never touches RocksDB, so this "twin" is
  not a real 16/8-vs-32/32 configuration difference — it is the same
  configuration, re-measured in a different session (the same kind of
  session-labeling artifact, just on an arm where the distinction is
  meaningless). Splitting that column would have implied a contrast that
  does not exist. **Applied**: `_panel` in `fig_heatmap.py` now merges the
  in-mem column's two sessions as ordinary extra repetitions instead of
  splitting it (`j == 0 and with_inmem is not None` skips the split path).
  Regenerated and visually confirmed the in-mem column is now a single
  block again (e.g. cap 1024, r100: "20/20") while the pool-size columns
  still split correctly.

Also removed the "Untested cells are marked by a grey dot" sentence from
both the `fig:heatmap` and `fig:geometry-law` captions, and the
corresponding unused `Line2D` legend entry from `fig_heatmap.py`: checked
both figures' actual grids for empty cells (`runs.empty` in
`gc.iter_cells`) and found zero in each, across all caps and both arms —
the grey-dot legend key never had a matching mark anywhere in the
rendered grid, which is its own kind of confusing. `fig_geometry_law.py`
and `fig_knife.py` never added a grey-dot entry to their own legends, so
no fix was needed there.

### 22. ~~"23 of 24" (fig:pvalues row 5, and the regime-law figure) silently combines three different lever-configuration arms against a single-arm baseline~~ — FIXED

You asked whether Figure 5 (fig:pvalues) combines several runs together,
since the rep counts across its five rows look inconsistent (24, 8, 16, 8,
24 vs. 18, 8, 16, 8, 10). Checked each row's provenance:

- **flagship** (14/24 vs. 0/18): combines 3 workloads x 8/6 reps —
  already disclosed in `fig:flagship`'s own caption ("8 repetitions per
  workload... 6 repetitions"). Fine as is.
- **pooled lever arms** (12/16 vs. 0/16): combines 2 arms per side (B1+B2
  vs. unpooled S\_A1+A2) — already disclosed at `5Experimental.tex:845`,
  "Taken together, the pooled lever arms complete 12 of 16...". Fine as is.
- **intrinsic backpressure** (23/24 vs. 3/10): **not** disclosed, and the
  surrounding sentence actively pointed the wrong way. Traced this to
  `figures/scripts/fig_regimes.py`'s own docstring, which already had the
  verified breakdown: `N_B0/B1/B2 = 7+8+8 of 24` (three pooled-lever arms:
  no levers, and the two lever configurations from the attribution ladder
  above) against `W_c1024_p32k_r200 = 0/8`, a single run of a *different*
  campaign at the other cap. `5Experimental.tex:864` read "Pooling helps
  elsewhere **with no levers at all**" immediately before citing the
  23/24 number — but 2 of the 3 combined arms *do* have levers on; the
  phrase pointed at the opposite of what the combined figure contains.
  Same non-disclosure recurred at `5Experimental.tex:915` (prose) and the
  `fig:regimes` caption (`5Experimental.tex:921-924`) — three separate
  places citing "23 of 24" with no mention that it is a 3-arm sum.

**Applied 2026-07-05** at all three locations. The mechanism claim survives
and is in fact stronger once stated correctly (the effect holds regardless
of lever configuration, not only without levers):
> Pooling helps elsewhere independently of the levers. A blocked
> allocation throttles ingest to the drain rate at one cell, and every
> pooled arm tested there recovers it, lever-off and lever-on alike.
> Combining all three pooled arms (no levers, and the two lever
> configurations above) against the single heap-architecture baseline,
> the cell rises from 3 of 10 to 23 of 24 ($p = 1.6 \times 10^{-4}$).

And at the regime-law passage and its figure caption (careful not to imply
the 1024\,MB side is also a 3-arm combination — it is one run of a
different campaign):
> One workload shows the law directly: its three pooled-lever arms
> (above) combine to complete 23 of 24 at cap 512\,MB, while a single run
> of the baseline pooled campaign dies 0 of 8 at cap 1024\,MB with twice
> the pool.

`fig_pvalues.py`'s own row label, `"intrinsic backpressure\n(bounded vs
heap)"`, was left unchanged — at the architecture level (any pooled
config vs. heap) it was already accurate; the missing disclosure was in
the surrounding prose, not the label.

### 23. ~~RQ1 rebuilt on the 16/8 baseline alone: split-cell heatmap retired, per-cap figures, subsections, and two more scripts that silently pooled 32/32 (and one phantom chk-cell)~~ — FIXED

You asked to (a) simplify RQ1 down to the 16/8 runs only ("that's what we
said we're going to stick with"), (b) split the operating-window figure per
cap as was done for the geometry law, (c) restructure RQ1 into subsections
with the supporting prose next to each figure, and (d) "make sure the data
points for the 16/8 data is used in this section and not the 32/32 data
points". Applied 2026-07-05:

**Figure scripts — 16/8 filter (`~df["cell"].str.contains("b32")`) added:**
- `fig_heatmap.py`: split-cell design retired entirely; now emits three
  standalone figures `fig_heatmap_{512,1024,2048}.pdf` (heap+in-mem panel
  over bounded panel, per cap). Old combined `fig_heatmap.pdf` orphaned.
- `fig_geometry_law.py`, `fig_knife.py`: same filter. Knife counts change
  from the pooled 20/20, 11/20, 0/20 to 10/10, 6/10, 0/10 (all OOM,
  verified from rc codes).
- `fig_effectiveness.py`, `fig_race.py`: **were still silently pooling the
  32/32 twins** (your (d) — confirmed correct). Filter added. Race-figure
  headline numbers moved: death zone 40/40 → **23/23 fail**; ρ<1 failure
  groups 16 capacity / 46 pool-geometry → **10 / 23**.
- `fig_race.py` + `fig_effectiveness.py` additionally never applied
  `gridcells.grid_df` (drops chk/ref verification reruns whose rc141
  teardown reads as a pool abort). One phantom all-fail cell,
  `W_chk_c1024_p32k_r100` (0/2, rc141), was inflating the pool-geometry
  group to 24. Both scripts now route through `grid_df`.

**Prose (`5Experimental.tex`, sec:rq1) restructured into five subsections**
(Headline Contrasts / Operating Windows of the Two Architectures / The
Two-Sided Geometry of the Bounded Window / Where Each Architecture Wins /
Completion as a Race), with a per-cap paragraph next to each new heatmap
figure. Blocks 7.2.6/7.2.6b moved from before tab:windows into the Race
subsection. A scope note at the top of RQ1 states the section is 16/8-only
and points the 32/32 contrast at Section 5.7.4 (sec:rb32); the split-cell
justification paragraph and the two "unlike Figure X, these still sum both
configs" caveats (geometry-law prose, fig:knife caption) are deleted.

**Data findings folded into the new per-cap prose (all verified against
the TSVs and `ANALYSIS-RQ.md`):**
- 1024/r500 completes 0 at every pool under BOTH architectures despite
  ρ 1.34 < r300's 1.54 (which completes 8/8 at 48k). Not a data error:
  ρ orders total measured footprint (uncapped calibration runs), the label
  number orders the input streams (r300 = old generator, r500/r700/r1000 =
  regenerated; `ANALYSIS-RQ.md:37-40`), and r500's failures are 100%
  rc141 pool aborts — its hot band exceeds any pool that fits under 1024
  (it needs 96k, first available at 2048).
- 2048 heap: per-workload completion bands move toward smaller pools as
  heap-resident state grows (r700@16k-32k, r500@32k-64k, r200@64k-128k);
  no heap pool column completes >2 of 5 workloads where bounded 96k
  completes 4. r300 is the band exception (fails everywhere, free-runs
  >2x r700's ingest) — race, not footprint.
- The "user's 32/32-better-at-low-cap" reading stays OUT of RQ1 prose;
  it belongs to sec:rb32, which already reports exactly that
  (W_ +15pp / N_ +33pp at 512, wash at 1024/2048).

**Fixed while re-verifying captions against the regenerated figures:**
- geometry-law 512 caption: "small pool aborts every workload but r75
  (7/9)" was pooled-data; 16/8 truth is r75 3/8, and r200/r300 die by OOM
  even at 8k. Caption rewritten to the actual per-column pattern.
- geometry-law 1024 caption: "the same two edges appear" — false on 16/8
  data (no plain OOM cell at this cap; single OOM rep inside r300's mixed
  16k cell). Rewritten: only the abort edge is reached. "r500 0 of 16" →
  0 of 8; "heaviest workload" → "most pool-demanding workload" (r500's ρ
  is NOT the largest in the panel).
- geometry-law 2048 caption: "OOM edge barely appears" → never reached.
- 7.2.6b's "a parallel measurement session completes the ρ≈0.89 point 6
  of 8" was actually the **32/32 twin** (`W_b32_c512_p16k_r100`) presented
  as a same-config repeat — now disclosed as "the same cell rerun under
  the 32/32 backend configuration of Section sec:rb32".
- "90 to 100 percent recover at the geometry-correct pool" no longer holds
  for the 16/8-only pool-geometry group: r300@2048 recovers only 5/8
  (bounded 96k), heap none. Reworded to "five of the group's six
  cap-and-workload coordinates ... the exception is r300 at 2048 MB".
- 7.2.6's ρ values aligned to the figure-printed 16/8 rho table
  (0.74→0.77, 0.80→0.82); r1000's ρ 1.03 → 1.01 (prose + tab:windows).

Full document compiled twice+ with pdflatex: zero errors, zero undefined
references. All five regenerated figures visually verified via pdftoppm.

**Open (flagged, not yet acted on):** the RQ2/4/5 figure scripts
(`fig_cost.py`, `fig_cpu_io.py`, `fig_composition.py`) also load runs
without the b32 filter; user's instruction covered RQ1 ("this section").
Same question applies there — awaiting a decision before extending.
*(Resolved in Issue #24: user directed "complete the open items".)*

---

## Issue #24 — b32 filter extended to the remaining figure scripts; RQ4 prose recomputed; two Fisher-value errors (2026-07-05)

User directive: "complete the open items accordingly" (closing Issue #23's
open question). Audited all 11 remaining `fig_*.py` scripts that had no b32
handling.

**Audit result per script:**
- `fig_cpu_io.py` — EXPOSED: campaign-based selection (`L/W/I8`) admitted
  b32 rows because b32 cells keep their parent campaign letter. 317 of 684
  selected ok-rows (88 → 48 plotted cells) were 32/32 twins. Filter added.
- `fig_composition.py` — EXPOSED: panel (b)'s campaign `L/W` selection
  admitted 181 of 374 spill rows. Filter added. Panel (b) story (heap/bounded
  on-disk volumes coincide) unchanged on 16/8-only data.
- `fig_cost.py`, `fig_flagship.py` — exact-cell / campaign-'b' selections can
  never hit a `*_b32_*` cell, but both fed the unfiltered df into
  `rho_table()`, which mixes b32 calibration reps into the r500/r700/r1000
  footprint medians (1361→1373.5, 1651→1675.5, 2077→2077.5 MB — none used by
  these figures' coordinates, so zero visual change). Filter added as a guard.
- `fig_gridlock.py`, `fig_ladder.py`, `fig_nexmark.py`, `fig_nexmark_cost.py`,
  `fig_nexmark_sweep.py` — safe: exact full-cell-name matches; b32 cells carry
  the `b32` infix (`NX_b32_q3_...`) and cannot collide. No change.
- `fig_pvalues.py`, `fig_regimes.py` — load no run data. No filter needed.

**RQ4 prose/caption/figure recomputed on 16/8-only data** (old numbers were
DATA-AUDIT's 2026-06-12 mixed-data pass):
- in-memory band 91–97% → **84–97%** (the 2–3 s r075 cells sit at 84.4/84.5%);
- "spilling winners run at 89–92%" → completing spilling cells beyond ρ=1 run
  at **62–87%** (the hotter 89–92% band came from the pooled 32/32 twins);
- "restores served from the page cache" re-scoped with the boundary it
  actually has: device reads ≈0 in 19 of 21 spilling cells **below** ρ=1;
  **above** ρ=1 every spilling cell reads 57–511 MB from the device;
- throttled regime 63% → **62%** (cell median of W_c512_p16k_r200, 8/8 ok;
  278 MB read + 525 MB written; summary-table row updated too);
- NEW pattern surfaced by the un-mixed data, added to prose: at every cap the
  bounded arm's largest probed pool yields the coolest cells (31% r75@512/24k,
  64% r100@1024/48k, 54% r700@2048/96k) with near-zero device reads and up to
  3× stretched runtimes — pool backpressure (mechanism of sec:rq1-windows);
- in-figure annotation "63%/54% are genuine idle" → "points ≤64% are genuine
  idle" (16/8 low points: 31.3/52.9/53.8/61.8/64.2%).
- DATA-AUDIT.md RQ4 paragraph got a dated addendum pointing here.

**Two Fisher-value errors found while verifying (pre-existing, exposed by the
filter work):**
1. `fig_flagship.py` mislabeled sidedness: 4.1e-5 is the TWO-sided p for
   14/24 vs 0/18 (one-sided is 3.71e-5). Prose and tex caption already said
   two-sided (correct); the script's docstring and caption-footer said
   one-sided. Fixed in the script; figure annotation value unchanged.
2. The "allocation-type win @512" contrast: prose 7.2.2b and fig_pvalues
   quoted p = 2.3e-5 for "8/8 versus 0/8" — that p matches NEITHER sidedness
   of that table. 2.3e-5 is the one-sided p for 8/8 vs **0/10**, i.e. bounded
   (W_c512_p16k_r200, 8/8) vs capped in-memory (L_c512_inm_r200, 0/10) — a
   different contrast. The pooled-vs-heap contrast (vs U_c512_p16k_r200, 0/8)
   is p = 7.8e-5, which 7.6p and 7.2.10 already quoted correctly. Fixes:
   7.2.2b and the fig_pvalues row → 7.8e-5; tab:windows bounded-512 row
   counts corrected "8/8 vs 0/8" → "8/8 vs 0/10" (its p = 2.3e-5 was right).
   fig_pvalues axis/docstring sidedness labels made honest (one-sided;
   flagship two-sided). All five rows now reproduce exactly from their counts
   via scipy `fisher_exact`.

Orphaned combined PDFs deleted (`img/fig_heatmap.pdf`,
`img/fig_geometry_law.pdf` — zero references in the tex tree).
Regenerated: fig_cpu_io, fig_composition, fig_cost, fig_flagship,
fig_pvalues; all visually verified via pdftoppm. Full document recompiled:
zero errors, zero undefined references.

---

## Issue #25 — what the r-labels mean (now a table); Figure 13 boundary + pool-geometry misreads fixed (2026-07-05)

User (reading Figure 13 = fig:effectiveness, "Completion at the memory
boundary") raised three things: (a) r300's ρ > r500's "doesn't make sense";
(b) a 10/10 in-memory cell sits just past the ρ=1 line in panel (a); (c) the
r-codes (r200/r300/r500) are never explained — "is it memory? throughput?".

**What r$N$ actually is (reverse-engineered from the run harness; CSVs are
gitignored, `.planning/eval-phase/lag-validation/work/gen_skew.py` +
`large_matrix.sh`):** each label names one pre-generated Zipfian/out-of-order
input stream. The number is the stream's **target state pressure ρ×100 at the
512 MB reference cap** for the original family. Measured footprints (median of
10 uncapped in-memory reps, ±6 MB spread) confirm it:
`r075=327, r100=458, r150=736, r200=1034, r300=1582` MB → ρ@512 =
`0.64, 0.89, 1.44, 2.02, 3.09` (matches 0.75/1.0/1.5/2.0/3.0). A **later batch**
`r500=1374, r700=1676, r1000=2078` MB was regenerated on a different scale
(the harness calls them "new ρ-files"); monotonic within itself but offset, so
**r500 (1374) < r300 (1582)** — the one inversion. → New **Table~\ref{tab:workloads}**
in §5.1 (sec:setup) listing footprint + ρ at each cap; prose para [7.1.2b]
states the measured ρ, not the label, is authoritative.

**Exit-code taxonomy now tabulated** (user asked for the "R-codes" as a table
in ~5.1). New **Table~\ref{tab:outcomes}**: Complete=0; OOM kill=137/137c/134;
Pool abort=141; Crash=139/11. Mirrors `gridcells.classify_rc` exactly. Para
[7.1.5] frames OOM (footprint > cap) vs pool abort (pool < hot band) as the two
sides of the geometry law with opposite cures.

**Figure 13 panel (a) — the 10/10 past ρ=1** is r200@1024 (ρ=1.0098). NOT an
error: ρ is measured on an *uncapped* run (footprint 1034 MB, `unc_r200`), which
runs ~10% fatter with no reclaim pressure; the same workload **capped at 1024
peaks at 948 MB** (`L_c1024_inm_r200` peak_anon median) and fits. The two cells
at ρ≈1.01 split on this margin: r200@1024 completes 10/10, r1000@2048 (genuinely
> cap) fails 0/10. Added an on-figure callout + prose [7.2.6c] + caption note;
reworded panel-(a) annotation from "no completion past ρ≈1" to "collapses once
state clearly exceeds memory".

**Figure 13 panel (b) — r500@1024 fails (0/10 both) while r300 to its right
(higher ρ) completes 8/8.** The central out-of-core lesson, not a mislabel:
completion is set by **pool geometry, not total footprint**. r500's hot band
needs a 96k pool (96000×8192 B ≈ 786 MB) that can't fit under 1024; its heap
failures are all rc137 (OOM), bounded all rc141 (pool abort). r300 completes
through a 48k pool that fits (`W_c1024_p48k_r300` 8/8). Added a panel-(b) callout
arrow + prose + caption note. Cross-referenced tab:workloads from the
heatmap-1024 r500 paragraph.

fig_effectiveness.py: two annotations added, both visually verified via
pdftoppm. Full document recompiled: 107 pages, zero errors, zero undefined
references; both new tables render cleanly (Tables 3 and 4).

---

## Issue #26 — three figure clarifications: race capacity->pool-geometry, Fig 24 "base" defined, Nexmark expanded + stale cost ranges fixed (2026-07-05)

Three parallel read-only investigation agents verified every number against the
run data; I verified their load-bearing claims independently, then applied.

**Fig 14 (fig:race) — SUBSTANTIVE NARRATIVE CHANGE.** The slow r500/r700 rho<1
failures were labelled "true capacity deaths (per-window state exceeds pool and
cap)". This is **contradicted by the data**: r500@2048 completes 10/10 (heap
32k/64k) and 8/8 (bounded 96k); r700@2048 completes 10/10 (heap 16k/32k) and 8/8
(bounded 96k). Their state fits (rho 0.67/0.82); they die only at a mis-sized
pool. So BOTH rho<1 groups are pool-geometry failures; they differ only in ingest
rate vs the drain ceiling (slow r500/r700 under it; fast r75-r300 above it =
fast-ingest valley). Rewrote the fig_race.py legend ("capacity"/"pool geometry"
-> "slow, under drain ceiling"/"fast, over drain ceiling (heap+bounded)"),
caption, and the [7.2.7b] prose block; fixed the misleading code comments/print.
The internal var names fail_truecap/n_truecap kept (historical) with a comment.
Verified: r500/r700 @2048 completion-by-pool.

**Fig 24 (fig:rb32-compartments) — "base" defined.** Generator is
`thesis/experiments/rb32-config-contrast/fig_rb32.py` (not in figures/scripts/);
`base = max(0, peak_anon - pool_mb - unpooled - rdb_rss)` — a pure residual over
the W_b32_* cells. Old caption called it "the fixed engine and runtime footprint"
— WRONG: reproduced medians show base(ok) = 198/419/813 MB at 512/1024/2048
(~40% of cap, the single largest compartment), so it is NOT fixed. It comprises
the release engine/runtime baseline (tens of MB) PLUS un-instrumented working set
(emission + in-flight buffers, slice-store bookkeeping, RocksDB working memory
beyond gauge, allocator retention). Fixed the figure legend ("base (residual:
engine/runtime + working set)"), the caption (full definition + growth 198->813),
the decomposition prose gloss, and the geometry-law "base footprint" first use
(line 908, noting RocksDB is folded into that 3-term version). Also fixed caption
"Median resident" -> "Median peak resident" (metric is per-cell peak_anon).

**Figs 15/16 (Nexmark) — expanded + TWO STALE RANGES FIXED.** Verified the full
completion matrix (q3/q7 x A/B/C x 512/1024 x inm/B2/U8) and cost matrix.
- STALE (figures regenerated at badbb35, prose not updated): fitting-cell loss
  "42--54%" -> **42--61%** (verified min -42.0 q3 B@1024 B2, max -61.2 q3 B@512
  B2); winning-cell "46--59%" -> **50--63%** (verified C@1024 both-arms -50.3 to
  -63.0). Fixed in prose (2x) and both figure captions.
- pool "about 132 MB" -> **125 MB** (recorded pool_mb at 512).
- Added a B2-vs-U8 three-regime paragraph (fits+headroom A@512/B@1024: heap 8/8
  all four, bounded throttled 2-6/8; tight B@512: both fail, bounded 141 / heap
  137; beyond-memory C@1024: bounded 8/8,7/8 vs heap 6/8,3/8) and a cost-
  comparison paragraph (heap cheaper at 6/7 cells by 2-12pp; survivor bias at
  C@1024). Reworded the awkward "At the fits cells..." caption sentence.
- OOC acronym: `\acro{OOC}{out-of-core}` (Template.tex:217), first use Ch1 so
  Ch5 renders short "OOC". Converted the 9 spelled-out "out-of-core" in the
  Nexmark section (sec:nexmark, 693-795) to `\ac{OOC}`. **DEFERRED to wave 2:**
  the chapter-wide OOC consistency sweep (18 more occurrences elsewhere in Ch5;
  the one in fig:cost's \caption[...] LoF arg must stay spelled out).
- NOT changed (agent false alarm I caught): "234 MB restored" IS reproducible =
  the q7 C@1024 B2 cell's device reads (610 MB writes); it is a valid number.

fig_race.pdf and fig_rb32_compartments.pdf regenerated; full doc compiles at
108 pages, zero errors, zero undefined references; all three verified in-render.

---

## Issue #27 — RQ2/RQ4/RQ5 restructured into scientific analysis; Fig 19 caption trimmed (wave 2a, 2026-07-05)

User: the non-RQ1 sections "seem a bit minimal", read as disconnected one-liners;
each RQ must be "addressed in a proper manner ... scientific analysis and the
individual aspects that compose the data and the answer". Two parallel read-only
agents drafted restructures (RQ2+RQ4, and RQ5+Fig19); I verified numbers and
applied. Each section now has: an OPENING framing how it answers the RQ + naming
its aspects, a threaded BODY over the component aspects, and a CONCLUDING "In
answer to RQ_n:" paragraph.

- **RQ2 (Cost):** opening (three moves: throughput / tail latency / footprint);
  merged throughput 7.3.1+7.3.2; tail-latency thread reordered confound-first
  (7.3.4 then 7.3.3) with the distributions sentence (old 7.3.7) folded in and
  its stranded copy deleted; granularity-finding bridge; closing answer. Numbers
  re-verified via fig_cost's exact mean-based logic: throughput 11--15% (heap
  -28% at 512/r75 is the flagged short-cell outlier, excluded), p99 1.1--1.6x,
  footprint -33% (918 vs 1385 MB), r700 tail 2491->902 us / +37% (prose 2509/899
  /36%). All hold.
- **RQ4 (Utilization):** opening frame; re-paragraphed the 10-sentence blob into
  measurement-basis / boundary-read-divide / two-idle-mechanisms (parallel "One
  is disk wait... The other is pool backpressure..."); closing answer. Numbers
  carried verbatim from the wave-1-corrected section.
- **RQ5 (Storage):** opening (four aspects); threaded composition / reducible-
  overhead(vtable + bucket dir) / write-amp / pool-sizing; closing answer
  (efficient; write-amp 1.0-1.8; ~17-20% reducible-but-unimplemented). Added
  panel refs Figure~\ref{fig:composition}(a)/(b) (fig went 0 -> 4 citations).
  Race cross-ref sharpened sec:rq3 -> sec:rq1-race.
- **Fig 19 caption trimmed** ~20 -> ~9 lines: now describes only what each panel
  shows + defers interpretation to sec:rq5; the moved sentences (directory
  spill/restore byte-copy; panel-(b) heap/bounded coincidence) relocated into the
  RQ5 body. "Median resident" kept; note the metric is per-cell values.

Compiles at 109 pages, zero errors, zero undefined references; RQ4 render
verified. DEFERRED to wave 2b: cross-reference pass for the still-uncited figures
(fig:flagship=Fig4, fig:race=Fig14, fig:geometry-law-1024, fig:gridlock,
fig:regimes; plus panel refs for fig:cost/fig:cpu-io), intro/answer framing for
RQ1 and RQ3, and the chapter-wide OOC acronym consistency sweep.

---

## Issue #28 — RQ1/RQ3 framing, figure cross-references, chapter-wide OOC sweep (wave 2b, 2026-07-05)

Done directly (editorial, full context in hand), completing the user's wave-2
requests.

- **RQ1/RQ3 framing** for red-thread consistency with the restructured RQ2/4/5:
  RQ1 opening now states the RQ question + closes with an "In answer to RQ1:"
  paragraph after Nexmark (backend prevents OOM within a characterized window;
  which arm holds it depends on the budget; transfers to Nexmark). RQ3 opening
  ties to its question; new "In answer to RQ3:" paragraph before the summary
  (two-sided feasibility law + ingest-drain race; three failure kinds ->
  Table~\ref{tab:outcomes}; bounded arm makes the corridor navigable). The
  Summary section already carried per-RQ narratives; these are the in-place
  section conclusions that complement it.
- **Figure cross-references (Task 6):** audit found several figures with ZERO
  \ref citations. Added: fig:flagship (Fig 4) in 7.2.1; fig:race (Fig 14) at
  "Completion is therefore a race"; fig:cost (Fig 17) panel refs (a)/(b)/(c) on
  the three cost dimensions; fig:cpu-io (Fig 18) panel refs (a) idle / (b) disk
  volume; fig:gridlock (Fig 21) at the deadlock; fig:regimes (Fig 22) at the
  three-regime law. fig:geometry-law-1024 was already covered by the range ref
  "Figures~\ref{...-512}--\ref{...-2048}" (= Figs 9--11), left as-is.
- **Chapter-wide OOC sweep:** converted the remaining 14 spelled-out
  "out-of-core" in Ch5 to \ac{OOC} (renders "OOC"; first use is Ch1). Protected
  the one in fig:cost's \caption[...] LoF optional argument (moving arg) -- it
  stays spelled out. Ch5 now uses OOC consistently everywhere except that LoF
  entry.

Compiles at 109 pages, zero errors, zero undefined references; RQ2 render
verified (Figure 17(a)/(b) panel refs + OOC + answer paragraph all correct).
Chapter-5 RQ-section restructuring (Issues #26--28) complete.

**Follow-up (same day) — OOM acronym + stray OOC.** The OOC sweep above missed
that "out-of-memory" was still spelled out in Ch5, and one "Out-of-core" too.
Ch2 already establishes the convention ("This thesis writes OOM for out of
memory") and Ch5 uses bare "OOM" in ~30 places, so the spelled-out forms were
inconsistent. Converted to bare "OOM" (matching the chapter's existing style):
L168 "an OOM kill means...", L181 outcomes-table cell "the cgroup OOM killer",
L319 RQ1 opening "prevents the OOM failures", L798 RQ1 answer "prevents the OOM
failure". Fixed one stray L340 "Out-of-core" -> \ac{OOC} in the flagship
\caption's *mandatory* argument (\ac safe there; renders "OOC"). The only
remaining spelled-out "out-of-core" is L835 fig:cost \caption[...] LoF optional
arg, which must stay spelled out. Recompiles clean (109 pp, 0 err, 0 undef).

---

## Issue #29 — Fig 4 heap label, 5.3.1 as preview, Fig 5 heap win-vs-loss explained (2026-07-05)

Reader-facing clarity fixes on the RQ1 headline area, each verified against data.

- **Fig 4 (fig:flagship) heap-only.** The out-of-core arm is the heap
  architecture only (data: `b_ooc_*` cells classify as `heap` in
  `data._classify_arm`; text L328 already said so), but the figure never showed
  it. Changed the legend to "out-of-core, heap arm" and the caption to "\ac{OOC}
  under the heap architecture ... Only the heap architecture is shown here; the
  bounded architecture at this cap is mapped in Section~\ref{sec:rq1-windows}."
- **5.3.1 (Headline Contrasts) reframed from conclusion to preview.** User: it
  "sounds more like a conclusion" and "jumps ahead"; a lead subsection should
  hint + outline. Rewrote [7.2.1]/[7.2.2] so it previews the anchor contrast
  ("That number is the headline, not yet the argument") and outlines 5.3.2--5.3.6
  with forward refs. Trimmed the duplicate roadmap out of the 5.3 section intro.
- **Fig 5 (fig:pvalues) heap win-vs-loss.** User confused how heap wins 14/24
  (row 1) yet loses 0/8 (row 4, allocation type). VERIFIED at r200/512: flagship
  heap = engine-default `b_ooc` (4K buffers) 8/8 at peak_anon 411 MB; row-4 heap
  = **matched-granularity control** `U_c512_p16k` (arm `heap8128`, 8128-byte
  buffers) OOM-killed (rc137) 0/8 at 496/512 MB; pooled `W_c512_p16k` 8/8 at
  390 MB; row-5 heap = `L_c512_p16000` 3/10, bounded = N_B0/B1/B2 = 23/24. Fixed
  the Fig 5 row descriptors to name the isolated axis and WHICH heap ("flagship:
  spill vs no spill"; "allocation type (r200, 512 MB) / pooled vs matched-gran.
  heap"; "intrinsic backpressure (r200)"). Added a paragraph [7.2.2c] resolving
  the tension: different baselines (spill-vs-no-spill vs bounded-vs-unbounded
  allocation) and different heap configs -> not a contradiction. All MB figures
  reproduced from the data before writing.

## Issue #30 — Ch5 figures kept in their subsection via \FloatBarrier (2026-07-05)

User: "figures show up in a completely different section" -- text discusses a
figure, then the float appears amid the next subsection. Confirmed by render:
Fig 5 (discussed in 5.3.1) rendered at the top of p28, which is all 5.3.2 text.
Cause: `placeins` was loaded as `[section]` only, so subsections had no float
barrier (the package has NO `[subsection]` option -- tried it, fatal error).
The preamble comment itself states the intent as barriers at "(sub)section
boundaries." All 21 figures + 9 tables live in Ch5, so a scoped fix is safe:
added `\FloatBarrier` before each figure-bearing subsection boundary -- RQ1
(before 5.3.2/5.3.3/5.3.4/5.3.5/5.3.6) and RQ3 (before 5.7.1/5.7.2/5.7.3/5.7.4).
Each subsection's figures now flush before the next heading. Rendered checks
(p28, 35, 36, 39, 64) show clean transitions, no bad mid-section whitespace;
"float too large" warnings are the pre-existing large setup/cost tables forced
to their own page. Compiles clean (111 pp, +1 page, 0 err, 0 undef).

Commits this session: `cb3fe5f` (OOM/OOC), `93616fd` (Fig4/5.3.1/Fig5),
`88451bd` (FloatBarrier). Not pushed.

---

## Issue #31 — three-reviewer pass + Fig 13 capped-footprint move (2026-07-06)

Ran three read-only reviewers (data/numeric, prose/clarity, structure/cross-ref)
over Ch5; I re-verified every load-bearing item against the data before editing.

**Fig 13 (fig:effectiveness) — user request.** Panel (a): r200@1024 completes
10/10 because its CAPPED peak (median 948 MB, verified) fits, though uncapped
rho=1.01. Plotted that one baseline cell at its realized capped footprint
(948/1024=0.93) so a completing cell is not in the beyond-memory zone; removed
the in-graph callout. Panel (b): removed the r500 pool-geometry annotation (now
text-only + short caption note). Prose 578--580 and caption reworded. Commit
`352977a`.

**Reviewer fixes applied (commit `d24165b`, all data-verified):**
- HIGH 164 scope self-contradiction (operating-window figures use 16/8 only;
  b32 contrast is sec:rb32, not sec:rq1). HIGH 972 base-footprint monster
  sentence split into four.
- Numeric: 1340 CPU 63->62% (throttled cell W_c512_p16k_r200 = 61.8%); 881
  Nexmark cost table -46/-59 -> -50/-63% (C@1024 = q7 B2 -63.0 .. q3 U8 -50.3);
  1145 r150 rho 2.2->1.44 (736/512); 907 spilling near-zero 19/21 -> 22/24
  (rho<1 population); 617 r300 ingest 2.9->2.8M (2.768); 1387->1388 MB x3
  (I8 mean 1387.75, matches fig); 476 'err'->'timeout' (rc124, NOT 'abort' as
  a reviewer guessed); 788 '210 cells'->'210 runs' (210 NX rows/30 cells);
  1381 '20 corrupted cells'->'a dozen' (12 crash cells df16).
- Prose/struct: 'In answer to RQ5:' frame; individual geometry-law-1024/-2048
  refs; drain ceiling unified ~2.6M; 439 comma splice; matched I/O granularity;
  'levers' forward-ref (sec:levers) + de-overloaded at setup L24;
  back-pressure->backpressure; status/rc NNN -> 'exit code NNN'; Table 3 caption
  +-6/10reps -> +-20/8-10; RQ0 how (was whether).

**Verified FALSE POSITIVE, not changed:** reviewer M4 (:736 "610 spilled, 234
restored") — these are q7 C@1024 B2 io_w MEAN (610) and io_r MEAN (234), a
consistent single-cell statement, exactly as concluded in a prior session.

**RESOLVED (commit `e82b6d1`, neutral wording per user):** (1) rb32 drain claim
(Ch5 :1323 + Annex E.7). Replaced "32/32 writes about 11% fewer bytes ...
(p<0.001)" with a plain statement of both statistics: ratio of medians
1.60/0.89/1.00 across 512/1024/2048, but per-pair 32/32 writes more at 1024 MB
(15 of 18 non-tied W_ pairs, Wilcoxon p<0.001), so the p<0.001 is now attached
to the per-pair result it actually describes, not the median ratio. Conclusion
(H3 does not transfer) unchanged; no overstatement.

**Still held (NOT applied):** (2) fig:race caption/text duplication (M10) ---
belongs with task #11 (figure-annotation shortening). (3) db_bench write-buffer/
block-cache sub-rows (:250--265) not reproducible with straightforward holds ---
needs the author's intended aggregation before any change.

All reviewers confirmed CLEAN: all p-values, every completion count, Table 3,
cost figure, race, RQ3 decisive cell, RQ4, rb32 deltas, Nexmark grid; no broken
references; panel letters/markers all match scripts.

---

## Issue #32 — cold-reader comprehension audit: Tier-1+2 fixes T1–T8 (2026-07-06)

Ran three "naive examiner" reviewers over the compiled 112-page PDF **only** (no
data, term maps, figure scripts, or planning docs — exactly what an external
examiner holds), to test whether the document is understandable cold. All three
converged; every concrete flag was re-verified against source before editing.
Consolidated report: `scratchpad/cold-review/CONSOLIDATED.md`. Fixes committed
`d0e6138`:

- **T1 (verified mis-citation).** "517 vs 685 MB / 25%" cited
  `Figure~\ref{fig:ladder}` — the completion-fraction ladder, which shows no
  memory numbers — in `1Introduction:57`, `:78` (C4), and `7Conclusion:16`.
  Repointed all three to `Section~\ref{sec:rq3}` (where the number is derived,
  `5Experimental:1057`). fig:ladder's other refs (:353, :1037, :1157) are correct
  and were kept.
- **T2.** §5.3.1 (`:352–357`) now glosses each of Figure 5's five rows in place
  (flagship / attribution ladder / pooled lever arms / allocation-type /
  intrinsic backpressure), from their own §rq3 definitions, + a sec:rq3 pointer;
  fig:pvalues caption "see the text" → "Section~\ref{sec:rq3}".
- **T3.** `:317` adds the one missing bridge — prose heap/pooled = tables'/appendix
  unbounded/bounded. (The definition at `:314–318` was already clean; not a
  full rename.)
- **T4.** Scoped the 25% (517/685) to "the decisive/parity cell" in intro +
  conclusion; `§RQ2:849` now ties the footprint margins into one range:
  14–22% (identical workloads) / 25% (parity cell) / 33% (2048/r200), not
  competing numbers.
- **T5.** ladder caption (`:1050`) decodes the "8128" x-axis tick in place (just
  under the 8192-byte page, so maps stay on the heap; sec:disambiguation).
- **T6 (verified gap).** Table 4 had no row for exit code 124 though `:477` says
  "1 timeout" and rc124 is in the data; §2.5 (`2Sci_bg:202–206`) listed "time
  limit" as its 4th outcome while Table 4 listed "Crash". Added a Timeout/124 row
  to Table 4 (+caption note), and added the rare crash + a Table-4 forward-ref to
  §2.5 — both sources now list the full five outcomes. Rendered PDF verified
  (Table 4 = 5 rows).
- **T7.** W_/N_/U_/L_ already glossed at first prose use (`:1255–1256`) and fully
  in the Fig 23 caption (`:1265–1268`); residual is only the figure's bare axis
  ticks → task #11 (figure pass). No prose change.
- **T8.** Halved the fig:race caption (`:645–666`): kept the marker/line/race
  decode, dropped the r500/r700 recovery detail that the following prose
  (`:670–709`) already carries in full. The plot itself (6 markers / 3 zones)
  is left for task #11.

Build clean: 112 pp, 0 errors, 0 undefined refs. The two structural changes
(Table 4's Timeout row; the §5.3.1 glosses) were confirmed in the rendered PDF.

**Not applied (Tier 3, T9–T25):** appendix cell-code token legend, heatmap
panel column non-alignment (Figs 6–8), r75/r075 inconsistency, Nexmark/Nautilus
one-line intros, the two "feasibility law" names, `rc` in the abbreviation list,
scattered run-counts, the Acknowledgments placeholder (author TODO), and small
caption keying gaps. All logged in CONSOLIDATED.md.

---

## Issue #33 — in-figure annotations shortened to Hinweise (task #11, 2026-07-06)

Six Ch5 figures carried embedded discussion sentences that duplicated the
caption/body. Reduced each to a short region tag; verified the removed content
is already in prose before trimming. Data and numbers unchanged. Regenerated
**only** these six (not `make_all.sh`, which would re-timestamp all 16 PDFs).
Commit `b198958`.

- **fig_race** — region labels → names only: "throttled", "the race", "state
  fits in memory", "$\rho=1$" (dropped ": spiller keeps up", "ingest outruns
  drain / 23/23 fail", ": above, state must spill"; all in caption/body
  `:670–709`). Pairs with the T8 caption trim.
- **fig_effectiveness** — dropped the "baseline collapses once state clearly
  exceeds memory" italic sentence (shown by marker fills + red band + $\rho=1$);
  suptitle → "Completion at the memory boundary".
- **fig_flagship** — dropped the redundant checksum sub-line from the title
  (in caption/methods note); the 14/24-vs-0/18 finding box kept.
- **fig_gridlock** — pool-line callout → "windowed state on the heap, outside
  the 250 MB pool" (deadlock mechanism in prose `:1084–1103` + caption).
- **fig_composition** — header-byte callout → "$\approx$20% recoverable (unused
  header pointer)" (full detail in prose `:950–951` and caption `:989–991`).
- **fig_nexmark_sweep** — dropped the "out-of-core matters only beyond memory"
  conclusion clause from the suptitle.

Build clean, 112 pp, 0 errors/undefined. While regenerating I reconfirmed the
flagship Fisher value: **two-sided $p = 4.105\times10^{-5}$** (= the stated
4.1e-5); the script's console `3.71e-05` is its one-sided diagnostic, not a
document number. Residual figure-density items still open: fig_race's 6-marker
plot and the W_/N_/U_/L_ axis ticks on fig_rb32_architecture (both plot geometry,
not annotations) — left as-is; would need a genuine redesign, not a trim.

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
