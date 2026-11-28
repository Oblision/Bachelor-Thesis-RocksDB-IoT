#!/usr/bin/env python3
"""Drift-control analysis: D16 (fresh 16/8) vs old W_ (06-12 16/8) = DRIFT;
D16 vs D32 (fresh 32/32, same session) = IN-SESSION config effect."""
import glob, os
from math import comb
from statistics import median
IX = {c:i for i,c in enumerate(
    "label tuples window emit_rate emit_lag n_w cap spill rc status elapsed_s peak_mb spill_dir_mb "
    "store_peak_b slices cpu_usec io_r_b io_w_b lat_p50 lat_p95 lat_p99 tps peak_anon steady_anon "
    "flush_anon peak_frac peak_file peak_shmem peak_rssanon pool_mb rdb_rss pool_peak pool_final unpooled".split())}
R="results"
rows={}
for p in glob.glob(os.path.join(R,"*.tsv")):
    f=open(p).read().rstrip("\n").split("\t")
    if len(f)>=34: rows[f[0]]=f
def ok(r): return r[IX["status"]]=="ok"
def g(r,c):
    try: return float(r[IX[c]])
    except: return None
def mcnemar(b,c):
    n=b+c
    if n==0: return 1.0
    k=min(b,c); return min(1.0, 2*sum(comb(n,i) for i in range(k+1))/2**n)

keys=sorted(k[len("D16_"):] for k in rows if k.startswith("D16_"))
def cap(key): return key.split("_")[0][1:]

def block(title, lab_a, lab_b):
    print(f"## {title}   (A={lab_a}  B={lab_b})")
    print(f"  {'cap':>5} {'pairs':>5} {'okA':>8} {'okB':>8} {'A-only':>6} {'B-only':>6} {'McNemar':>8}")
    for cp in ["512","1024"]:
        ks=[k for k in keys if cap(k)==cp and f"{lab_a}_{k}" in rows and f"{lab_b}_{k}" in rows]
        if not ks: continue
        a=sum(1 for k in ks if ok(rows[f"{lab_a}_{k}"]))
        b=sum(1 for k in ks if ok(rows[f"{lab_b}_{k}"]))
        ao=sum(1 for k in ks if ok(rows[f"{lab_a}_{k}"]) and not ok(rows[f"{lab_b}_{k}"]))
        bo=sum(1 for k in ks if ok(rows[f"{lab_b}_{k}"]) and not ok(rows[f"{lab_a}_{k}"]))
        n=len(ks); p=mcnemar(ao,bo)
        st="***" if p<.001 else "**" if p<.01 else "*" if p<.05 else "ns"
        print(f"  {cp:>5} {n:>5} {a:>3}({a/n*100:>3.0f}%) {b:>3}({b/n*100:>3.0f}%) {ao:>6} {bo:>6} {p:>6.3f}{st:>2}")
    print()

print("# DRIFT-CONTROL ANALYSIS\n")
# DRIFT: fresh 16/8 (D16) vs OLD 16/8 baseline (W_, 06-12)
block("DRIFT  (fresh 16/8 vs old-06-12 16/8) — B-only = cells the OLD baseline lost to session state",
      "W", "D16")
# IN-SESSION A/B: fresh 16/8 vs fresh 32/32 (same session+card)
block("IN-SESSION A/B  (16/8 vs 32/32, same session) — B-only = genuine 32/32 wins",
      "D16", "D32")

# footprint / throughput drift magnitude on both-ok D16∩old pairs
print("## Drift magnitude on cells BOTH completed (fresh-16/8 vs old-16/8)")
for metric in ("peak_anon","tps","peak_mb"):
    diffs=[]
    for k in keys:
        a=rows.get(f"W_{k}"); b=rows.get(f"D16_{k}")
        if a and b and ok(a) and ok(b) and g(a,metric) and g(b,metric):
            diffs.append((g(b,metric)-g(a,metric))/g(a,metric)*100)
    if diffs:
        print(f"  {metric:>10}: n={len(diffs)} median Δ={median(diffs):>+6.1f}%  range[{min(diffs):>+.0f}%,{max(diffs):>+.0f}%]")
