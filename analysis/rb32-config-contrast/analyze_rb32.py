#!/usr/bin/env python3
"""Paired analysis of the RB32 (32/32) vs baseline (16/8) RocksDB-config contrast.
Pairs each X_b32_... cell with its X_... twin on the full label (== matched cap,pool,rho,rep),
then: completion per cap (Fisher pre-registered + McNemar paired-exact), and H3 drain bytes
on both-completed pairs (median ratio + sign test). Pure stdlib."""
import glob, os, sys
from math import comb
from statistics import median

COLS = ["label","tuples","window","emit_rate","emit_lag","n_w","cap","spill","rc","status",
        "elapsed_s","peak_mb","spill_dir_mb","store_peak_b","slices","cpu_usec","io_r_b","io_w_b",
        "lat_p50_us","lat_p95_us","lat_p99_us","tps","peak_anon_mb","steady_anon_mb","flush_anon_mb",
        "peak_frac_pct","peak_file_mb","peak_shmem_mb","peak_rssanon_mb","pool_mb","rocksdb_rss_mb",
        "pool_used_peak_mb","pool_used_final_mb","unpooled_peak_mb"]
IX = {c:i for i,c in enumerate(COLS)}

def load(d):
    rows = {}
    for p in glob.glob(os.path.join(d, "*.tsv")):
        f = open(p).read().rstrip("\n").split("\t")
        if len(f) < 34: continue
        rows[f[0]] = f
    return rows

def num(row, col):
    try: return float(row[IX[col]])
    except: return None

# ---- stats (pure stdlib) ----
def fisher_two_sided(a,b,c,d):
    n=a+b+c+d; r1=a+b; col1=a+c; col2=b+d
    if n==0 or r1==0 or (a+c)==0 or (b+d)==0 or r1==n: return 1.0
    denom=comb(n,r1)
    def P(k): return comb(col1,k)*comb(col2,r1-k)/denom
    pobs=P(a); kmin=max(0,r1-col2); kmax=min(r1,col1); s=0.0
    for k in range(kmin,kmax+1):
        pk=P(k)
        if pk<=pobs*(1+1e-9): s+=pk
    return min(1.0,s)

def mcnemar_exact(b,c):           # two-sided exact binomial on discordant pairs
    n=b+c
    if n==0: return 1.0
    k=min(b,c); s=sum(comb(n,i) for i in range(k+1))/2**n
    return min(1.0,2*s)

def sign_test(less,total):        # two-sided exact sign test
    if total==0: return 1.0
    k=min(less,total-less); s=sum(comb(total,i) for i in range(k+1))/2**total
    return min(1.0,2*s)

def stars(p): return "***" if p<.001 else "**" if p<.01 else "*" if p<.05 else "ns"

# ---- load + pair ----
RES = sys.argv[1] if len(sys.argv)>1 else "results"
rows = load(RES)
b32 = {k:v for k,v in rows.items() if "_b32_" in k}
pairs = []          # (label16, row16, row32)
unpaired = 0
for lab, r32 in b32.items():
    twin = lab.replace("_b32_", "_", 1)
    if twin in rows: pairs.append((twin, rows[twin], r32))
    else: unpaired += 1

def ok(row): return row[IX["status"]] == "ok"
def campaign(lab): return lab.split("_")[0]
def cap(row): return row[IX["cap"]]

print(f"# RB32 (32/32) vs baseline (16/8) — PAIRED ANALYSIS")
print(f"b32 cells: {len(b32)} | paired with a 16/8 twin: {len(pairs)} | b32 without twin: {unpaired}\n")

def completion_block(title, sel):
    sub = [p for p in pairs if sel(p)]
    if not sub: return
    print(f"## Completion — {title}  (n_pairs={len(sub)})")
    print(f"  {'cap':>6} {'pairs':>6} {'ok32':>9} {'ok16':>9} {'Δ':>7}  {'both':>4} {'32+':>4} {'16+':>4} {'neither':>7}  {'McNemar':>9} {'Fisher':>9}")
    caps = sorted({cap(r16) for _,r16,_ in sub}, key=lambda x:(x.isdigit()==False, int(x) if x.isdigit() else 1e9))
    for cp in caps:
        g=[p for p in sub if cap(p[1])==cp]
        n=len(g)
        a=sum(1 for _,r16,r32 in g if ok(r32) and ok(r16))      # both ok
        b=sum(1 for _,r16,r32 in g if ok(r32) and not ok(r16))  # 32 only
        c=sum(1 for _,r16,r32 in g if not ok(r32) and ok(r16))  # 16 only
        d=n-a-b-c
        ok32=a+b; ok16=a+c
        f32=ok32/n; f16=ok16/n
        pm=mcnemar_exact(b,c); pf=fisher_two_sided(ok32,n-ok32,ok16,n-ok16)
        print(f"  {cp:>6} {n:>6} {ok32:>4}({f32*100:>3.0f}%) {ok16:>4}({f16*100:>3.0f}%) {(f32-f16)*100:>+6.0f}%  {a:>4} {b:>4} {c:>4} {d:>7}  {pm:>7.3f}{stars(pm):>2} {pf:>7.3f}{stars(pf):>2}")
    print()

completion_block("HEADLINE W_ (final_matrix, POOLED maps-in-pool)", lambda p: campaign(p[0])=="W")
completion_block("N_ (lever_matrix_n1, POOLED pooling arms)",        lambda p: campaign(p[0])=="N")
completion_block("L_ (large_matrix, HEAP by design)",                lambda p: campaign(p[0])=="L")
completion_block("U_ (u8_arm, HEAP control 8128)",                   lambda p: campaign(p[0])=="U")
completion_block("ALL campaigns pooled+heap (context only)",         lambda p: True)

# per-campaign one-line summary
print("## Completion by campaign (all caps pooled)")
camps=sorted({campaign(p[0]) for p in pairs})
print(f"  {'camp':>8} {'pairs':>6} {'ok32%':>7} {'ok16%':>7} {'Δ':>6}  {'McNemar':>9}")
for cm in camps:
    g=[p for p in pairs if campaign(p[0])==cm]; n=len(g)
    a=sum(1 for _,r16,r32 in g if ok(r32) and ok(r16))
    b=sum(1 for _,r16,r32 in g if ok(r32) and not ok(r16))
    c=sum(1 for _,r16,r32 in g if not ok(r32) and ok(r16))
    ok32=a+b; ok16=a+c; pm=mcnemar_exact(b,c)
    print(f"  {cm:>8} {n:>6} {ok32/n*100:>6.0f}% {ok16/n*100:>6.0f}% {(ok32-ok16)/n*100:>+5.0f}%  {pm:>7.3f}{stars(pm):>2}")
print()

# ---- H3 drain bytes (both-completed pairs) ----
def drain_block(title, sel, metric):
    sub=[p for p in pairs if sel(p) and ok(p[1]) and ok(p[2])
         and num(p[1],metric) is not None and num(p[2],metric) is not None]
    if not sub: return
    print(f"## H3 {metric} — {title}  (both-ok pairs)")
    print(f"  {'cap':>6} {'pairs':>6} {'med32':>10} {'med16':>10} {'ratio':>7} {'32<16':>7} {'sign p':>8}")
    caps=sorted({cap(p[1]) for p in sub}, key=lambda x:(x.isdigit()==False,int(x) if x.isdigit() else 1e9))
    for cp in caps+["ALL"]:
        g=sub if cp=="ALL" else [p for p in sub if cap(p[1])==cp]
        if not g: continue
        v32=[num(r32,metric) for _,_,r32 in g]; v16=[num(r16,metric) for _,r16,_ in g]
        m32=median(v32); m16=median(v16); ratio=(m32/m16) if m16 else float('nan')
        less=sum(1 for x32,x16 in zip(v32,v16) if x32<x16)
        diff=sum(1 for x32,x16 in zip(v32,v16) if x32!=x16)
        p=sign_test(less,diff)
        print(f"  {cp:>6} {len(g):>6} {m32:>10.1f} {m16:>10.1f} {ratio:>6.2f}x {less:>3}/{diff:<3} {p:>6.3f}{stars(p):>2}")
    print()

drain_block("W_ pooled", lambda p: campaign(p[0])=="W", "io_w_b")
drain_block("W_ pooled", lambda p: campaign(p[0])=="W", "spill_dir_mb")

# ---- secondary: tps, p99 on both-ok W_ pairs ----
for metric in ("tps","lat_p99_us"):
    sub=[p for p in pairs if campaign(p[0])=="W" and ok(p[1]) and ok(p[2])
         and num(p[1],metric) and num(p[2],metric)]
    if not sub: continue
    v32=[num(r32,metric) for _,_,r32 in sub]; v16=[num(r16,metric) for _,r16,_ in sub]
    m32=median(v32); m16=median(v16)
    print(f"## Secondary {metric} (W_ both-ok, n={len(sub)}): med32={m32:.0f} med16={m16:.0f} ratio={m32/m16:.3f}x")
