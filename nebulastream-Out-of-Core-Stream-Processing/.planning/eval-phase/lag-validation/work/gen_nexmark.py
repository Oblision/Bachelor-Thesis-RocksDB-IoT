#!/usr/bin/env python
"""Nexmark-style bid stream: in-order event time (1 ms/row), hot-skewed auction popularity.
auctionId = 70% Zipf(1.1) over the hot head + 30% uniform long tail (canonical auction popularity).
Columns: ts,auctionId,bidder,datetime,price."""
import sys, numpy as np
rows, hot_head, total_auctions, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
rng = np.random.default_rng(7)
CH = 2_000_000
with open(out, 'w') as f:
    for start in range(0, rows, CH):
        n = min(CH, rows - start)
        ts = np.arange(start, start + n, dtype=np.uint64)
        hot = rng.zipf(1.1, size=n) % hot_head
        tail = rng.integers(0, total_auctions, size=n)
        pick = rng.random(n) < 0.7
        auction = np.where(pick, hot, tail).astype(np.uint64)
        bidder = rng.integers(0, 100_000, size=n)
        price = rng.integers(1, 10_000, size=n)
        np.savetxt(f, np.column_stack([ts, auction, bidder, ts, price]), fmt='%d', delimiter=',')
