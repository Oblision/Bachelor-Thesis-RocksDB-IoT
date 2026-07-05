#!/usr/bin/env python3
"""Build the RB32 (32/32 vs 16/8) thesis figures. Pure-stdlib TSV/trace parsing + matplotlib
(no pandas dependency, robust across versions). Run with the figs venv:
  ~/.venv-thesis-figs/bin/python fig_rb32.py
PDFs land in thesis/latex/img/fig_rb32_*.pdf."""
import glob, os, sys
from statistics import median, mean
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
LAGVAL = os.environ.get("LAGVAL_DIR",
    os.path.join(HERE, "../../../nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation"))
RES = os.path.join(LAGVAL, "results"); TRC = os.path.join(LAGVAL, "traces")
IMG = os.path.join(HERE, "../../latex/img"); os.makedirs(IMG, exist_ok=True)

COLS = ("label tuples window emit_rate emit_lag n_w cap spill rc status elapsed_s peak_mb spill_dir_mb "
        "store_peak_b slices cpu_usec io_r_b io_w_b lat_p50 lat_p95 lat_p99 tps peak_anon steady_anon "
        "flush_anon peak_frac peak_file peak_shmem peak_rssanon pool_mb rdb_rss pool_peak pool_final unpooled").split()
IX = {c:i for i,c in enumerate(COLS)}
def load():
    R={}
    for p in glob.glob(os.path.join(RES,"*.tsv")):
        f=open(p).read().rstrip("\n").split("\t")
        if len(f)>=34: R[f[0]]=f
    return R
ROWS=load()
def ok(r): return r[IX["status"]]=="ok"
def num(r,c):
    try: return float(r[IX[c]])
    except: return None
def cap(r): return r[IX["cap"]]
C16, C32 = "#4C72B0", "#C44E52"   # 16/8 blue, 32/32 red
plt.rcParams.update({"font.size":9,"axes.grid":True,"grid.alpha":.3,"figure.dpi":120})

def save(fig,name):
    fig.tight_layout(); p=os.path.join(IMG,name); fig.savefig(p); plt.close(fig); print("  wrote",os.path.relpath(p,HERE))

# ---- Fig 1: in-session (drift-free) 512 completion, D16 vs D32 ----
def fig_insession():
    keys=sorted(k[4:] for k in ROWS if k.startswith("D16_") and cap(ROWS[k])=="512")
    keys=[k for k in keys if f"D32_{k}" in ROWS]
    if not keys: print("  [skip insession] no D16/D32 512 cells"); return
    n=len(keys); o16=sum(ok(ROWS[f"D16_{k}"]) for k in keys); o32=sum(ok(ROWS[f"D32_{k}"]) for k in keys)
    fig,ax=plt.subplots(figsize=(3.2,3))
    ax.bar([0,1],[o16/n*100,o32/n*100],color=[C16,C32],width=.6)
    for x,v in [(0,o16),(1,o32)]: ax.text(x,v/n*100+1,f"{v}/{n}\n{v/n*100:.0f}%",ha="center",va="bottom",fontsize=8)
    ax.set_xticks([0,1]); ax.set_xticklabels(["16/8","32/32"]); ax.set_ylabel("completed (%)")
    ax.set_title(f"In-session 512 A/B (n={n}, drift-free)"); ax.set_ylim(0,max(60,o32/n*100+12))
    save(fig,"fig_rb32_insession512.pdf")

# ---- Fig 2: architecture sign-flip at 512 (delta completion, pooled vs heap) ----
def fig_architecture():
    arms=[("W","pooled"),("N","pooled"),("U","heap"),("L","heap")]
    labs,deltas,cols=[],[],[]
    for camp,kind in arms:
        ks=[k[len(f"{camp}_b32_"):] for k in ROWS if k.startswith(f"{camp}_b32_") and cap(ROWS[k])=="512"]
        ks=[k for k in ks if f"{camp}_{k}" in ROWS]
        if not ks: continue
        n=len(ks); o32=sum(ok(ROWS[f"{camp}_b32_{k}"]) for k in ks); o16=sum(ok(ROWS[f"{camp}_{k}"]) for k in ks)
        labs.append(f"{camp}_\n({kind})"); deltas.append((o32-o16)/n*100)
        cols.append("#55A868" if kind=="pooled" else "#8172B3")
    if not labs: print("  [skip architecture] no 512 pairs"); return
    fig,ax=plt.subplots(figsize=(4,3))
    ax.bar(range(len(labs)),deltas,color=cols,width=.65)
    ax.axhline(0,color="k",lw=.8); ax.set_xticks(range(len(labs))); ax.set_xticklabels(labs,fontsize=8)
    ax.set_ylabel("Δ completion 32/32 − 16/8 (pp)"); ax.set_title("Architecture-gated effect @ cap 512")
    for i,d in enumerate(deltas): ax.text(i,d+(2 if d>=0 else -2),f"{d:+.0f}",ha="center",va="bottom" if d>=0 else "top",fontsize=8)
    # Extra headroom above/below the tallest bars so the value labels clear the
    # axes frame instead of touching or crossing it.
    ymax=max(deltas+[0]); ymin=min(deltas+[0])
    pad=max(6,0.18*(ymax-ymin))
    ax.set_ylim(ymin-pad,ymax+pad)
    save(fig,"fig_rb32_architecture.pdf")

# ---- Fig 3: memory compartment decomposition (W_ b32), per cap, ok vs failed ----
def fig_compartments():
    caps=["512","1024","2048"]; groups=[]
    for cp in caps:
        for st,lab in [(True,"ok"),(False,"fail")]:
            cells=[r for k,r in ROWS.items() if k.startswith("W_b32_") and cap(r)==cp and ok(r)==st]
            if not cells: continue
            def md(c):
                vs=[num(r,c) for r in cells if num(r,c) is not None]; return median(vs) if vs else 0
            pool=md("pool_mb"); unp=md("unpooled"); rdb=md("rdb_rss"); anon=md("peak_anon")
            base=max(0,anon-pool-unp-rdb); fil=md("peak_file")
            groups.append((f"{cp}\n{lab}\n(n={len(cells)})",pool,unp,rdb,base,fil,int(cp)))
    if not groups: print("  [skip compartments] no W_ b32 cells"); return
    fig,ax=plt.subplots(figsize=(6.4,3.4)); x=range(len(groups));
    pool=[g[1] for g in groups]; unp=[g[2] for g in groups]; rdb=[g[3] for g in groups]; base=[g[4] for g in groups]; fil=[g[5] for g in groups]
    b=[0]*len(groups)
    for vals,lab,col in [(pool,"pool floor (pre-allocated global buffer pool)","#4C72B0"),
                         (unp,"unpooled directories (bucket dirs + map arena, on heap)","#DD8452"),
                         (rdb,"RocksDB (backend resident set)","#C44E52"),
                         (base,"base (residual: engine/runtime + working set)","#937860")]:
        ax.bar(x,vals,bottom=b,label=lab,color=col,width=.7); b=[bi+vi for bi,vi in zip(b,vals)]
    ax.plot(x,[g[6] for g in groups],"k_",ms=18,mew=2,label="cap")
    ax.set_xticks(list(x)); ax.set_xticklabels([g[0] for g in groups],fontsize=7); ax.set_ylabel("resident anon (MB, median)")
    ax.set_title("Memory compartments — where the budget goes"); ax.legend(fontsize=7,loc="upper left")
    save(fig,"fig_rb32_compartments.pdf")

# ---- Fig 4: overhead-over-time traces (anon vs cap), 16/8 vs 32/32, a 512 cell ----
def read_trace(lbl):
    p=os.path.join(TRC,f"{lbl}.trace")
    if not os.path.exists(p): return None
    t,anon=[],[]
    for ln in open(p):
        f=ln.split()
        if len(f)>=3:
            try: t.append(float(f[0])); anon.append(float(f[2]))
            except: pass
    if not t: return None
    t0=t[0]; return [x-t0 for x in t], anon
def fig_traces():
    # find a 512 cell key present for both D16 and D32 with a trace
    fig,ax=plt.subplots(figsize=(4.6,3))
    plotted=0
    for k in sorted(x[4:] for x in ROWS if x.startswith("D16_") and cap(ROWS[x])=="512"):
        a=read_trace(f"D16_{k}"); b=read_trace(f"D32_{k}")
        if a and b:
            ax.plot(a[0],a[1],color=C16,lw=1.4,label=f"16/8 ({ROWS['D16_'+k][IX['status']]})")
            ax.plot(b[0],b[1],color=C32,lw=1.4,label=f"32/32 ({ROWS['D32_'+k][IX['status']]})")
            ax.set_title(f"Anon memory over time — {k}"); plotted=1; break
    if not plotted: print("  [skip traces] no paired D16/D32 512 trace"); plt.close(fig); return
    ax.axhline(512,color="k",ls="--",lw=.8,label="cap 512"); ax.set_xlabel("time (s)"); ax.set_ylabel("anon (MB)")
    ax.legend(fontsize=7); save(fig,"fig_rb32_trace512.pdf")

# ---- Fig 5: drain bytes H3 (io_w_b, both-ok W_ pairs) by cap ----
def fig_drain():
    caps=["512","1024","2048"]; m16,m32=[],[]
    for cp in caps:
        ks=[k[len("W_b32_"):] for k in ROWS if k.startswith("W_b32_") and cap(ROWS[k])==cp]
        pairs=[(ROWS[f"W_{k}"],ROWS[f"W_b32_{k}"]) for k in ks if f"W_{k}" in ROWS]
        pairs=[(a,b) for a,b in pairs if ok(a) and ok(b) and num(a,"io_w_b") and num(b,"io_w_b")]
        m16.append(median([num(a,"io_w_b")/1e6 for a,b in pairs]) if pairs else 0)
        m32.append(median([num(b,"io_w_b")/1e6 for a,b in pairs]) if pairs else 0)
    fig,ax=plt.subplots(figsize=(4,3)); x=range(len(caps)); w=.38
    ax.bar([i-w/2 for i in x],m16,w,color=C16,label="16/8")
    ax.bar([i+w/2 for i in x],m32,w,color=C32,label="32/32")
    ax.set_xticks(list(x)); ax.set_xticklabels(caps); ax.set_xlabel("cap (MB)"); ax.set_ylabel("median bytes→SD (MB)")
    ax.set_title("Drain bytes (H3, both-completed W_)"); ax.legend(fontsize=8)
    save(fig,"fig_rb32_drain.pdf")

if __name__=="__main__":
    print(f"building RB32 figures -> {os.path.relpath(IMG,HERE)}")
    for fn in (fig_insession,fig_architecture,fig_compartments,fig_traces,fig_drain):
        try: fn()
        except Exception as e: print(f"  [error] {fn.__name__}: {e}")
    print("done")
