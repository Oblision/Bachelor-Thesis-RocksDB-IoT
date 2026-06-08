#!/usr/bin/env python3
"""Combined dataset: UNPOOLED flagship OOC vs BOUNDED (pooled-pages) OOC vs in-memory, same experiment.
Per rho: completion of each variant, Fisher tests (bounded-OOC vs in-mem; bounded vs unpooled OOC),
throughput/p99 overhead, checksum. Writes one combined CSV. Baseline (in-mem, rho) = UNPOOLED arms.
TSV idx: 8=rc 20=p99 21=tps 22=peak_anon. Labels: [b_]{ooc,inm,unc,ooccs,unccs}_<rho>_r<n>.
"""
import glob, os, re, math
import numpy as np
from scipy import stats

D = os.path.join(os.path.dirname(__file__), ".."); RES, LOG = D+"/results", D+"/logs"
RHOS = ["r075", "r100", "r150", "r200", "r300"]

def cells(prefix, arm, rho):
    out = []
    for f in glob.glob(f"{RES}/{prefix}{arm}_{rho}_r*.tsv"):
        try:
            c = open(f).read().rstrip("\n").split("\t"); out.append((c[8], float(c[21]), float(c[20]), float(c[22])))
        except Exception: pass
    return out  # (rc, tps, p99, anon)

def cks(label):
    f = f"{LOG}/{label}.log"
    if not os.path.exists(f): return None
    m = re.search(r"checksum:\s*(\d+)\s+numberOfTuples:\s*(\d+)", open(f, errors="ignore").read())
    return m.groups() if m else None

def ok_vals(cl, i): return [c[i] for c in cl if c[0] == "0"]
def compl(cl): return sum(c[0] == "0" for c in cl), len(cl)

out = open(f"{RES}/combined_dataset.csv", "w")
out.write("rho_label,rho_meas,arm_variant,n_ok,n_total,mean_tps_ok,mean_p99_ok,mean_anon_ok\n")
rows = []
pool_b_ok=pool_b_f=pool_u_ok=pool_u_f=pool_im_ok=pool_im_f=0
print(f"{'rho':>5}{'meas':>6} | {'in-mem':>8} {'OOC-unp':>8} {'OOC-bnd':>8} | {'p:bnd-v-inm':>11} {'p:bnd-v-unp':>11} | "
      f"{'bnd tps ov%':>11} {'bnd p99 ov%':>11} | cks")
for rho in RHOS:
    unc = cells("", "unc", rho); rho_m = (np.mean(ok_vals(unc,3))/512) if ok_vals(unc,3) else float('nan')
    im = cells("", "inm", rho); uo = cells("", "ooc", rho); bo = cells("b_", "ooc", rho)
    bu = cells("b_", "unc", rho)
    for tag, cl in [("inm",im),("ooc_unpooled",uo),("ooc_bounded",bo),("unc",unc),("b_unc",bu)]:
        o,n = compl(cl)
        t = np.mean(ok_vals(cl,1)) if ok_vals(cl,1) else 0
        p = np.mean(ok_vals(cl,2)) if ok_vals(cl,2) else 0
        a = np.mean(ok_vals(cl,3)) if ok_vals(cl,3) else 0
        out.write(f"{rho},{rho_m:.3f},{tag},{o},{n},{t:.0f},{p:.0f},{a:.0f}\n")
    imo,imn=compl(im); uoo,uon=compl(uo); boo,bon=compl(bo)
    p_bi = stats.fisher_exact([[boo,bon-boo],[imo,imn-imo]])[1] if bon and imn else float('nan')
    p_bu = stats.fisher_exact([[boo,bon-boo],[uoo,uon-uoo]])[1] if bon and uon else float('nan')
    bt=ok_vals(bo,1); ut=ok_vals(unc,1); bp=ok_vals(bo,2); up=ok_vals(unc,2)
    tov = 100*(np.mean(bt)-np.mean(ut))/np.mean(ut) if bt and ut else float('nan')
    pov = 100*(np.mean(bp)-np.mean(up))/np.mean(up) if bp and up else float('nan')
    c_o,c_u = cks(f"b_ooccs_{rho}"), cks(f"b_unccs_{rho}")
    ck = "MATCH" if (c_o and c_u and c_o==c_u) else ("mismatch" if (c_o and c_u) else "n/a")
    if rho_m>=1: pool_b_ok+=boo; pool_b_f+=bon-boo; pool_u_ok+=uoo; pool_u_f+=uon-uoo; pool_im_ok+=imo; pool_im_f+=imn-imo
    print(f"{rho:>5}{rho_m:>6.2f} | {imo:>3}/{imn:<3} {uoo:>3}/{uon:<3} {boo:>3}/{bon:<3} | "
          f"{p_bi:>11.2e} {p_bu:>11.2e} | {tov:>10.1f}% {pov:>10.1f}% | {ck}")
out.close(); print(f"\n[wrote] {RES}/combined_dataset.csv")
print("\n=== POOLED rho>=1 ===")
if pool_b_ok+pool_b_f and pool_im_ok+pool_im_f:
    pbi = stats.fisher_exact([[pool_b_ok,pool_b_f],[pool_im_ok,pool_im_f]])[1]
    pbu = stats.fisher_exact([[pool_b_ok,pool_b_f],[pool_u_ok,pool_u_f]])[1] if pool_u_ok+pool_u_f else float('nan')
    print(f"bounded-OOC {pool_b_ok}/{pool_b_ok+pool_b_f} | unpooled-OOC {pool_u_ok}/{pool_u_ok+pool_u_f} | in-mem {pool_im_ok}/{pool_im_ok+pool_im_f}")
    print(f"Fisher bounded-OOC vs in-mem: p={pbi:.3e}   |   bounded vs unpooled OOC: p={pbu:.3e}")
