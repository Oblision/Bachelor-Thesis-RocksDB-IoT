# Push-Based Backpressure for Bounded In-Flight Spill Emit

*Research survey for the BSc thesis "Out-of-Core Stream Processing in NebulaStream".*
*Date: 2026-05-24. Author: research-explorer agent (AI-generated; conclusions to be reviewed).*

## Scope and the concrete problem

NebulaStream (NES) is a **push-based** stream processor: operators push output buffers
downstream; there is no demand/pull (no Volcano `getNext`, no consumer-driven demand). NES
lacks graceful emit backpressure — when the downstream cannot keep up, the buffer pool starves
(query aborts with a buffer-pool-exhaustion error) or the process OOMs, rather than the producer
blocking.

In the out-of-core design, at end-of-stream (terminal flush) we read spilled window/partition
state back from RocksDB and push it downstream to be finalized by an **async** consumer (a JIT
aggregation probe that drains queued buffers later). The terminate loop produces re-materialized
buffers **faster** than the async probe drains them, so the in-flight set piles up to ≈ the whole
retained band → OOM. Partitioning the emit into `P` smaller units shrinks each unit but does **not**
by itself bound the in-flight **count** — so we need a backpressure mechanism that bounds how many
emitted units are in flight at once.

**Out of scope (advisor constraint):** converting the engine to pull / Volcano demand-driven
`getNext`. That is the wrong reference frame for a push system. This survey researches what
*push* engines actually do, and argues that **bounded-in-flight via receiver-granted credits /
tokens / a bounded queue is the de-facto push-side best practice** — and that our
partition-granularity backpressure is a constrained instance of it.

---

## Q1. Survey of push-based backpressure mechanisms across real engines

### Apache Flink — credit-based flow control (the central reference)

**This is the key argument for the thesis, so it is worth getting the framing exactly right.**

Flink's runtime is a **push** dataflow: each task forwards (pushes) result buffers down the
network stack to the next task's input gate; there is no consumer-issued `getNext`. What Flink
added in 1.5 (FLINK-7282) is a **credit** layer *on top of* that push: a sender may only push a
buffer **when it holds credit for the target channel**, and credit is granted by the **receiver**
and represents a **guaranteed free buffer at the receiver**. The canonical design statement is
unambiguous:

> "Senders must never send buffers without credit, that means all the buffers sent must be
> accepted by receivers so **no buffers accumulate on the network wire**."
> — FLINK-7282 design (Apache JIRA). [1]

Mechanism details (Flink network-stack deep dive [2]; FLINK-7282 [1]):

- **Exclusive buffers**: each remote input channel has its own dedicated set of buffers
  (`taskmanager.network.memory.buffers-per-channel`). These are the channel's *initial credit*.
- **Floating buffers**: a shared pool per input gate
  (`taskmanager.network.memory.floating-buffers-per-gate`), handed out on demand based on the
  sender-reported **backlog** so a receiver can absorb bursts.
- **Credit announcement**: "When receivers have buffers available, they notify the sender by
  giving them credits (1 buffer = 1 credit)." Each result subpartition tracks its channel credits;
  "buffers are only forwarded to the lower network stack if credit is available and each sent
  buffer reduces the credit score by one." [2]
- **Backpressure emergence**: "If a receiver cannot keep up, its available credits will
  eventually hit 0 and stop the sender from forwarding buffers to the lower network stack." [2]
  Backpressure then "bubbles upstream and eventually reaches the sources and slows them down." [3]

**Is credit-based flow control "pull"?** No — and the distinction is the load-bearing claim of
this report. It is a **push system with receiver-granted credits**. The data path is still
push (the *sender* decides when to forward a buffer; the receiver never issues a per-record
`getNext`/demand call to fetch). The credit is *not* a request for data; it is a **reservation of
a buffer slot** advertised ahead of time. The sender, holding credit, autonomously pushes — but
only up to the count of slots the receiver has guaranteed. Contrast with a true pull/Volcano
engine where the consumer's `getNext()` call is what *triggers* production at all. Flink's
motivation makes this concrete: the alternative they *replaced* was TCP-based backpressure, where
"all channels multiplexed into the same TCP connection stall together as soon as one channel has
backpressure," and checkpoint barriers could not traverse a backpressured connection [1]. Credits
fix that by per-channel reservation, while keeping the engine push-based. This is exactly the
"credit/token-bounded emit is the elegant push-side primitive" argument the thesis wants.

> Useful caveat for the prose: a credit *is* a small demand signal in the abstract sense (the
> receiver communicates capacity back to the producer). But it is demand for **buffer space**, not
> demand-driven *evaluation*. The engine never inverts control flow; production is still
> producer-initiated. So "push with credits" ≠ "pull." Frame it as: the receiver grants
> permission-to-send (a buffer reservation); the sender remains the active party. (Reactive
> Streams calls the analogous signal `request(n)` — see Q2 — and even there the producer pushes
> onNext, it just respects the outstanding-demand budget.)

### Apache Storm — `topology.max.spout.pending` + watermark backpressure

Storm is push-based (spouts/bolts emit tuples downstream). It offers **two** push-side
backpressure mechanisms:

1. **Ack-based rate limiting via `topology.max.spout.pending`**: caps the number of un-acked
   tuples in flight from a spout. "Once a spout reaches this limit, the spout's `nextTuple()`
   method will not be called until some acks are received for the outstanding emits." [4][5] This
   is precisely a **bounded in-flight token pool**: each emitted tuple-tree consumes a token; an
   ack releases it; the producer is throttled (its emit callback is not invoked) when no tokens
   remain. It bounds the **count** of in-flight units, which is exactly our requirement.
2. **Automatic watermark backpressure (Storm 1.0, STORM-886)**: each task's receive queue has
   configurable high/low watermarks (a percentage of buffer size). "If the high water mark is
   reached, Storm slows down the topology's spouts; throttling stops when the low water mark is
   reached." [6][7] The high/low hysteresis prevents oscillation. This is a **bounded-queue**
   backpressure signal that propagates back to the source — again push, with a count/occupancy
   bound, not pull.

Both keep Storm push-based: nothing in the consumer *requests* tuples; the producer is simply
**gated** (paused) when the in-flight budget is exhausted.

### Apache Heron — Stream-Manager spout backpressure via bounded queues + watermarks

Heron (the Twitter re-architecture of Storm) uses **spout backpressure**: "Stream Managers clamp
down on local spouts to reduce the data injected into the topology." [8][9] Every socket channel
has an application-level buffer "bounded in size by both a high and a low water mark.
Backpressure is triggered when the buffer size reaches the high water mark and remains in effect
until the buffer size goes below the low water mark." The explicit design rationale is to
"prevent a topology from rapidly oscillating between going into and coming out of the backpressure
mitigation mode" and to give "a consistent rate of delivering results and a way to reason about
the system" [9]. Heron is push; the bounded queue + watermark hysteresis is the throttle.

### Spark Structured Streaming — micro-batch admission control (rate, not flow control)

Spark Structured Streaming is **micro-batch**, not a continuous push pipeline, so its
"backpressure" is **admission control on the source**, not per-edge flow control:
`maxOffsetsPerTrigger` (Kafka source) bounds records ingested per micro-batch [10][11].
Databricks documents this explicitly as a memory-safety lever: "Limiting the input rate for
Structured Streaming queries helps maintain a consistent batch size and **prevents large batches
from leading to spill**" [11]. Note the honest limitation: "Structured Streaming cannot do real
backpressure, because Spark cannot tell other applications to slow down the speed of pushing data
into Kafka" [10] — i.e., its mechanism is rate-throttling the *read*, not edge-level credit flow
control. For our purposes Spark is a **weaker** model than Flink/Storm: it bounds input rate to
keep batches (and thus spill) memory-bounded, which is conceptually aligned with bounding
in-flight work but at coarse (whole-batch) granularity.

For spill in hash aggregation / exchange specifically, Spark bounds memory at the **operator**
level (spillable hash map / sort-based aggregate spills partitions to disk when the execution
memory pool is exhausted), and the admission control above keeps batches from being so large that
spill cascades. This is operator-local memory bounding, not an emit-side credit — relevant to Q3
as a contrast.

### Kafka Streams / Beam / Timely-Differential — briefly

- **Kafka Streams**: backpressure is *implicit* via the poll loop. A task only pulls more records
  from the broker when it has made progress; under load you tune `max.poll.records` /
  `max.poll.interval.ms`, or `pause()`/`resume()` partitions so paused partitions issue no fetch
  requests [12]. The broker's durable log is effectively an unbounded external buffer, so the
  "backpressure" is really *consumer self-pacing* against a persistent queue — a different regime
  from in-memory edge flow control. Not a strong reference for our seam.
- **Apache Beam**: backpressure is runner-dependent (Beam is a programming model; on the Flink
  runner you inherit Flink's credit-based flow control; on Dataflow it is the service's autoscaling
  + persistent-state model). Cite only as "delegates to the runner."
- **Timely / Differential Dataflow (Naiad)**: coordination is via **progress tracking /
  frontiers** (logical-timestamp completion), *not* an explicit producer-throttling backpressure
  mechanism. The Naiad paper centers on "knowing when work is complete" via frontiers rather than
  rate-limiting production [13]; it does not describe consumer-initiated buffer flow control.
  Mention as "progress/frontier-based coordination, orthogonal to in-flight memory bounding" — do
  **not** cite it as a backpressure primitive.

**Cross-engine takeaway:** every push engine that *does* bound in-flight memory does so with the
**same primitive in two flavors** — (a) **credit / token budget** (Flink credits; Storm
`max.spout.pending`) or (b) **bounded queue with high/low watermarks** (Storm 1.0, Heron). Both
bound the **count/occupancy** of in-flight units and throttle the *producer* (never inverting
control to pull). That equivalence is the spine of the thesis argument.

---

## Q2. Bounded-buffer / blocking-queue producer-consumer as the canonical push-side primitive

Yes — the **bounded buffer (bounded blocking queue)** is the textbook, de-facto answer for
push-side backpressure, and it is mathematically a **counting semaphore / bounded token pool**.

- The classic producer-consumer / bounded-buffer formulation: a fixed-capacity queue where "when
  the buffer is full, producers must wait for consumers to catch up" [14], implemented with **two
  counting semaphores** — one initialized to the buffer size that blocks producers when full, one
  that blocks consumers when empty [15]. The "empty-slots" semaphore *is* a bounded token pool of
  size N: a producer acquires a slot-token before enqueuing and a consumer releases one after
  dequeuing.
- **Reactive Streams** is the modern, standardized statement of the same idea for asynchronous
  pipelines: it provides "a standard for asynchronous stream processing with **non-blocking
  back pressure**," whose explicit goal is "to govern the exchange of stream data across an
  asynchronous boundary … while ensuring that **the receiving side is not forced to buffer
  arbitrary amounts of data**" [16]. The consumer signals capacity via `request(n)` (a demand
  budget); the producer still *pushes* `onNext` but only up to the outstanding budget. This is the
  same credit idea as Flink, generalized — and again it is **push with a demand budget**, not
  pull-driven evaluation.

So the lineage is: **counting semaphore / bounded buffer (OS textbook)** → **Reactive Streams
`request(n)` demand budget** → **Flink credits / Storm `max.spout.pending` (distributed stream
engines)**. They are the same mechanism (bound the in-flight count; block/gate the producer when
the bound is hit) at different scales. For a single-process push engine like NES, the
**bounded blocking queue / counting semaphore is the direct, idiomatic realization.**

---

## Q3. Spill-to-disk emit specifically: bounding in-flight materialized output

The out-of-core classics already prescribe a **partition-at-a-time, bounded-output discipline**;
the modern engine contribution is to put a **bounded output queue / semaphore-gated emit** on the
re-materialization path.

### Classic external/grace/hybrid hash literature

- **Shapiro (1986), "Join Processing in Database Systems with Large Main Memories"** — defines
  **hybrid hash join**: partition R into buckets sized so that one bucket stays resident as an
  in-memory hash table while the rest spill; then process **one partition pair at a time** on the
  rebuild/probe pass [17][18]. The defining property is that the algorithm is structured so that
  **only a bounded amount of state is in memory at once** (one partition's hash table), and it
  explicitly addresses **partition overflow** (when a partition is too big it is recursively
  re-partitioned) [18]. The same partition-at-a-time discipline applies to **hybrid hash
  aggregation** (group-by): spill groups to partitions, then finalize one partition at a time.
- **Graefe (1993), "Query Evaluation Techniques for Large Databases"** (ACM Computing Surveys) —
  the canonical survey establishing the **duality of sort- and hash-based** set-matching and the
  partitioning discipline for hash-based query processing: "work space used for in-memory sorting
  and for hash tables can be part of the I/O buffer," and partition fan-out is chosen so each
  partition fits the memory budget on the second pass [19][20]. External merge sort is the dual:
  bounded number of in-memory runs merged at a time.

**The load-bearing point for the thesis:** these algorithms bound *resident* state by processing
**one partition (or a bounded fan-in of runs) at a time**. That is a backpressure discipline at
the algorithm level — the producer (the partition-replay loop) advances to the next partition
**only after** the current unit's memory is freed. Our re-materialization-from-RocksDB-then-emit
loop is structurally the **rebuild/probe pass of a spilled aggregation**: read a spilled
partition back, push it to the finalizer, and *do not flood the next partitions in before the
finalizer frees the current ones*. The classics give us the "partition-at-a-time + bounded
in-memory" half; the missing half in a push engine is **bounding the in-flight emitted units
across an *async* finalizer**, which is exactly the credit/token/bounded-queue mechanism from Q1–Q2.

### Modern push-engine treatment

The push-engine realization of "bound the materialized output of a spilled operator" is the same
**bounded output queue / credit-gated emit**: Flink's spilling operators sit inside the same
credit-based network stack, so a spilling source/operator that pushes re-read partitions
downstream is automatically rate-limited by downstream credits — it cannot materialize the whole
spilled relation in flight because credits cap the in-flight buffer count [1][2]. Storm/Heron
achieve the equivalent with `max.spout.pending` / bounded watermark queues [4][8]. The general
pattern: **size a token budget (or queue capacity) to N in-flight partitions/chunks; acquire
before emitting a partition; release when the consumer has consumed/freed that chunk.**

---

## Q4. The minimal, idiomatic mechanism for NES: a counting semaphore sized to N in-flight chunks

This is push-native and **not a hack**: it is **Flink's credit model at coarser (partition)
granularity** — instead of 1 credit = 1 network buffer, we use 1 token = 1 in-flight emitted
partition/chunk. The mechanism:

**Mechanism ("design 2e"):**

1. A **counting semaphore `inflight` initialized to N**, where a permit represents *the right to
   have one emitted partition (re-materialized chunk) outstanding in the async finalizer's queue*.
   (Equivalently: a bounded blocking queue of capacity N between the terminate loop and the
   finalizer — the semaphore is the explicit token-pool view of the same thing; see Q2.)
2. **Terminate / replay loop (producer):**
   - `inflight.acquire()` — blocks if N partitions are already outstanding (this is the
     backpressure; the producer blocks instead of starving the buffer pool / OOMing).
   - read the next spilled partition from RocksDB, re-materialize it into buffer(s).
   - **push** the partition to the async finalizer (enqueue + schedule the probe task).
   - loop.
3. **Async finalizer / JIT probe (consumer):** after it has *fully consumed* a partition's
   buffers and **freed** them back to the buffer pool, it calls `inflight.release()` (one permit
   per completed partition). The release is what lets the blocked producer advance.

**Where acquire/release sit relative to async emit + async drain (critical):**

- `acquire` is **before** producing/emitting the unit (so we never over-produce).
- `release` is **after** the consumer has *freed the buffers of that unit* — **not** when the
  buffers are merely enqueued, and **not** when the probe task is merely scheduled. Releasing too
  early (e.g. on enqueue) defeats the bound, because the producer would refill while the memory is
  still resident. The permit must track *memory liberation*, mirroring Flink where a credit is
  returned only when the receiver's buffer is actually free for reuse [1].

**How to size N:** N is the **memory budget for the emit seam** divided by the **per-partition
peak resident footprint**:

```
N  =  floor( emit_memory_budget / max_partition_footprint )
N  >= 2   // see liveness below
```

- `max_partition_footprint` = re-materialized buffers for one partition *plus* any transient
  finalizer working set for that partition. Use the partitioning fan-out `P` to make each
  partition small enough that `N >= 2` fits the budget (this is *why* partitioning matters — it
  shrinks the unit so a meaningful N fits; partitioning + token bound together give both small
  units *and* a bounded count).
- Larger N → more pipeline overlap (producer and consumer work concurrently) → higher throughput,
  at higher peak memory. Smaller N → tighter memory, less overlap. N is the explicit
  memory/throughput knob — the coarse-grained analogue of Flink's `buffers-per-channel`.

**Deadlock / liveness conditions (see Q5 for the single-process specifics):**

- The consumer **must be able to make progress while the producer is blocked** on `acquire`. If
  the drain runs on the *same* thread that is blocked in `acquire`, it can never call `release` →
  self-deadlock. Hence: **the finalizer must drain on a different thread** than the terminate loop
  (thread count > 1), or the producer must yield/poll cooperatively rather than hard-block.
- The buffer pool must have **headroom > 0** beyond the N in-flight partitions so the consumer can
  always allocate the scratch it needs to *finish* a partition and thus release a permit. If
  freeing a permit itself requires allocating from an exhausted pool, you re-introduce the
  starvation deadlock. Reserve consumer working-set memory *outside* the N-token budget.

---

## Q5. Liveness / correctness pitfalls in a single-process async-drain setting

The danger unique to a single-process engine with an async drain is **producer-blocks-consumer
self-deadlock**:

- **Same-thread drain → deadlock.** If the async "probe" is actually executed inline on the
  terminate thread (e.g. the drain is a callback invoked from the producer's own loop, or the task
  scheduler is single-threaded and the producer thread is blocked in `acquire`), then the thread
  that must call `release` is the very thread parked in `acquire`. No permit is ever returned →
  permanent stall. This is the textbook reason the **thread count must be > 1** (or the producer
  must not hard-block — instead cooperatively yield and re-check). NES's task/worker thread model
  must therefore guarantee that the finalizer's drain is schedulable on a thread distinct from the
  terminate loop.
- **Avoid self-blocking at terminate.** Terminal flush is exactly when you are most tempted to
  "just push everything out" synchronously. If the terminate path both (a) blocks waiting for
  in-flight permits and (b) is the only thread that would drain them, terminate never completes.
  Two safe shapes: (i) terminate loop on worker thread A, finalizer drain on worker thread B,
  permits released by B; or (ii) terminate loop uses a **non-blocking try-acquire + reschedule**
  (cooperative yield) so the same scheduler can interleave drain tasks — never a hard park that
  monopolizes the only worker.
- **Permit accounting must be exact and exception-safe.** Every `acquire` must be matched by
  exactly one `release`, including on the error/abort path — otherwise a leaked permit shrinks the
  effective N until the pipeline wedges (slow leak → eventual deadlock). Use RAII / scope-guard so
  a permit is released even if finalizing a partition throws. (Aligns with the codebase's
  error-handling and immutability rules: the token pool is the single source of truth for
  in-flight count.)
- **Watermark/hysteresis is optional but nice.** Storm/Heron use high/low watermarks to avoid
  rapid on/off oscillation [6][8]. A pure semaphore at partition granularity oscillates far less
  than a per-buffer one (units are coarse), so a plain counting semaphore is usually sufficient;
  mention hysteresis only if profiling shows thrashing.
- **Progress guarantee.** Liveness holds iff: at least one outstanding partition can always be
  *finished* by the consumer using memory **outside** the N-token budget, so a `release` is always
  eventually reachable while the producer is blocked. State this as the invariant.

---

## Q6. Thesis-ready paragraph

> The de-facto best practice for backpressure in **push-based** stream processors is to **bound
> the number of in-flight units** between a producer and a (possibly asynchronous) consumer, and
> to **throttle the producer** when that bound is reached — without ever inverting control flow to
> a pull/demand-driven model. This is realized in production engines as either a **credit / token
> budget** or an equivalent **bounded queue with high/low watermarks**. Apache Flink exemplifies
> the credit form: its runtime still *pushes* result buffers downstream, but a sender "must never
> send buffers without credit," where each credit is a buffer slot the **receiver has guaranteed**
> — so credits bound the in-flight buffer count and propagate backpressure upstream while keeping
> the engine push-based [1][2]. Apache Storm's `topology.max.spout.pending` is the same idea at
> tuple granularity (a bounded pool of un-acked tuples that gates the spout) [4], and Storm 1.0
> and Heron add bounded receive queues with watermark hysteresis [6][8]. The underlying primitive
> is the classic **bounded buffer / counting semaphore** producer-consumer pattern [14][15],
> standardized for asynchronous pipelines by **Reactive Streams** non-blocking backpressure [16].
> For the out-of-core spill-emit path, the external-memory classics already prescribe a
> **partition-at-a-time, bounded-resident discipline** (Shapiro's hybrid hash [17][18]; Graefe's
> survey [19]); we extend that discipline across an *asynchronous* finalizer by adding a
> **counting semaphore sized to N in-flight partitions** — acquire a token before emitting a
> re-materialized partition, release it when the finalizer has consumed and freed that chunk.
> This is **push-native** and not an ad-hoc workaround: it is precisely Flink's credit model at a
> coarser (partition) granularity. The honest caveat is that, unlike Flink or Storm, **NES core
> provides no framework-level backpressure**, so we implement the bound **locally, at the
> spill-emit seam**, as a single-process counting semaphore (token = one outstanding partition),
> with the liveness requirement that the finalizer drains on a separate thread so a blocked
> producer cannot starve its own consumer.

---

## Q7. (Implicit consolidation) Why this is the right answer and what to implement

- **Right answer, principled:** bounded-in-flight is the *only* mechanism every memory-safe push
  engine uses; the two surface forms (credits, bounded watermark queues) are the same counting-
  semaphore primitive. None of them convert to pull. So choosing a token/semaphore bound is
  adopting the established push-side practice, not inventing one.
- **Concrete minimal design (recap of Q4):** one `std::counting_semaphore`-style token pool of
  size `N = floor(emit_budget / max_partition_footprint)`, `N >= 2`; `acquire` before emitting a
  re-materialized partition; `release` (RAII, exception-safe) after the async finalizer has freed
  that partition's buffers; finalizer drains on a thread distinct from the terminate loop; reserve
  consumer working-set memory outside the N budget to guarantee a permit is always eventually
  releasable.

---

## Sources

Primary sources (official docs / specs / peer-reviewed papers) are preferred; blogs are marked
**[blog]**. Note that the Flink network deep-dive [2] is an *official Apache Flink project blog*
(authoritative project documentation, not third-party).

1. **FLINK-7282: "Credit-based Network Flow Control"** — Apache Flink JIRA design issue. Apache
   Software Foundation. <https://issues.apache.org/jira/browse/FLINK-7282>
   (Key line: "Senders must never send buffers without credit … no buffers accumulate on the
   network wire"; motivation = avoid TCP multiplexed connection stalling.)
2. **"A Deep-Dive into Flink's Network Stack"** — Nico Kruber, *Apache Flink project blog*,
   2019-06-05. <https://flink.apache.org/2019/06/05/a-deep-dive-into-flinks-network-stack/>
   (Credits = 1 buffer = 1 credit; exclusive vs floating buffers; "if a receiver cannot keep up,
   its available credits will eventually hit 0 and stop the sender.")
3. **"Flink Network Stack Vol. 2: Monitoring, Metrics, and that Backpressure Thing"** — Nico
   Kruber & Piotr Nowojski, *Apache Flink project blog*, 2019-07-23.
   <https://flink.apache.org/2019/07/23/flink-network-stack-vol.-2-monitoring-metrics-and-that-backpressure-thing/>
   (Backpressure "bubbles upstream and eventually reaches the sources and slows them down.")
4. **Apache Storm — Performance Tuning** (official docs). Apache Software Foundation.
   <https://storm.apache.org/releases/current/Performance.html>
   (`topology.max.spout.pending`: caps un-acked tuples; `nextTuple()` not called until acks
   received.) Mirror: <https://github.com/apache/storm/blob/master/docs/Performance.md>
5. **STORM-1949 / Storm docs** — `max.spout.pending` semantics and interaction with backpressure.
   Apache JIRA. <https://issues.apache.org/jira/browse/STORM-1949>
6. **STORM-886: "Automatic Back Pressure"** — Apache Storm JIRA (watermark-based backpressure,
   high/low water marks). <https://issues.apache.org/jira/browse/STORM-886>
7. **"Apache Storm 1.0.0 released"** — Apache Storm project blog, 2016-04-12 (announces automatic
   high/low watermark backpressure). <https://storm.apache.org/2016/04/12/storm100-released.html>
8. **"Stream Manager" — Apache Heron docs** (official). Apache Software Foundation.
   <https://heron.apache.org/docs/next/cluster-config-stream/>
   (Spout backpressure; per-channel buffer with high/low water marks; oscillation-avoidance
   rationale.)
9. **Kulkarni et al., "Twitter Heron: Stream Processing at Scale"** — SIGMOD 2015 (the Heron
   paper; spout backpressure, bounded buffers, water-mark design rationale).
   Summary: <https://blog.acolyer.org/2015/06/15/twitter-heron-stream-processing-at-scale/> **[blog summary of paper]**
10. **"Structured Streaming Programming Guide"** — Apache Spark official docs.
    <https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html>
    (Source rate limits, e.g. `maxOffsetsPerTrigger`; admission-control model; micro-batch.)
11. **"Configure Structured Streaming batch size"** — Databricks docs.
    <https://docs.databricks.com/aws/en/structured-streaming/batch-size>
    ("Limiting the input rate … prevents large batches from leading to spill.")
12. **Kafka consumer poll loop / `max.poll.records` / pause-resume** — Confluent/Kafka docs and
    discussion. <https://groups.google.com/g/kafka-clients/c/5jagwTywVb8/m/2v7vYg9SBAAJ>
    (Paused partitions issue no fetch requests; consumer self-pacing against the durable log.)
13. **Murray et al., "Naiad: A Timely Dataflow System"** — SOSP 2013.
    <https://sigops.org/s/conferences/sosp/2013/papers/p439-murray.pdf>
    (Progress tracking / frontiers for coordination; *not* an in-flight-memory backpressure
    mechanism — cited as orthogonal.)
14. **"Producer-Consumer: The Bounded Buffer"** — Rafiul Alam.
    <https://alamrafiul.com/blogs/producer-consumer-bounded/> **[blog]**
    (Bounded buffer = backpressure: full buffer → producer waits.)
15. **"ThreadMentor: The Producer/Consumer (Bounded-Buffer) Problem"** — Ching-Kuang Shene,
    Michigan Tech (course material). <https://pages.mtu.edu/~shene/NSF-3/e-Book/MONITOR/ProducerConsumer-1/MON-example-buffer-1.html>
    (Two counting semaphores; empty-slots semaphore initialized to buffer size.)
16. **Reactive Streams specification** — reactive-streams.org (official spec site).
    <https://www.reactive-streams.org/>
    ("Asynchronous stream processing with non-blocking back pressure … the receiving side is not
    forced to buffer arbitrary amounts of data"; `request(n)` demand budget.)
17. **Shapiro, L. D., "Join Processing in Database Systems with Large Main Memories"** — ACM
    Transactions on Database Systems (TODS) 11(3), Sept. 1986.
    <https://dl.acm.org/doi/10.1145/6314.6315>
    (Hybrid hash join; one resident bucket as hash table; partition-at-a-time rebuild/probe;
    partition-overflow handling.) PDF: <https://cs-people.bu.edu/mathan/reading-groups/papers-classics/join.pdf>
18. **Shapiro 1986 (PDF, full text)** — as above (overflow handling / recursive re-partition).
    <https://cs-people.bu.edu/mathan/reading-groups/papers-classics/join.pdf>
19. **Graefe, G., "Query Evaluation Techniques for Large Databases"** — ACM Computing Surveys
    25(2), June 1993. <https://dl.acm.org/doi/10.1145/152610.152611>
    (Sort/hash duality; partitioning fan-out chosen so each partition fits memory on the second
    pass; work space as part of I/O buffer.) PDF: <https://cs.uwaterloo.ca/~david/cs848s13/graefe.pdf>
20. **Graefe 1993 (PDF, full text)** — as above.
    <https://cs.uwaterloo.ca/~david/cs848s13/graefe.pdf>

### Citation-quality note for the thesis

- **Cite as primary in the thesis:** Shapiro 1986 [17], Graefe 1993 [19], Naiad/SOSP 2013 [13],
  Heron/SIGMOD 2015 [9 — cite the paper, not the blog summary], FLINK-7282 [1], the two official
  Flink network-stack blog posts [2][3] (project-authoritative), Storm STORM-886 [6] and official
  Storm/Heron/Spark/Databricks docs [4][8][10][11], Reactive Streams spec [16].
- **Use blogs only as illustration** (bounded-buffer intuition [14], Heron summary [9-blog]); back
  every load-bearing claim with the primary source listed alongside it.
- **For the Heron paper [9]**, retrieve the SIGMOD 2015 PDF for the verbatim watermark/backpressure
  passage before final citation (the blog summary was used here for the design rationale wording).
