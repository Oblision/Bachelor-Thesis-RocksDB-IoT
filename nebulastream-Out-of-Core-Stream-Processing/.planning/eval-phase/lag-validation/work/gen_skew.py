#!/usr/bin/env python3
"""Pre-generate a skewed / out-of-order / bursty CSV stream for the NES File source.

Output columns (no header, matches `CREATE LOGICAL SOURCE s(key UINT64, val UINT64, ts UINT64)`):
    key,val,ts

Design (proposal-aligned breadth workload):
  * key      -- Zipfian over [0, KEYS):  a few hot keys, a long cold tail (smart-sensor skew).
  * ts       -- monotone base event-time + per-row positive jitter in [0, LATENESS].
               The jitter makes the stream genuinely OUT-OF-ORDER over a span of LATENESS
               event-units, so ~LATENESS/WINDOW tumbling windows stay concurrently open.
               Set the NES emit_lag (allowed lateness) >= LATENESS so late rows are not dropped.
  * arrival  -- rows are written in base-ts order (= read/arrival order). BURST intervals
               locally compress the event-time step (more rows per window) to model bursty load.
  * val      -- random 0..99 (payload to SUM; irrelevant to state size).

Stats are printed to stderr so the workload can be sized to cross the 512 MB cap before running NES.
"""
import argparse, sys
import numpy as np


def build_zipf_cdf(keys: int, s: float) -> np.ndarray:
    ranks = np.arange(1, keys + 1, dtype=np.float64)
    w = 1.0 / np.power(ranks, s)
    w /= w.sum()
    return np.cumsum(w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, required=True, help="total rows to emit")
    ap.add_argument("--keys", type=int, default=100_000, help="distinct key universe size")
    ap.add_argument("--zipf", type=float, default=1.1, help="Zipf exponent s (0=uniform, ~1.1 classic skew)")
    ap.add_argument("--window", type=int, default=50_000, help="tumbling window size (event-time units, for stats)")
    ap.add_argument("--rows-per-window", type=float, default=20_000, help="avg rows per window (density)")
    ap.add_argument("--lateness", type=int, default=4_000_000, help="max out-of-order jitter D (event-time units)")
    ap.add_argument("--burst-period", type=int, default=0, help="burst region starts every Nth window (0=off)")
    ap.add_argument("--burst-width", type=int, default=1, help="contiguous burst windows per region (make > jitter/window so it survives smearing)")
    ap.add_argument("--burst-amp", type=float, default=4.0, help="density multiplier inside a burst window")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chunk", type=int, default=1_000_000)
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    cdf = build_zipf_cdf(a.keys, a.zipf)
    W = a.window
    R = a.rows_per_window

    # Build the BASE event-time per row by explicit per-window row allocation.
    # Normal window -> R rows; burst window (every burst_period-th) -> R*burst_amp rows
    # packed into the same W span => a genuine density/cardinality spike. Bases are linearly
    # spaced inside each window, so base is globally monotone (arrival order).
    base_chunks = []
    total = 0
    w = 0
    while total < a.rows:
        is_burst = a.burst_period > 0 and (w % a.burst_period < a.burst_width)
        n_w = int(round(R * (a.burst_amp if is_burst else 1.0)))
        n_w = min(n_w, a.rows - total)
        if n_w <= 0:
            break
        start = w * W
        bw = (start + (np.arange(n_w) * (W / n_w))).astype(np.int64)
        base_chunks.append(bw)
        total += n_w
        w += 1
    base_all = np.concatenate(base_chunks)[: a.rows]
    n_base_windows = w

    distinct_seen = np.zeros(a.keys, dtype=bool)
    n_distinct = 0
    max_ts = 0
    written = 0

    with open(a.out, "w", buffering=1 << 20) as f:
        remaining = a.rows
        off = 0
        while remaining > 0:
            n = min(a.chunk, remaining)
            # keys via inverse-CDF sampling (fast, exact Zipf over [0,keys))
            u = rng.random(n)
            keys = np.searchsorted(cdf, u).astype(np.uint64)
            vals = rng.integers(0, 100, size=n, dtype=np.uint64)

            base_int = base_all[off : off + n]
            # out-of-order jitter in [0, lateness] (positive => late tuples fall into later windows)
            jitter = rng.integers(0, a.lateness + 1, size=n, dtype=np.int64) if a.lateness > 0 else np.zeros(n, dtype=np.int64)
            ts = (base_int + jitter).astype(np.uint64)
            max_ts = max(max_ts, int(ts.max()))

            # stats: distinct keys
            uniq = np.unique(keys)
            newmask = ~distinct_seen[uniq]
            n_distinct += int(newmask.sum())
            distinct_seen[uniq] = True

            # write
            block = np.column_stack((keys, vals, ts))
            np.savetxt(f, block, fmt="%d", delimiter=",")
            written += n
            remaining -= n
            off += n

    n_windows = max_ts / a.window if a.window else 0
    nw_concurrent = a.lateness / a.window
    # crude state estimate: distinct keys per window ~ min(rows_per_window, n_distinct), 39 B/key
    keys_per_win = min(a.rows_per_window, n_distinct)
    band_mb = nw_concurrent * keys_per_win * 39 / 1e6
    print(f"[gen] rows={written} file={a.out}", file=sys.stderr)
    print(f"[gen] distinct_keys_seen={n_distinct} / universe={a.keys}  zipf_s={a.zipf}", file=sys.stderr)
    print(f"[gen] max_ts={max_ts}  total_windows~={n_windows:.0f}  window={a.window}", file=sys.stderr)
    print(f"[gen] lateness D={a.lateness}  =>  ~{nw_concurrent:.1f} windows concurrently OPEN (set emit_lag>=D)", file=sys.stderr)
    print(f"[gen] est concurrent band ~= {band_mb:.0f} MB  (keys/win~={keys_per_win:.0f} x {nw_concurrent:.0f} win x 39B)", file=sys.stderr)
    if a.burst_period:
        print(f"[gen] bursts: every {a.burst_period}th window x{a.burst_amp} density", file=sys.stderr)


if __name__ == "__main__":
    main()
