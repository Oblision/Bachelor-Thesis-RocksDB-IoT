---
title: "HashMap per-entry overhead: a virtual destructor adds an 8-byte vtable pointer to every aggregation key"
date: 2026-06-08
tags: [thesis, nebulastream, nautilus, hashmap, memory, footprint, RQ5, finding, deferred]
status: measured; NOT implemented (Nautilus data-structure change is out of the chosen scope)
---

# Finding: the ChainedHashMap entry carries a vtable pointer it never uses

## One-line
Every aggregation entry stores an **8-byte vtable pointer** purely because `AbstractHashMapEntry`
declares a `virtual` destructor. The entries are never deleted polymorphically, so the vptr is dead
weight: it makes the per-entry header **24 B instead of 16 B**, and the header is the **single largest
component of HashMap memory (41%)**. Removing the `virtual` would shrink each entry 40 B → 32 B
(**~20 % smaller HashMap**) at zero functional cost — but it touches a Nautilus data structure, so it
is **recorded, not implemented** (out of the chosen "config-only" scope).

## The code
`nes-nautilus/include/Nautilus/Interface/HashMap/HashMap.hpp:23`
```cpp
class AbstractHashMapEntry
{
public:
    virtual ~AbstractHashMapEntry() = default;   // <-- the ONLY virtual; forces a vtable -> 8B vptr/entry
};
```
`ChainedHashMapEntry final : public AbstractHashMapEntry` adds `ChainedHashMapEntry* next` (8 B) +
`HashFunction::HashValue::raw_type hash` (8 B). With the base's vptr that is **24 B of header**.

## The measurement (HMGAUGE instrumentation, 2026-06-08)
Per-map gauge logged from the `ChainedHashMap` destructor (throttled 1/4, read-only):
`HMGAUGE entries=… entrySize=… hdr=… buckets=… entriesPerPage=… pageSize=…`.

Run: in-mem el workload (`skew_Nw80_smallD.csv`), release build, `--cpus=2`, pool=26000, uncapped.
Aggregated over **309 maps / 5.2 M entries**:

| component | share of resident HashMap |
|---|---:|
| **per-entry header (24 B)** | **41 %** |
| key + value (the data, 16 B) | 27 % |
| bucket directory | 30 % |
| page slack | 2 % |

Measured constants: **entrySize = 40 B, header = 24 B** (so key+value = 16 B = UINT64 key + UINT64 SUM).
Header (24 B) > data (16 B): the entry is **2.5× the payload**, and the vptr is **8 of those 24 header bytes (= 20 % of the whole entry)**.

Context (same run): logical store 232 MB, resident unpooled HashMap 323 MB (×1.4 allocator overhead on top),
total process anon 674 MB (of which buffer pool 203 MB, see [[project-release-eval-setup]]).

## Why it is safe to remove (analysis)
`ChainedHashMapEntry` objects are **placement-constructed inside pages** rented from a buffer/chunk
provider and are **freed en masse when the page/HashMap is destroyed** — they are never `delete`d
individually through an `AbstractHashMapEntry*`. Therefore the virtual destructor is **never dispatched**;
making `AbstractHashMapEntry` non-polymorphic (drop the `virtual` dtor, or remove the base) would not
change behaviour. (One must confirm no call-site does `delete static_cast<AbstractHashMapEntry*>(...)`.)

## Estimated impact (from the measured data)
- Header 24 B → 16 B ⇒ entrySize 40 B → 32 B (**−20 % per entry**).
- On the el run: header share 41 % of ~323 MB ≈ **132 MB → ~88 MB (−44 MB)**; HashMap resident ~323 → ~279 MB.
- Scales with the number of distinct keys resident (per-entry cost), so it grows with workload cardinality —
  unlike the per-map overheads (bucket dir, slack) which the window-size lever addresses.

## Status / decision
**Deferred.** It is a Nautilus interface data-structure change; the project scope was set to *config-only*
levers (pool right-sizing, larger windows, bucket-clamp tuning) without touching Nautilus. Recorded here
as a measured, citable RQ5 footprint finding and a concrete future optimisation. See
[[project-ooc-memory-measurement]] for the full breakdown and [[project-thesis-scope-anchor]] for scope.
