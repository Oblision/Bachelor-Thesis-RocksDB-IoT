#!/usr/bin/env python3
"""3-way combined dataset: in-memory vs OOC-unpooled vs OOC-bounded(pooled pages) vs OOC-bucket-tweak.
Per rho: completion of each OOC variant, Fisher (each variant vs in-mem; bucket-tweak vs bounded),
throughput/p99 overhead vs uncapped baseline, peak anon. Writes combined3_dataset.csv.
TSV idx: 8=rc 20=p99 21=tps 22=peak_anon. Labels: [b_|bt_]{ooc,inm,unc}_<rho>_r<n>.
"""
import glob, os, re
import numpy as np
from scipy import stats

D = os.path.join(os.path.dirname(__file__), ".."); RES = D+"/results"
RHOS = ["r075","r100","r150","r200","r300"]
VAR = [("","unpooled"), ("b_","bounded"), ("bt_","buckettweak")]

def cells(pfx, arm, rho):
    out=[]
    for f in glob.glob(f"{RES}/{pfx}{arm}_{rho}_r*.tsv"):
        try:
            c=open(f).read().rstrip("\n").split("\t"); out.append((c[8],float(c[21]),float(c[20]),float(c[22])))
        except Exception: pass
    return out
def ok(cl,i): return [c[i] for c in cl if c[0]=="0"]
def compl(cl): return sum(c[0]=="0" for c in cl), len(cl)

o=open(f"{RES}/combined3_dataset.csv","w")
o.write("rho,rho_meas,variant,arm,n_ok,n_total,mean_tps_ok,mean_p99_ok,mean_anon_ok\n")
pool={v:[0,0] for _,v in VAR}; pool_im=[0,0]
hdr = f"{'rho':>5}{'meas':>6} | {'in-mem':>7}"+"".join(f"{n:>11}" for _,n in VAR)+f" | {'p:unp':>8}{'p:bnd':>8}{'p:btw':>8} {'p:btw-bnd':>9}"
print(hdr)
for rho in RHOS:
    unc=cells("","unc",rho); rho_m=(np.mean(ok(unc,3))/512) if ok(unc,3) else float('nan')
    im=cells("","inm",rho); imo,imn=compl(im)
    for pfx,name in VAR+[("","inm"),("","unc"),("b_","unc"),("bt_","unc")]:
        arm = name if name in ("inm","unc") else "ooc"; p = pfx
        cl = im if name=="inm" else (cells(p,"unc",rho) if arm=="unc" else cells(p,"ooc",rho))
        c_ok,c_n=compl(cl)
        o.write(f"{rho},{rho_m:.3f},{name},{arm},{c_ok},{c_n},{np.mean(ok(cl,1)) if ok(cl,1) else 0:.0f},{np.mean(ok(cl,2)) if ok(cl,2) else 0:.0f},{np.mean(ok(cl,3)) if ok(cl,3) else 0:.0f}\n")
    res={};
    for pfx,name in VAR:
        co=cells(pfx,"ooc",rho); res[name]=compl(co)
        if rho_m>=1: pool[name][0]+=res[name][0]; pool[name][1]+=res[name][1]
    if rho_m>=1: pool_im[0]+=imo; pool_im[1]+=imn
    def fp(a,b):
        return stats.fisher_exact([[a[0],a[1]-a[0]],[b[0],b[1]-b[0]]])[1] if a[1] and b[1] else float('nan')
    line=f"{rho:>5}{rho_m:>6.2f} | {imo:>3}/{imn:<3}"
    for _,n in VAR: line+=f"   {res[n][0]:>2}/{res[n][1]:<2}  "
    line+=f" | {fp(res['unpooled'],(imo,imn)):>7.1e}{fp(res['bounded'],(imo,imn)):>8.1e}{fp(res['buckettweak'],(imo,imn)):>8.1e} {fp(res['buckettweak'],res['bounded']):>9.2f}"
    print(line)
o.close(); print(f"\n[wrote] {RES}/combined3_dataset.csv")
print("\n=== POOLED rho>=1 completion ===")
print(f"in-mem {pool_im[0]}/{pool_im[1]}", *(f"| {n} {pool[n][0]}/{pool[n][1]}" for _,n in VAR))
