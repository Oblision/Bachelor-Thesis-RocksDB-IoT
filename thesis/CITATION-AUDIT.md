# Citation Audit — 2026-07-14

Every `\cite` in the chapter files (35 occurrences, 24 bib entries, all used, no orphans)
checked against what the cited work actually says. Sources verified against full texts,
abstracts, JIRA records, and DBLP.

**Verdict key:**
- **OK** — source directly supports the sentence as written.
- **CAVEAT** — supports the core, but one clause or word overreaches.
- **WEAK** — source is on-topic but does not carry the specific claim.
- **MISMATCH** — source does not support the claim; fix required.

## Summary

| Verdict | Count | Sites |
|---|---|---|
| OK | 19 | see per-chapter detail |
| CAVEAT | 8 | 1.4, 2.15, 2.16, 2.17, 5.1, 6.3, 6.5, 6.9b |
| WEAK | 3 | 1.5, 1.6, 6.4 |
| MISMATCH | 5 | 1.1 (horowitz), 2.10 (marcu), 5.3+5.6 (jin/compression), 6.6 (wei), 6.10 (fragkoulis) |

The five mismatches are the priority. Fixes are one-sentence rewordings or citation swaps;
none changes any result.

---

## Chapter 1 — Introduction

**[1.1] L11 — "cloud alone is not an option … bandwidth and latency issues at scale" `\cite{horowitz2014,zeuch2020}`**
zeuch2020: **OK** — the CIDR paper's motivation is exactly this.
horowitz2014: **MISMATCH** — Horowitz's ISSCC keynote is about chip-level energy (compute vs. memory access). It says nothing about cloud infrastructure, network bandwidth, or latency.
*Fix: drop horowitz2014 here; zeuch2020 alone carries the sentence.*

**[1.2] L12 — NES processes data on edge devices near the source `\cite{zeuch2020,zeuch2022}` — OK.**

**[1.3] L13 — reduces transmitted data while preserving cloud benefits `\cite{zeuch2020,zeuch2022}` — OK.**

**[1.4] L26 — "data movement dominates the cost of edge deployments" `\cite{horowitz2014,zeuch2020}`**
horowitz2014: **CAVEAT** — Horowitz shows data movement dominates *energy* cost at the chip level (a DRAM access costs orders of magnitude more energy than an arithmetic op). "Cost of edge deployments" extrapolates beyond the source.
*Fix: reword to "and moving data costs far more energy than computing on it~\cite{horowitz2014}", keeping zeuch2020 for the edge-deployment half. Or scope to "energy cost".*

**[1.5] L31 — RocksDB "an industry-standard for this role" `\cite{flink2021}`**
**WEAK** — the Flink blog only evidences Flink's own use; it makes no industry-wide claim.
*Fix: either soften ("the storage engine that Apache Flink, the reference system for this role, ships in production") or add a citation that carries "industry-standard": Dong et al., "RocksDB: Evolution of Development Priorities in a Key-Value Store Serving Large-Scale Applications" (ACM TOS 2021 / FAST'21), or the RocksDB "Users and Use Cases" wiki.*

**[1.6] L47 — "Flink with their pull-based model, already use RocksDB as their out-of-core state backend" `\cite{flink2021}`**
RocksDB-backend half: **OK** (blog: bundled in the distribution, supports state beyond configured memory, memory backend "not for production").
"pull-based model" clause: **WEAK** — the blog never discusses Flink's exchange model.
*Fix: add marcu2022 for the pull-based clause, or restructure so flink2021 only backs the backend claim.*

## Chapter 2 — Scientific Background

**[2.1] L20 — NES is a data management system for the IoT `\cite{zeuch2020,zeuch2022}` — OK.**

**[2.2] L25 — combines edge advantages with cloud advantages `\cite{zeuch2020,zeuch2022}` — OK.**

**[2.3] L28 — compiles each query into pipelines `\cite{zeuch2020,zeuch2022}` — OK** (query compilation into pipelines is described in both).

**[2.10] L35 — "No part of the system polls for new data" `\cite{marcu2022}`**
**MISMATCH (scope)** — Marcu & Bouvry analyze the *storage-broker-to-engine ingestion boundary* (KerA/Kafka vs. Flink sources); they define pull as timed RPC polling and push as send-on-arrival, but they never mention NebulaStream and do not describe engine-internal execution. As placed, the cite appears to back a fact about NES internals.
*Fix: cite zeuch2020,zeuch2022 for NES's push-based execution. If the pull/push definition is worth a cite, attach marcu2022 to a definitional sentence ("In pull-based systems, consumers poll for new data~\cite{marcu2022}."), not to the NES fact.*

**[2.8] L76 — window state via stream slicing `\cite{TraubGCBKRM20}` — OK** (Scotty is the origin of general stream slicing; the cite backs the technique).

**[2.13] L127 — RocksDB is a persistent KV store `\cite{borthakur2013}` — OK.**

**[2.13] L130 — design follows the LSM `\cite{oneil1996}` — OK.**

**[2.15] L141 — write amplification definition `\cite{jin2022,wei2025}` — OK** — wei2025 defines it verbatim ("ratio between the bytes written to storage and the bytes ingested to the database"); jin2022 gives the same equation.

**[2.15b] L150 — "These parameters together determine how much memory RocksDB uses and how much it writes" `\cite{jin2022,pamarthi2024,adao2025,wei2025}`**
**CAVEAT** — the *write* half is carried by jin2022 (parameter analysis, WAF/SA effects) and wei2025 (compaction policy affects WA). The *memory* half is carried by none: no cited paper measures memory use as an outcome. adao2025 (Keigo) is not a parameter study at all — it is storage-hierarchy co-design middleware. jin2022 never touches the block cache.
*Fix: cite jin2022,wei2025 for the write half and rocksdb-memory for the memory half; drop adao2025 and pamarthi2024 from this cluster.*

**[2.15b] L152 — "Prior studies show … tuning cannot remove these costs entirely" (same four cites)**
**CAVEAT** — supported for writes: jin2022's optimized configuration still has WAF 7.0 (vs. 9.2 default); wei2025 shows even the enumerated-optimal file-picking policy retains substantial WA. Unsupported for memory (see above), and adao2025 again does not belong.
*Fix: reword to write behavior ("affect its write behavior, and that tuning reduces but does not remove write amplification~\cite{jin2022,wei2025}").*

**[2.16] L161 — "Algorithms whose data exceeds main memory are called external-memory algorithms" `\cite{graefe1993}`**
**CAVEAT** — Graefe's survey covers the concept thoroughly (external sorting/hashing, hot/cold spilling) but the *term* "external-memory algorithms" is canonically Aggarwal & Vitter (CACM 1988) / Vitter's survey (ACM CSUR 2001).
*Fix (optional): add vitter2001 or aggarwal1988 for the naming; keep graefe1993 for the technique.*

## Chapter 4 — Solution

**[4.1] L116 — "Reactive Streams propagate demand signals upstream; Flink uses credit-based flow control; Storm and Heron throttle at the source" `\cite{reactivestreams,flink_credit,storm886,kulkarni2015heron}` — OK, all four.**
Reactive Streams: request(n) demand, verified. FLINK-7282: "Senders must never send buffers without credit", verified. STORM-886: spouts throttle on high/low watermark, verified — spouts are the sources. Heron: backpressure clamps spouts, per the SIGMOD paper. This is the cleanest citation cluster in the thesis: each system matched to its own source.

## Chapter 5 — Evaluation

**[5.1] L31 — "512 MB floor is the low end of the memory budgets reported for constrained IoT and edge-class devices" `\cite{ojo2019,shi2016}`**
ojo2019: **CAVEAT** — 512 MB is indeed the low end of the paper's *high-end* class (BeagleBone Black, Intel Galileo, ARTIK 520, UDOO Neo: all 512 MB; up to 1–2 GB), i.e., the gateways/SBCs that do edge analytics. But the paper's *constrained* devices (low/middle-end) have KB-to-64 MB RAM. "Constrained IoT" is the wrong class label for 512 MB.
shi2016: **MISMATCH for this claim** — the vision paper contains no device RAM figures at all ("memory" does not occur in the text).
*Fix: reword to "the low end of the memory reported for edge-class gateway devices~\cite{ojo2019}" and drop shi2016 here (it remains a fine cite for general edge-computing motivation, e.g., in Chapter 1).*

**[5.2] L222 — Welch's t-test, no equal-variance assumption `\cite{welch1947}` — OK.**
*Consistency note: Fisher's exact test (L221) and McNemar's test (L225) are used but uncited while Welch is cited. Either cite all three (Fisher 1934; McNemar 1947) or none.*

**[5.3] L239 — "Compression reduces the bytes written to disk and increases processor time" `\cite{jin2022}`**
**MISMATCH** — jin2022 shows compression *parameters* strongly affect WA/SA and throughput, but contains no processor-time measurement and never states this trade-off.
*Fix: cite the RocksDB compression documentation/wiki for the CPU-vs-bytes trade-off, or reword to what jin2022 shows: "Compression settings are among the parameters with the largest effect on bytes written~\cite{jin2022}."*

**[5.4] L240 — write buffer semantics `\cite{rocksdb-memory}` — OK** (wiki: memtables are "in-memory write buffers", size via write_buffer_size, larger memtable → less write amplification).

**[5.5] L241 — block cache semantics `\cite{rocksdb-memory}` — OK.**

**[5.6] L296 (Table, compression row) `\cite{jin2022}` — same MISMATCH as [5.3]; fix together.**

**[5.7] L298, L300 (Table, write buffer / block cache rows) `\cite{rocksdb-memory}` — OK.**

## Chapter 6 — Related Work

**[6.1] L11 — Flink ships RocksDB as its production out-of-core state backend `\cite{flink2021}` — OK** (minor nuance: one of two production backends; RocksDB is the out-of-core one, so the sentence is accurate).

**[6.2] L17 — Flink's data exchange "is pull-based at the boundaries that Marcu and Bouvry analyze" `\cite{marcu2022}` — OK** — the paper analyzes exactly Flink's pull-based source/ingestion boundary, and the sentence scopes itself to those boundaries. Careful wording; keep as is.

**[6.3] L23 — Jin et al. "large-scale parameter tuning improves performance but does not remove compaction overhead" `\cite{jin2022}`**
**CAVEAT** — the improvement is the paper's headline (WA −20.65%, SA −54.5%, processing rate +89.7%); "does not remove compaction overhead" is a correct inference from its residual WAF of 7.0 but the paper never states that conclusion.
*Fix: make the number carry it: "…improves performance but leaves write amplification at 7× even after optimization~\cite{jin2022}."*

**[6.4] L24 — Pamarthi `\cite{pamarthi2024}`**
Claim as paraphrase: supported (the paper asserts exactly that tuning improves RocksDB's behavior in stream processing). **The source itself is the problem: WEAK.** IJSRA (correct name: International Journal of *Science and Research* Archive, not "Scientific Research Archive") is not indexed in Scopus/WoS/DOAJ, publishes for a fee within days, and is flagged as likely predatory. The paper is a practitioner overview with an unmethodological benchmark table and no academic affiliation.
*Recommendation: drop the sentence and citation, or replace with a peer-reviewed source on RocksDB state backends in stream processing (e.g., Asyabi et al., EuroSys'22 state-store benchmark, or Kalavri/Liagouris work on state backends). An examiner who checks this venue will hold it against the thesis.*

**[6.5] L25 — Adão et al. "co-design the storage hierarchy below the KV store and reduce, but do not eliminate, sensitivity to memory and I/O pressure" `\cite{adao2025}`**
**CAVEAT** — co-design half is exactly right (Keigo: concurrency-/workload-aware placement across a storage hierarchy, up to 4×/18× throughput). But the paper never discusses *memory* pressure; its subject is heterogeneous storage devices.
*Fix: "Adão et al. co-design the storage hierarchy below the KV store and show that placement across heterogeneous devices, not tuning alone, governs LSM I/O performance~\cite{adao2025}."*

**[6.6] L26 — Wei et al. "analyze how background compaction degrades foreground performance" `\cite{wei2025}`**
**MISMATCH** — the paper benchmarks and optimizes the *write amplification of partial-compaction file-picking policies*. Foreground degradation appears only as motivation, not as the analysis.
*Fix: "Wei et al. analyze the write amplification of RocksDB's compaction policies and show that even the best file-picking policy retains substantial write amplification~\cite{wei2025}." This also strengthens the paragraph's actual point.*

**[6.7] L27 — "Together these studies show that parameters mitigate but do not remove memory and I/O sensitivity" (no cite, summary)**
Overreaches on "memory" for the same reason as [2.15b].
*Fix: "…mitigate but do not remove the write and I/O costs."*

**[6.8] L33 — WindFlow larger-than-memory windowed processing, single-node `\cite{frassinelli2025}` — OK** — confirmed: WindFlow is a scale-up multicore C++ library; the paper's persistent operators keep windowed/keyed state in RocksDB.

**[6.9] L38 — AION keeps window state across memory and storage for late events `\cite{esteves2020}` — OK** (abstract states this problem and solution verbatim).
**[6.9b] L39 — "bounds storage growth with proactive caching and predictive cleanup"**
**CAVEAT** — both mechanisms exist under those names, but only *predictive cleanup* bounds storage; proactive caching anticipates restores (a latency mechanism).
*Fix: "It anticipates restores with proactive caching and bounds storage growth with predictive cleanup."*

**[6.10] L48–49 — Fragkoulis et al. "survey the evaluation practice of stream processing systems … motivates the single-node methodology" `\cite{fragkoulis2024}`**
**MISMATCH** — the survey covers the *evolution of system functionality* (out-of-order handling, state management, fault tolerance, load management, elasticity, reconfiguration). It is not about evaluation or benchmarking practice, and nothing in it motivates a single-node methodology.
*Fix, two options:*
1. *Reword to what it is: "Fragkoulis et al. survey the evolution of stream processing systems, including state management~\cite{fragkoulis2024}." and let the pre-registration/negative-results sentence stand on its own; or*
2. *Keep the methodology sentence and cite a benchmarking-methodology source instead (e.g., a stream-processing benchmarking survey or the state-store benchmark literature).*
*As written, this is the citation most likely to be caught by a reviewer who knows the survey.*

---

## Bibliography fixes (bibliography.bib)

1. **graefe1993** — pages are **73–170**, not 73–169 (DBLP confirmed).
2. **TraubGCBKRM20** — incomplete: add `volume = {46}, number = {1}, pages = {1:1--1:46}` (ACM TODS 46(1), Article 1, 2021); replace `url` with `doi = {10.1145/3433675}`.
3. **pamarthi2024** — journal name is "International Journal of **Science and Research** Archive"; DOI 10.30574/ijsra.2024.12.1.0867. (Moot if the citation is dropped per [6.4].)
4. **ojo2019** — year field (2018) is correct; the key `ojo2019` is cosmetically misleading but harmless. IEEE lists the paper as 2018, vol. 6.
5. **storm886** — the JIRA's actual summary is "Automatic Back Pressure"; move "(high/low watermark)" into the `note`.
6. **marcu2022 / esteves2020** — both cited as arXiv preprints; check once whether peer-reviewed versions exist and prefer those.
7. Unused entries: none. Every bib entry is cited at least once.

## Suggested new bib entries (if fixes above are adopted)

- Dong et al., "RocksDB: Evolution of Development Priorities in a Key-Value Store Serving Large-Scale Applications", ACM Transactions on Storage 17(4), 2021 — for "industry-standard" [1.5].
- Aggarwal & Vitter, "The Input/Output Complexity of Sorting and Related Problems", CACM 31(9), 1988 — or Vitter, "External Memory Algorithms and Data Structures", ACM CSUR 33(2), 2001 — for the term "external-memory algorithms" [2.16].
- Fisher (1935) and McNemar (1947) — only if the statistics citations are made consistent [5.2].
