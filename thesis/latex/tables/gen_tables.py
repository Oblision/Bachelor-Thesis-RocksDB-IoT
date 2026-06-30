#!/usr/bin/env python3
"""Generate Appendix A per-cell result tables from the lag-validation TSVs.
Aggregates per cell (label minus _rN), grouped by campaign prefix.
Columns: cell, reps, ok, modal rc, median tps (ok), median p99 (ok), median peak anon."""
import os, re, statistics, collections

RES = os.path.join(os.path.dirname(__file__), "../../../nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation/results")
OUT = os.path.join(os.path.dirname(__file__), "appendixA_cells.tex")

cells = collections.defaultdict(list)
for fn in sorted(os.listdir(RES)):
    if not fn.endswith(".tsv"): continue
    # Exclude the 32/32 backend-size contrast (sec:rb32) and its drift control:
    # they reuse the core campaign prefixes but are a different RocksDB config and
    # are tabulated separately in Appendix E.6. Keeping them here would inflate the
    # core 3,136-run / 460-cell count and mix two configurations in one campaign.
    if "_b32_" in fn or re.match(r"D(?:16|32)_", fn): continue
    row = open(os.path.join(RES, fn)).read().split("\t")
    if len(row) < 31: continue
    label = row[0]
    cell = re.sub(r"_r\d+$", "", label)
    cells[cell].append(row)

def med(vals):
    vals = [v for v in vals if v]
    return statistics.median(vals) if vals else None

def fmt_tps(v):  return f"{v/1e6:.2f}" if v else "--"
def fmt_int(v):  return f"{int(v)}" if v else "--"

groups = collections.defaultdict(list)
for cell, rows in sorted(cells.items()):
    prefix = cell.split("_")[0]
    n = len(rows)
    ok = sum(1 for r in rows if r[9] == "ok")
    rcs = collections.Counter(r[8] for r in rows if r[9] != "ok")
    modal_rc = rcs.most_common(1)[0][0] if rcs else "--"
    okrows = [r for r in rows if r[9] == "ok"]
    tps  = med([float(r[21]) for r in okrows])
    p99  = med([float(r[20]) for r in okrows])
    anon = med([float(r[22]) for r in rows if r[22] not in ("", "0")])
    cap  = rows[0][6]
    groups[prefix].append((cell, cap, n, ok, modal_rc, fmt_tps(tps), fmt_int(p99), fmt_int(anon)))

# Configuration provenance and validity per campaign. DEFAULTCFG marks data taken
# under the engine-default 4096-byte buffers, where every hash-map page lives on the
# heap and the buffer pool holds NO window state (Section 5.6 / Section "RQ3") —
# valid as the unbounded/heap architecture, NOT as evidence about pooling.
DEFAULTCFG = ("engine-default configuration (4096-byte buffers): all window state "
              "unpooled, heap/unbounded architecture")
NOTES = {
    "L":   "Primary cap$\\times$pool matrix under the %s; includes the capped in-memory baselines (\\_inm) and uncapped references (\\_unc)." % DEFAULTCFG,
    "b":   "Flagship 512\\,MB three-variant comparison (RQ1 headline), %s." % DEFAULTCFG,
    "bt":  "Bucket-tweak variant of the flagship (negative result: directory sizing unchanged on two-thirds of maps); same configuration as b.",
    "S":   "\\textbf{DEMOTED as lever evidence.} The entire campaign ran the %s, so the pool the levers defended contained no window state; the original lever conclusions from this data were retracted and the campaign is retained as an architecture-ablation arm only. An example of how one wrong binary default can void a whole campaign's intended question." % DEFAULTCFG,
    "lev": "Early lever shakedown under the %s --- same caveat as campaign S." % DEFAULTCFG,
    "V":   "\\textbf{DEMOTED.} Soft threshold derived from the pool (pool/3) understates memory pressure; retained as the threshold-sensitivity arm only.",
    "V2":  "Follow-up to the demoted V arm; threshold-sensitivity probes.",
    "W":   "Bounded final grid: corrected configuration (8192-byte flag engages pooling) with reserve and paced-restore levers.",
    "N":   "Attribution-ladder arms at the decisive cell: B0 pooling without levers, B1 +reserve, B2 +paced restore (corrected 8192-byte flag).",
    "U":   "Matched-granularity heap control (8128-byte buffers: granularity corrected, allocation still heap) --- the disambiguation arm.",
    "I8":  "In-memory baseline at matched $\\sim$8\\,K granularity (cost-table reference).",
    "B3":  "Allocation-backpressure diagnostics; source of the gridlock evidence (Figure on the allocation deadlock).",
    "NX":  "Nexmark-style transfer workload (bounded architecture).",
    "p8k": "Spill-threshold sweep under the %s." % DEFAULTCFG,
    "psweep":  "Pool-size sweep under the %s." % DEFAULTCFG,
    "psweepU": "Pool-size sweep, uncapped variant.",
    "unc": "Uncapped footprint-calibration runs; source of the measured state pressures ($\\rho$).",
    "ASAN":    "AddressSanitizer-instrumented build for the corruption root cause; \\textbf{not performance-comparable} to release-build campaigns.",
    "ASAN2":   "See ASAN.", "ASAN3": "See ASAN.", "ASAN4": "See ASAN.",
    "ASAN5nc": "ASAN build with the slice cache disabled (falsification arm of the twin-defect diagnosis).",
    "F":   "Failure-probe set of the investigation chain (RQ3).",
    "GA":  "Gridlock alternative-explanation probes (pre-registered, refuted).",
    "GAmb": "Gridlock probe with maxbuckets variation.",
    "GC":  "Gridlock probe, control arm.",
    "P":   "Pool-geometry probes under the %s." % DEFAULTCFG,
    "X":   "Exploratory probes.", "XO": "Exploratory probes.", "XP": "Exploratory probes.",
    "inm": "Early in-memory smoke runs.", "ooc": "Early out-of-core smoke runs.",
    "ooccs": "Early checksum-verification runs.",
    "gauge": "Instrumentation shakedown.", "hmg": "HMGAUGE shakedown.",
    "memtrace": "Memory-sampler shakedown.",
    "rel": "Release-build sanity runs.",
}

with open(OUT, "w") as f:
    f.write("% AUTO-GENERATED by tables/gen_tables.py — do not edit by hand.\n")
    f.write("%% Source: lag-validation/results (%d runs, %d cells, %d campaigns)\n" % (sum(len(v) for v in cells.values()), len(cells), len(groups)))
    for prefix in sorted(groups):
        rows = groups[prefix]
        f.write("\n\\subsection*{Campaign %s (%d cells)}\n" % (prefix.replace("_","\\_"), len(rows)))
        note = NOTES.get(prefix)
        if note:
            f.write("\\textit{%s}\\par\\smallskip\n" % note)
        f.write("\\begin{longtable}{|l|r|r|r|r|r|r|r|}\n\\hline\n")
        f.write("\\textbf{Cell} & \\textbf{Cap} & \\textbf{Reps} & \\textbf{OK} & \\textbf{rc} & \\textbf{tps (M)} & \\textbf{p99 ($\\mu$s)} & \\textbf{anon (MB)} \\\\\n\\hline\n\\endhead\n")
        for (cell, cap, n, ok, rc, tps, p99, anon) in rows:
            f.write("%s & %s & %d & %d & %s & %s & %s & %s \\\\\n\\hline\n" %
                    (cell.replace("_","\\_"), cap, n, ok, rc, tps, p99, anon))
        f.write("\\end{longtable}\n")
print("wrote", OUT)
