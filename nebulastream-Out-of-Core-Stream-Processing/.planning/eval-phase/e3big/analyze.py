#!/usr/bin/env python3
"""Extended analyzer for the scaled sweep (e3big schema). Produces per-RQ + per-hypothesis figures and an
H1-H5 verdict table. Reads columns by name, so it is robust to column additions.

Usage: python3 analyze.py runs.tsv figs
"""
import sys, os
import pandas as pd, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

TSV = sys.argv[1] if len(sys.argv) > 1 else "runs.tsv"
OUT = sys.argv[2] if len(sys.argv) > 2 else "figs"
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.autolayout": True, "font.size": 11, "axes.grid": True, "grid.alpha": .3})

df = pd.read_csv(TSV, sep="\t")
NUM = ["state_tuples","cap_mb","emit_lag_ms","wbuf_mb","cache_mb","num_partitions","throughput_tps","peak_mb",
       "store_peak_mb","slices_spilled","bytes_restored","spill_dir_mb","write_amp","sst_footprint_b",
       "write_stall_micros","elapsed_s","p50_us","p95_us","p99_us","spill_p50_us","spill_p99_us"]
for c in NUM:
    if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
df["state_k"] = (df["emit_lag_ms"]/1000).round().astype("Int64")
df["ok"] = df["status"].eq("ok")
ooc = df[df["variant"].str.startswith("ooc")]      # ooc + ooc_skew
print(f"[analyze] {len(df)} runs; {df['status'].value_counts().to_dict()}")

def med(sub,col,by): g=sub.groupby(by)[col]; return g.median(), (g.std()/g.mean()).fillna(0)
def savefig(fig,name): fig.savefig(f"{OUT}/{name}",dpi=130,bbox_inches="tight"); plt.close(fig)

# ---- Fig: survival by P x state (per cap) incl in-mem -------------------------------------------------
caps=sorted(df["cap_mb"].dropna().unique())
fig,axes=plt.subplots(1,len(caps),figsize=(6*len(caps),4.6),squeeze=False)
for ax,cap in zip(axes[0],caps):
    sub=df[(df["cap_mb"]==cap)]
    rows=[("in-mem",sub[sub["variant"]=="inmem"])]+[(f"P={int(p)}",sub[(sub["variant"]=="ooc")&(sub["num_partitions"]==p)]) for p in [1,2,4,8,16]]
    states=sorted(sub["state_k"].dropna().unique())
    M=np.full((len(rows),len(states)),np.nan)
    for i,(_,r) in enumerate(rows):
        for j,s in enumerate(states):
            c=r[r["state_k"]==s]
            if len(c): M[i,j]=c["ok"].mean()
    ax.imshow(M,cmap="RdYlGn",vmin=0,vmax=1,aspect="auto")
    ax.set_xticks(range(len(states))); ax.set_xticklabels([f"{s}k" for s in states],rotation=45,ha="right")
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([n for n,_ in rows])
    for i in range(len(rows)):
        for j in range(len(states)):
            if not np.isnan(M[i,j]): ax.text(j,i,"OK" if M[i,j]>=.999 else("OOM" if M[i,j]<=.001 else f"{int(M[i,j]*100)}"),ha="center",va="center",fontsize=8,fontweight="bold")
    ax.set_title(f"cap {int(cap)} MB"); ax.set_xlabel("retained state")
fig.suptitle("OOM survival by partition count P (and in-mem)",y=1.02,fontweight="bold"); savefig(fig,"fig_survival_P.png")

# ---- Fig: P-knee — store_peak & throughput vs P (largest surviving state, 512) ------------------------
p=ooc[(ooc["variant"]=="ooc")&(ooc["cap_mb"]==512)&(ooc["ok"])]
if len(p):
    st=p["state_k"].max(); pk=p[p["state_k"]==st]
    sp,_=med(pk,"store_peak_mb","num_partitions"); th,_=med(pk,"throughput_tps","num_partitions")
    fig,ax1=plt.subplots(figsize=(7.5,4.6))
    ax1.plot(sp.index,sp.values/1e6,"o-",color="tab:green",label="store working set (MB)")
    ax1.set_xlabel("partitions P"); ax1.set_ylabel("store working set (MB)",color="tab:green"); ax1.set_xscale("log",base=2)
    ax2=ax1.twinx(); ax2.plot(th.index,th.values/1e6,"s--",color="tab:blue",label="throughput (M t/s)")
    ax2.set_ylabel("throughput (M tuples/s)",color="tab:blue"); ax2.grid(False)
    ax1.set_title(f"P-knee @512MB, state={int(st)}k — residency falls ~1/P; throughput cost of more partitions")
    savefig(fig,"fig_P_knee.png")

# ---- Fig: compression (H3) ---------------------------------------------------------------------------
b=ooc[(ooc["variant"]=="ooc")&(ooc["compression"].isin(["none","lz4","zstd"]))&(ooc["ok"])]
if len(b):
    fig,ax=plt.subplots(1,3,figsize=(15,4.2))
    for a,(col,lab,scale) in zip(ax,[("throughput_tps","throughput (M t/s)",1e6),("write_amp","write amp",1),("sst_footprint_b","SST footprint (MB)",1e6)]):
        m=b.groupby("compression")[col].median()
        a.bar(m.index,m.values/scale,color=["#b3263b","#2f6f8f","#5a4a8a"]); a.set_title(lab); a.set_xlabel("compression")
    fig.suptitle("H3 — compression: throughput / write-amp / footprint",y=1.03,fontweight="bold"); savefig(fig,"fig_H3_compression.png")

# ---- Fig: wbuf (H1) ----------------------------------------------------------------------------------
c=ooc[(ooc["variant"]=="ooc")&(ooc["ok"])]
if c["wbuf_mb"].nunique()>1:
    fig,ax=plt.subplots(figsize=(7.5,4.6))
    for st in sorted(c["state_k"].dropna().unique()):
        s=c[c["state_k"]==st]; t,_=med(s,"throughput_tps","wbuf_mb")
        ax.plot(t.index,t.values/1e6,"o-",label=f"{st}k")
    ax.set_xlabel("write_buffer (MB)"); ax.set_ylabel("throughput (M t/s)"); ax.legend(title="state",fontsize=8)
    ax.set_title("H1 — throughput vs write buffer size"); savefig(fig,"fig_H1_wbuf.png")

# ---- Fig: stall fraction (H2) ------------------------------------------------------------------------
c=ooc[ooc["ok"]].copy(); c["stall_frac"]=c["write_stall_micros"]/(c["elapsed_s"]*1e6)
if c["stall_frac"].notna().any():
    fig,ax=plt.subplots(figsize=(7.5,4.6))
    for wb in sorted(c["wbuf_mb"].dropna().unique()):
        s=c[c["wbuf_mb"]==wb]; m,_=med(s,"stall_frac","state_k")
        ax.plot(m.index,m.values*100,"o-",label=f"wbuf {int(wb)}")
    ax.axhline(5,color="red",ls=":",label="H2 threshold 5%")
    ax.set_xlabel("retained state (k)"); ax.set_ylabel("write-stall % of elapsed"); ax.legend(fontsize=8)
    ax.set_title("H2 — write-stall fraction vs state"); savefig(fig,"fig_H2_stall.png")

# ---- H1-H5 verdict table (printed + saved) -----------------------------------------------------------
lines=["# H1-H5 verdicts (from data)\n"]
# H1
base=ooc[(ooc["variant"]=="ooc")&(ooc["wbuf_mb"]==64)&(ooc["cache_mb"]==32)&(ooc["compression"]=="lz4")&(ooc["ok"])]
h1=base["throughput_tps"].median() if len(base) else float("nan")
lines.append(f"H1 throughput @lz4/wbuf64/cache32: median {h1:,.0f} tuples/s (end-to-end). Predicted >=30k db_bench ops/s — DIFFERENT metric; report integrated overhead vs in-mem.")
# H2
if c["stall_frac"].notna().any():
    h2=c[c["wbuf_mb"]>=64]["stall_frac"].median()*100
    lines.append(f"H2 stall fraction @wbuf>=64: median {h2:.2f}% (threshold 5%) -> {'CONFIRMED' if h2<5 else 'REFUTED'}")
# H3
if len(b):
    g=b.groupby("compression")["throughput_tps"].median()
    if "none" in g and "lz4" in g: lines.append(f"H3 compression: lz4 vs none throughput {g.get('lz4',float('nan')):,.0f} vs {g.get('none',float('nan')):,.0f} t/s; footprint differs -> assess mandatory-ness")
# H4
lines.append("H4 watermark: see fig (Sweep F). Note: architecturally the terminal-flush burst (not the watermark) was the binding constraint pre-partitioning; report in that context.")
# H5
sp=ooc[ooc["ok"]];
if "spill_p99_us" in sp and sp["spill_p99_us"].notna().any():
    lines.append(f"H5 spill latency: per-spill p50/p99 us median {sp['spill_p50_us'].median():.0f}/{sp['spill_p99_us'].median():.0f}; spill_dir median {sp['spill_dir_mb'].median():.0f} MB. Estimate s-per-100MB and compare to the ~2s prediction.")
open(f"{OUT}/H1-H5-verdicts.md","w").write("\n".join(lines))
print("\n".join(lines)); print(f"[analyze] figures + verdicts -> {OUT}")
