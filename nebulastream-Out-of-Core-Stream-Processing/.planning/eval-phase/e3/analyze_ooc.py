#!/usr/bin/env python3
"""E4 analysis — read the E3 sweep TSV and emit the RQ1/RQ2/RQ3 + §3.3 figures.

Deliberately small + self-contained (the microbench analyze.py targets db_bench's schema; we read our own columns).
All figures use generous margins / rotated labels / spacing so nothing overlaps.

Usage:  python3 analyze_ooc.py [runs.tsv] [outdir]
"""
import sys, os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TSV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "runs.tsv")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "figs")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.autolayout": True, "font.size": 11, "axes.grid": True, "grid.alpha": 0.3})

def num(s):
    return pd.to_numeric(s, errors="coerce")

df = pd.read_csv(TSV, sep="\t")
for c in ["state_tuples","cap_mb","emit_lag_ms","throughput_tps","peak_mb","store_peak_mb","slices_spilled",
          "bytes_restored","spill_dir_mb","write_amp","sst_footprint_b","bytes_written","p50_us","p95_us","p99_us",
          "num_partitions"]:
    if c in df: df[c] = num(df[c])
# state axis in thousands of retained keys (emit_lag ms, step=1)
df["state_k"] = (df["emit_lag_ms"]/1000).round().astype("Int64")
print(f"[analyze] {len(df)} runs, {df['label'].nunique()} cells")

def agg(sub, col):
    """median over reps + coefficient of variation (data-quality)."""
    g = sub.groupby("state_k")[col]
    return g.median(), (g.std()/g.mean()).fillna(0)

# ---- Fig 1: OOM-survival matrix (the §3.3 headline) ------------------------------------------
fig, axes = plt.subplots(1, df["cap_mb"].dropna().nunique(), figsize=(5.5*df["cap_mb"].dropna().nunique(), 4.2), squeeze=False)
caps = sorted(df["cap_mb"].dropna().unique())
variants = ["inmem","ooc_p1","ooc"]
vlabels  = ["in-mem\nbaseline","naive OOC\n(P=1)","partitioned\nOOC (P=8)"]
for ax, cap in zip(axes[0], caps):
    sub = df[(df["cap_mb"]==cap) & (df["variant"].isin(variants))]
    states = sorted(sub["state_k"].dropna().unique())
    M = np.full((len(variants), len(states)), np.nan)
    for i,v in enumerate(variants):
        for j,s in enumerate(states):
            cell = sub[(sub["variant"]==v)&(sub["state_k"]==s)]
            if not cell.empty:
                M[i,j] = (cell["status"]=="ok").mean()   # fraction of reps that survived
    ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(states))); ax.set_xticklabels([f"{s}k" for s in states], rotation=0)
    ax.set_yticks(range(len(variants))); ax.set_yticklabels(vlabels)
    for i in range(len(variants)):
        for j in range(len(states)):
            if np.isnan(M[i,j]): continue
            lbl = "OK" if M[i,j]>=0.999 else ("OOM" if M[i,j]<=0.001 else f"{int(M[i,j]*100)}%")
            ax.text(j, i, lbl, ha="center", va="center", color="black", fontweight="bold")
    ax.set_title(f"cap = {int(cap)} MB"); ax.set_xlabel("retained state (distinct keys)")
fig.suptitle("§3.3 OOM survival — partition-at-spill (P=8) completes where naive OOC (P=1) is OOM-killed",
             y=1.03, fontweight="bold")
fig.savefig(f"{OUT}/fig1_oom_survival.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- Fig 2: residency — P=1 vs P=8 store & process peak vs state (at the 512 cap where both survive) ----
fig, ax = plt.subplots(figsize=(8.0,4.8))
CAP_R = 512 if 512 in caps else caps[-1]
p8 = df[(df["variant"]=="ooc")&(df["cap_mb"]==CAP_R)&(df["status"]=="ok")]
p1 = df[(df["variant"]=="ooc_p1")&(df["cap_mb"]==CAP_R)&(df["status"]=="ok")]
if not p8.empty:
    sp,_ = agg(p8,"store_peak_mb"); pp,_ = agg(p8,"peak_mb")
    ax.plot(sp.index, sp.values, "o-", color="tab:green", label="P=8 store working set")
    ax.plot(pp.index, pp.values, "s-", color="tab:green", alpha=0.5, label="P=8 process RSS")
if not p1.empty:
    sp,_ = agg(p1,"store_peak_mb"); pp,_ = agg(p1,"peak_mb")
    ax.plot(sp.index, sp.values, "o--", color="tab:red", label="P=1 store working set")
    ax.plot(pp.index, pp.values, "s--", color="tab:red", alpha=0.5, label="P=1 process RSS")
ax.axhline(CAP_R, color="black", ls=":", lw=1.2, label=f"memory cap ({CAP_R} MB)")
ax.set_xlabel("retained state (k distinct keys)"); ax.set_ylabel("peak memory (MB)")
ax.set_title(f"Residency @ {CAP_R} MB cap — P=1 process RSS climbs toward the cap; P=8 stays bounded (~1/P)")
ax.legend(fontsize=8, ncol=2); ax.set_ylim(0, CAP_R*1.08)
fig.savefig(f"{OUT}/fig2_residency.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- Fig 3: throughput vs state (OOC capped vs uncapped in-mem reference) ---------------------
fig, ax = plt.subplots(figsize=(7.5,4.6))
for cap in caps:
    sub = df[(df["variant"]=="ooc")&(df["cap_mb"]==cap)&(df["status"]=="ok")]
    if not sub.empty:
        t,cv = agg(sub,"throughput_tps"); ax.plot(t.index, t.values/1e6, "o-", label=f"OOC (cap {int(cap)})")
ref = df[(df["variant"]=="inmem_uncapped")&(df["status"]=="ok")]
if not ref.empty:
    t,_ = agg(ref,"throughput_tps"); ax.plot(t.index, t.values/1e6, "k^--", label="in-mem (uncapped ref)")
ax.set_xlabel("retained state (k distinct keys)"); ax.set_ylabel("throughput (M tuples/s)")
ax.set_title("RQ2 — throughput overhead vs the uncapped in-memory baseline"); ax.legend(fontsize=9)
fig.savefig(f"{OUT}/fig3_throughput.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- Fig 4: tail latency p95/p99 vs state (OOC) ----------------------------------------------
fig, ax = plt.subplots(figsize=(7.5,4.6))
for cap in caps:
    sub = df[(df["variant"]=="ooc")&(df["cap_mb"]==cap)&(df["status"]=="ok")]
    if sub.empty: continue
    p95,_ = agg(sub,"p95_us"); p99,_ = agg(sub,"p99_us")
    ax.plot(p95.index, p95.values, "o-", label=f"p95 (cap {int(cap)})")
    ax.plot(p99.index, p99.values, "s--", label=f"p99 (cap {int(cap)})", alpha=0.7)
ax.set_xlabel("retained state (k distinct keys)"); ax.set_ylabel("per-task latency (µs)")
ax.set_title("RQ2 — per-task tail latency (note: per-task, not per-window)"); ax.legend(fontsize=9)
fig.savefig(f"{OUT}/fig4_latency.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- Fig 5: write-amp + on-disk footprint vs state (OOC) -------------------------------------
fig, ax1 = plt.subplots(figsize=(7.5,4.6))
sub = df[(df["variant"]=="ooc")&(df["status"]=="ok")]
if not sub.empty:
    wa,_ = agg(sub,"write_amp"); ft,_ = agg(sub,"sst_footprint_b")
    ax1.plot(wa.index, wa.values, "o-", color="tab:blue", label="write amplification")
    ax1.set_ylabel("write amplification", color="tab:blue"); ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(ft.index, ft.values/1e6, "s--", color="tab:red", label="SST footprint")
    ax2.set_ylabel("on-disk SST footprint (MB)", color="tab:red"); ax2.tick_params(axis="y", labelcolor="tab:red")
    ax2.grid(False)
ax1.set_xlabel("retained state (k distinct keys)")
ax1.set_title("RQ3 — RocksDB write amplification & storage footprint")
fig.savefig(f"{OUT}/fig5_writeamp_footprint.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- Fig 6: spill/restore volume vs state (OOC) ---------------------------------------------
fig, ax = plt.subplots(figsize=(7.5,4.6))
sub = df[(df["variant"]=="ooc")&(df["status"]=="ok")]
if not sub.empty:
    sd,_ = agg(sub,"spill_dir_mb"); br,_ = agg(sub,"bytes_restored")
    ax.plot(sd.index, sd.values, "o-", label="spilled to disk (MB)")
    ax.plot(br.index, (br.values/1e6), "s--", label="restored on emit (MB)")
ax.set_xlabel("retained state (k distinct keys)"); ax.set_ylabel("MB")
ax.set_title("RQ2/RQ3 — spill & partitioned-restore volume"); ax.legend(fontsize=9)
fig.savefig(f"{OUT}/fig6_spill_restore.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# ---- data-quality: CV report ----------------------------------------------------------------
print("[analyze] coefficient-of-variation per OOC cell (throughput):")
for cap in caps:
    sub = df[(df["variant"]=="ooc")&(df["cap_mb"]==cap)&(df["status"]=="ok")]
    if sub.empty: continue
    _,cv = agg(sub,"throughput_tps")
    for s,v in cv.items(): print(f"   cap={int(cap)} state={s}k  CV={v:.3f}")
print(f"[analyze] figures -> {OUT}")
