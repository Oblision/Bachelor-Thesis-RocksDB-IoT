#!/usr/bin/env python3
"""Aggregate the pool-size sweep TSVs into a plottable CSV + linear fits + ASCII plots.
Records BOTH the capped@512 sweep (psweep_*) and the uncapped sweep (psweepU_*) for comparison.

TSV cols (1-based): 9=rc 10=status 11=elapsed 22=tps 23=peak_anon 30=pool_alloc_mb 32=pool_used_peak.
Label: psweep[U]_<arm>_p<pool>_r<rep>.
"""
import glob, os, re, statistics as st
import numpy as np

RES = os.path.join(os.path.dirname(__file__), "..", "results")
rows = []
for f in glob.glob(os.path.join(RES, "psweep*_*_p*_r*.tsv")):
    base = os.path.basename(f)[:-4]
    m = re.match(r"(psweepU?)_(\w+?)_p(\d+)_r(\d+)$", base)
    if not m:
        continue
    sweep = "uncapped" if m.group(1) == "psweepU" else "capped512"
    arm, pool = m.group(2), int(m.group(3))
    try:
        c = open(f).read().rstrip("\n").split("\t")
        rc = c[8]; tps = float(c[21]); anon = float(c[22]); pool_used = float(c[31])
    except Exception:
        continue
    rows.append(dict(sweep=sweep, arm=arm, pool=pool, rc=rc, tps=tps, anon=anon, pool_used=pool_used))

keys = sorted({(r["sweep"], r["arm"], r["pool"]) for r in rows})
agg = {}
out = os.path.join(RES, "pool_sweep_points.csv")
with open(out, "w") as o:
    o.write("sweep,arm,pool_buffers,pool_mb,n_total,n_ok,n_oom,n_exhaust,n_crash,"
            "mean_tps_ok,mean_anon_ok,mean_anon_all,mean_pool_used_ok\n")
    for k in keys:
        g = [r for r in rows if (r["sweep"], r["arm"], r["pool"]) == k]
        ok = [r for r in g if r["rc"] == "0"]
        n_oom = sum(r["rc"] == "137" for r in g); n_exh = sum(r["rc"] == "141" for r in g)
        n_crash = sum(r["rc"] in ("134", "11", "139") for r in g)
        mt = round(st.mean([r["tps"] for r in ok])) if ok else ""
        ma_ok = round(st.mean([r["anon"] for r in ok])) if ok else ""
        ma_all = round(st.mean([r["anon"] for r in g])) if g else ""
        mp = round(st.mean([r["pool_used"] for r in ok])) if ok else ""
        pmb = k[2]*8192//1048576
        agg[k] = dict(tps=mt, anon_ok=ma_ok, anon_all=ma_all, pool_used=mp, n_ok=len(ok))
        o.write(f"{k[0]},{k[1]},{k[2]},{pmb},{len(g)},{len(ok)},{n_oom},{n_exh},{n_crash},{mt},{ma_ok},{ma_all},{mp}\n")
print(f"[wrote] {out}\n")

def series(sweep, arm, field, ok_only):
    pts = []
    for k in keys:
        if k[0] != sweep or k[1] != arm:
            continue
        v = agg[k][field]
        if v == "" or (ok_only and agg[k]["n_ok"] == 0):
            continue
        pts.append((k[2]*8192/1048576, float(v)))
    return sorted(pts)

def fit_plot(sweep, arm, field, label, ok_only):
    pts = series(sweep, arm, field, ok_only)
    if len(pts) < 2:
        print(f"  [{sweep}/{arm}/{label}] <2 points"); return
    x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
    a, b = np.polyfit(x, y, 1)
    yh = a*x+b; r2 = 1 - np.sum((y-yh)**2)/np.sum((y-y.mean())**2) if len(y) > 2 and y.std() > 0 else float('nan')
    print(f"== {sweep}/{arm}/{label} vs pool_MB ==  y = {a:.3f}*x + {b:.1f}  (R2={r2:.3f}, n={len(pts)})")
    ymax = y.max() or 1
    for xi, yi in zip(x, y):
        print(f"   pool={xi:5.0f}MB | {'#'*int(40*yi/ymax)} {yi:.0f}")
    print()

for arm in ("inmem", "ooc"):
    print(f"\n########## ARM = {arm} ##########")
    fit_plot("uncapped",  arm, "tps",      "throughput_tps", True)
    fit_plot("uncapped",  arm, "anon_ok",  "anon_MB(uncapped)", True)
    fit_plot("capped512", arm, "anon_all", "anon_MB(capped@512, all runs)", False)
    # feasibility table for capped
    print(f"  capped@512 feasibility ({arm}): pool_MB -> ok/oom/exhaust/crash")
    for k in keys:
        if k[0] == "capped512" and k[1] == arm:
            g = [r for r in rows if (r["sweep"], r["arm"], r["pool"]) == k]
            print(f"    {k[2]*8192//1048576:4d}MB : ok={sum(r['rc']=='0' for r in g)} "
                  f"oom={sum(r['rc']=='137' for r in g)} exh={sum(r['rc']=='141' for r in g)} "
                  f"crash={sum(r['rc'] in ('134','11','139') for r in g)}")
