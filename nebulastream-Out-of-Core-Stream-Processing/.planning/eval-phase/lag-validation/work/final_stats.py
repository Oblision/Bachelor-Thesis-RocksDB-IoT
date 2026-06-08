#!/usr/bin/env python3
"""Statistical analysis of the flagship OOC-vs-in-memory matrix.
Per rho: completion (Fisher's exact, OOC vs in-mem), throughput & p99 overhead (Welch t-test, OOC vs
uncapped baseline) with 95% CI, and checksum correctness (OOC == uncapped). Plus pooled Fisher over rho>=1.
TSV idx: 8=rc 20=p99 21=tps 22=peak_anon. Checksum from logs/<label>.log.
"""
import glob, os, re, math
import numpy as np
from scipy import stats

D = os.path.join(os.path.dirname(__file__), "..")
RES, LOG = os.path.join(D, "results"), os.path.join(D, "logs")
RHOS = ["r075", "r100", "r150", "r200", "r300"]

def cells(prefix, rho):
    out = []
    for f in glob.glob(os.path.join(RES, f"{prefix}_{rho}_r*.tsv")):
        try:
            c = open(f).read().rstrip("\n").split("\t")
            out.append(dict(rc=c[8], tps=float(c[21]), p99=float(c[20]), anon=float(c[22])))
        except Exception: pass
    return out

def checksum(label):
    f = os.path.join(LOG, f"{label}.log")
    if not os.path.exists(f): return None
    m = re.search(r"checksum:\s*(\d+)\s+numberOfTuples:\s*(\d+)", open(f, errors="ignore").read())
    return (m.group(1), m.group(2)) if m else None

def ci95(a, b):  # mean diff (b-a) CI via Welch
    if len(a) < 2 or len(b) < 2: return (float('nan'),)*3
    ma, mb = np.mean(a), np.mean(b); va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    se = math.sqrt(va/len(a) + vb/len(b)); df = (va/len(a)+vb/len(b))**2 / ((va/len(a))**2/(len(a)-1)+(vb/len(b))**2/(len(b)-1))
    t = stats.t.ppf(0.975, df); return (mb-ma, (mb-ma)-t*se, (mb-ma)+t*se)

pool_oc_ok = pool_oc_fail = pool_im_ok = pool_im_fail = 0
print(f"{'rho':>5} {'rho_meas':>8} | {'OOC compl':>10} {'inm compl':>10} {'Fisher p':>10} | "
      f"{'OOC tps':>9} {'base tps':>9} {'tps over%':>9} {'t-p':>8} | {'OOC p99':>8} {'base p99':>8} {'p99 ov%':>8} | checksum")
for rho in RHOS:
    oc, im, un = cells("ooc", rho), cells("inm", rho), cells("unc", rho)
    if not un: continue
    oc_ok = sum(c["rc"] == "0" for c in oc); oc_f = len(oc)-oc_ok
    im_ok = sum(c["rc"] == "0" for c in im); im_f = len(im)-im_ok
    anon_meas = np.mean([c["anon"] for c in un if c["rc"] == "0"]) if any(c["rc"]=="0" for c in un) else float('nan')
    rho_meas = anon_meas/512
    _, fp = stats.fisher_exact([[oc_ok, oc_f], [im_ok, im_f]]) if (oc_ok+oc_f and im_ok+im_f) else (0, float('nan'))
    if rho_meas >= 1.0:
        pool_oc_ok += oc_ok; pool_oc_fail += oc_f; pool_im_ok += im_ok; pool_im_fail += im_f
    oc_tps = [c["tps"] for c in oc if c["rc"]=="0"]; un_tps = [c["tps"] for c in un if c["rc"]=="0"]
    oc_p99 = [c["p99"] for c in oc if c["rc"]=="0"]; un_p99 = [c["p99"] for c in un if c["rc"]=="0"]
    tover = 100*(np.mean(oc_tps)-np.mean(un_tps))/np.mean(un_tps) if oc_tps and un_tps else float('nan')
    tp = stats.ttest_ind(oc_tps, un_tps, equal_var=False).pvalue if len(oc_tps)>1 and len(un_tps)>1 else float('nan')
    pover = 100*(np.mean(oc_p99)-np.mean(un_p99))/np.mean(un_p99) if oc_p99 and un_p99 else float('nan')
    cs_o, cs_u = checksum(f"ooccs_{rho}"), checksum(f"unccs_{rho}")
    cs = "n/a"
    if cs_o and cs_u: cs = "MATCH" if cs_o == cs_u else f"MISMATCH {cs_o} vs {cs_u}"
    elif cs_u and not cs_o: cs = "OOC-missing(OOM?)"
    print(f"{rho:>5} {rho_meas:>8.2f} | {oc_ok}/{len(oc):>1}={'':1} {oc_ok:>3}/{len(oc):<3}  {im_ok:>3}/{len(im):<3}  {fp:>10.2e} | "
          f"{(np.mean(oc_tps) if oc_tps else 0):>9.0f} {(np.mean(un_tps) if un_tps else 0):>9.0f} {tover:>8.1f}% {tp:>8.1e} | "
          f"{(np.mean(oc_p99) if oc_p99 else 0):>8.0f} {(np.mean(un_p99) if un_p99 else 0):>8.0f} {pover:>7.1f}% | {cs}")

print("\n=== POOLED over rho>=1 (the headline) ===")
if pool_oc_ok+pool_oc_fail and pool_im_ok+pool_im_fail:
    _, pf = stats.fisher_exact([[pool_oc_ok, pool_oc_fail], [pool_im_ok, pool_im_fail]])
    print(f"OOC completed {pool_oc_ok}/{pool_oc_ok+pool_oc_fail}; in-mem completed {pool_im_ok}/{pool_im_ok+pool_im_fail}")
    print(f"Fisher's exact (completion, OOC vs in-mem) p = {pf:.3e}")
