# Spillable Window Slice Storage for NebulaStream

## Overview

Implement a **hybrid spillable storage layer** that:
1. Uses fast in-memory storage by default
2. **Strategically** spills large, long-lived slices to disk (for large aggregations over long windows)
3. **Reactively** spills when memory budget is exceeded (to prevent OOM under sporadic high traffic)

## Design Philosophy

```
                         Memory (Fast, Limited)
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
    Small/Hot Slices      Budget Tracking         Large/Cold Slices
    (keep in memory)             │                (spill to disk)
          │                      │                      │
          │         ┌────────────┴────────────┐         │
          │         │                         │         │
          │    Soft Watermark            Hard Watermark │
          │    (start spilling)          (aggressive)   │
          │         │                         │         │
          └─────────┴─────────────────────────┴─────────┘
                                 │
                    Persistent Storage (Slow, Large)
                           (RocksDB)
```

**Two spilling triggers:**
| Trigger | Reason | Strategy |
|---------|--------|----------|
| **Strategic** | Slice grows large over time (long windows, many keys) | Spill when slice exceeds size threshold |
| **Reactive** | Memory budget watermark hit (sporadic traffic) | Spill LRU (least recently used) slices |

---

## Memory Budget Model

User configures a **memory budget** for slice storage. The system tracks actual slice memory usage against this budget.

```
Memory Budget (e.g., 512 MB)
    ^
    │  ═══════════ Hard Watermark (90%) ═══════════
    │     [CRITICAL: Aggressive LRU spilling]
    │
    │  ─────────── Soft Watermark (70%) ───────────
    │     [WARNING: Start spilling cold/large slices]
    │
    │  ~~~~~~~~~~~ Unspill Zone (< 50%) ~~~~~~~~~~~
    │     [SAFE: Can bring spilled data back if accessed]
    │
    └──────────────────────────────────────────────> Slice Memory Usage
```

**Additionally**, slices that exceed a **per-slice size threshold** (e.g., 10 MB) are candidates for strategic spilling regardless of overall memory pressure - these are likely large aggregations that benefit from disk storage.

---

## Architecture

```
WindowSlicesStoreInterface (interface)
         │
    ┌────┴────┬───────────────────────┐
    │         │                       │
DefaultTimeBasedSliceStore    SpillableTimeBasedSliceStore (NEW)
(in-memory only)                      │
                              ┌───────┴───────┐
                              │               │
                        MemoryBudgetTracker   RocksDBSpillBackend
                              │               │
                              └───────┬───────┘
                                      │
                              SpillableSlice
                              (can live in memory OR disk)
```

---

## Files to Create

### 1. Memory Budget Tracking
| File | Purpose |
|------|---------|
| `nes-physical-operators/include/SliceStore/Spilling/MemoryBudgetTracker.hpp` | Track slice memory usage against configurable budget |
| `nes-physical-operators/src/SliceStore/Spilling/MemoryBudgetTracker.cpp` | Implementation |

### 2. RocksDB Spill Backend
| File | Purpose |
|------|---------|
| `nes-physical-operators/include/SliceStore/Spilling/RocksDBSpillBackend.hpp` | RocksDB wrapper for persistent state |
| `nes-physical-operators/src/SliceStore/Spilling/RocksDBSpillBackend.cpp` | Implementation |

### 3. Spillable Components
| File | Purpose |
|------|---------|
| `nes-physical-operators/include/SliceStore/Spilling/SpillableTimeBasedSliceStore.hpp` | Main store with spill logic |
| `nes-physical-operators/include/SliceStore/Spilling/SpillableSlice.hpp` | Slice that can be in memory or on disk |
| `nes-physical-operators/src/SliceStore/Spilling/SpillableTimeBasedSliceStore.cpp` | Implementation |
| `nes-physical-operators/src/SliceStore/Spilling/SpillableSlice.cpp` | Implementation |

### 4. Configuration
| File | Purpose |
|------|---------|
| `nes-physical-operators/include/SliceStore/Spilling/SpillConfig.hpp` | Configuration struct |

---

## Files to Modify

| File | Change |
|------|--------|
| [LowerToPhysicalWindowedAggregation.cpp:217-220](nes-query-optimizer/src/RewriteRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp#L217-L220) | Create SpillableTimeBasedSliceStore when enabled |
| `QueryExecutionConfiguration.hpp` | Add spill configuration |
| `nes-physical-operators/CMakeLists.txt` | Link RocksDB |
| `vcpkg/vcpkg.json` | Add RocksDB dependency |

---

## Core Design

### SpillConfig

```cpp
struct SpillConfig {
    // Memory budget
    size_t memoryBudgetBytes = 512 * 1024 * 1024;  // 512 MB default
    double softWatermarkRatio = 0.70;               // Start spilling at 70%
    double hardWatermarkRatio = 0.90;               // Aggressive spill at 90%
    double unspillThresholdRatio = 0.50;            // Can unspill below 50%

    // Strategic spilling (large slices)
    size_t sliceSizeThresholdBytes = 10 * 1024 * 1024;  // 10 MB per slice

    // Storage path
    std::filesystem::path rocksDBPath = "/tmp/nes-spill";

    bool enabled = false;  // Must opt-in
};
```

### MemoryBudgetTracker

```cpp
class MemoryBudgetTracker {
public:
    explicit MemoryBudgetTracker(SpillConfig config);

    // Track slice memory
    void trackAllocation(SliceEnd sliceEnd, size_t bytes);
    void trackDeallocation(SliceEnd sliceEnd, size_t bytes);
    void updateSliceSize(SliceEnd sliceEnd, size_t newSize);

    // Query state
    size_t getCurrentUsageBytes() const;
    size_t getBudgetBytes() const;
    double getUsageRatio() const;

    enum class WatermarkState { SAFE, SOFT, HARD };
    WatermarkState getWatermarkState() const;

    // Spill decisions
    bool shouldSpill() const;
    bool canUnspill(size_t additionalBytes) const;

    // Get slices sorted by last access time (LRU order)
    std::vector<SliceEnd> getSlicesLRUOrder() const;

    // Get slices exceeding size threshold (strategic spill candidates)
    std::vector<SliceEnd> getLargeSlices() const;

private:
    SpillConfig config;

    struct SliceInfo {
        size_t sizeBytes;
        std::chrono::steady_clock::time_point lastAccessTime;
    };
    folly::Synchronized<std::unordered_map<SliceEnd, SliceInfo>> sliceRegistry;
    std::atomic<size_t> totalUsageBytes{0};
};
```

### SpillableSlice

```cpp
enum class SliceLocation { IN_MEMORY, ON_DISK };

class SpillableSlice : public Slice {
public:
    SpillableSlice(
        SliceStart start, SliceEnd end,
        std::shared_ptr<AggregationSlice> memorySlice,
        std::shared_ptr<RocksDBSpillBackend> spillBackend,
        std::shared_ptr<MemoryBudgetTracker> budgetTracker,
        const CreateNewHashMapSliceArgs& sliceArgs);

    // Location management
    SliceLocation getLocation() const;
    bool isInMemory() const { return location == SliceLocation::IN_MEMORY; }
    bool isOnDisk() const { return location == SliceLocation::ON_DISK; }

    // Spill/Unspill operations
    void spill();    // Memory → RocksDB
    void unspill();  // RocksDB → Memory

    // Transparent access (unspills on-demand if needed)
    HashMap* getHashMapPtrOrCreate(WorkerThreadId workerThreadId);
    HashMap* getHashMapPtr(WorkerThreadId workerThreadId) const;
    uint64_t getNumberOfHashMaps() const;

    // Metrics
    size_t getMemoryFootprint() const;
    std::chrono::steady_clock::time_point getLastAccessTime() const;
    void touchAccessTime();  // Update LRU tracking

private:
    std::atomic<SliceLocation> location{SliceLocation::IN_MEMORY};

    // In-memory state (nullptr when spilled)
    std::shared_ptr<AggregationSlice> memorySlice;

    // Persistent storage
    std::shared_ptr<RocksDBSpillBackend> spillBackend;

    // For recreating slice after unspill
    CreateNewHashMapSliceArgs sliceArgs;

    // Budget tracking
    std::shared_ptr<MemoryBudgetTracker> budgetTracker;

    // Thread safety for spill/unspill transitions
    mutable std::shared_mutex stateMutex;

    // LRU tracking
    std::atomic<std::chrono::steady_clock::time_point> lastAccessTime;
};
```

### SpillableTimeBasedSliceStore

```cpp
class SpillableTimeBasedSliceStore final : public WindowSlicesStoreInterface {
public:
    SpillableTimeBasedSliceStore(
        uint64_t windowSize,
        uint64_t windowSlide,
        SpillConfig config);

    ~SpillableTimeBasedSliceStore() override;

    // WindowSlicesStoreInterface implementation
    std::vector<std::shared_ptr<Slice>> getSlicesOrCreate(
        Timestamp timestamp,
        const std::function<std::vector<std::shared_ptr<Slice>>(SliceStart, SliceEnd)>& createNewSlice) override;

    std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
    getTriggerableWindowSlices(Timestamp globalWatermark) override;

    std::optional<std::shared_ptr<Slice>> getSliceBySliceEnd(SliceEnd sliceEnd) override;

    std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
    getAllNonTriggeredSlices() override;

    void garbageCollectSlicesAndWindows(Timestamp newGlobalWaterMark) override;
    void deleteState() override;
    void incrementNumberOfInputPipelines() override;
    uint64_t getWindowSize() const override;

private:
    // Window/slice metadata (always in memory - small footprint)
    folly::Synchronized<std::map<WindowInfo, SlicesAndState>> windows;
    folly::Synchronized<std::map<SliceEnd, std::shared_ptr<SpillableSlice>>> slices;

    SliceAssigner sliceAssigner;
    std::atomic<SequenceNumber::Underlying> sequenceNumber{0};
    std::atomic<uint64_t> numberOfActiveInputPipelines{0};

    // Spilling infrastructure
    SpillConfig config;
    std::shared_ptr<MemoryBudgetTracker> budgetTracker;
    std::shared_ptr<RocksDBSpillBackend> spillBackend;

    // Spill management
    void checkAndSpillIfNeeded();
    void spillSlice(std::shared_ptr<SpillableSlice> slice);

    // Strategic spilling: large slices
    void checkForLargeSlices();

    // Reactive spilling: LRU under memory pressure
    void spillLRUSlices(size_t targetBytesToFree);
};
```

---

## Spilling Logic

### When to Check for Spilling

```cpp
std::vector<std::shared_ptr<Slice>> SpillableTimeBasedSliceStore::getSlicesOrCreate(
    Timestamp timestamp,
    const std::function<...>& createNewSlice)
{
    // ... create or get slice ...

    // After adding data, check spill conditions
    checkAndSpillIfNeeded();

    return result;
}

void SpillableTimeBasedSliceStore::checkAndSpillIfNeeded() {
    // 1. Strategic: Check for large slices that should be on disk
    checkForLargeSlices();

    // 2. Reactive: Check memory watermarks
    auto state = budgetTracker->getWatermarkState();

    if (state == WatermarkState::HARD) {
        // Aggressive: free 20% of budget
        size_t target = config.memoryBudgetBytes * 0.20;
        spillLRUSlices(target);
    }
    else if (state == WatermarkState::SOFT) {
        // Gentle: free 10% of budget
        size_t target = config.memoryBudgetBytes * 0.10;
        spillLRUSlices(target);
    }
}
```

### Strategic Spilling (Large Slices)

```cpp
void SpillableTimeBasedSliceStore::checkForLargeSlices() {
    auto largeSlices = budgetTracker->getLargeSlices();

    for (auto sliceEnd : largeSlices) {
        auto slicesLock = slices.rlock();
        if (auto it = slicesLock->find(sliceEnd); it != slicesLock->end()) {
            auto& slice = it->second;
            if (slice->isInMemory() &&
                slice->getMemoryFootprint() > config.sliceSizeThresholdBytes) {
                // Large slice → move to disk for better scalability
                spillSlice(slice);
            }
        }
    }
}
```

### Reactive Spilling (LRU under pressure)

```cpp
void SpillableTimeBasedSliceStore::spillLRUSlices(size_t targetBytesToFree) {
    size_t freedBytes = 0;

    // Get slices in LRU order (coldest first)
    auto lruOrder = budgetTracker->getSlicesLRUOrder();

    auto slicesLock = slices.wlock();
    for (auto sliceEnd : lruOrder) {
        if (freedBytes >= targetBytesToFree) break;

        if (auto it = slicesLock->find(sliceEnd); it != slicesLock->end()) {
            auto& slice = it->second;
            if (slice->isInMemory()) {
                size_t sliceSize = slice->getMemoryFootprint();
                spillSlice(slice);
                freedBytes += sliceSize;
            }
        }
    }
}
```

### Transparent Access (On-Demand Unspill)

```cpp
HashMap* SpillableSlice::getHashMapPtrOrCreate(WorkerThreadId workerThreadId) {
    touchAccessTime();  // Update LRU tracking

    // Fast path: already in memory
    {
        std::shared_lock readLock(stateMutex);
        if (location == SliceLocation::IN_MEMORY) {
            return memorySlice->getHashMapPtrOrCreate(workerThreadId);
        }
    }

    // Slow path: need to unspill
    unspill();
    return memorySlice->getHashMapPtrOrCreate(workerThreadId);
}
```

---

## RocksDB Key Encoding

```cpp
// Key format: [sliceEnd:8B big-endian][workerThreadId:2B][entryKey:varlen]
// Big-endian ensures lexicographic ordering by time

class RocksDBKeyEncoder {
public:
    static std::string encode(SliceEnd sliceEnd, WorkerThreadId tid,
                              std::span<const std::byte> entryKey);

    static std::tuple<SliceEnd, WorkerThreadId, std::vector<std::byte>>
    decode(const rocksdb::Slice& key);

    // For prefix iteration over a slice
    static std::string slicePrefix(SliceEnd sliceEnd);

    // For DeleteRange during garbage collection
    static std::pair<std::string, std::string> sliceRange(SliceEnd sliceEnd);
};
```

---

## Integration Point

```cpp
// In LowerToPhysicalWindowedAggregation.cpp (line 217)
std::unique_ptr<WindowSlicesStoreInterface> sliceAndWindowStore;

if (conf.spillConfig.enabled) {
    sliceAndWindowStore = std::make_unique<SpillableTimeBasedSliceStore>(
        windowType->getSize().getTime(),
        windowType->getSlide().getTime(),
        conf.spillConfig);
} else {
    sliceAndWindowStore = std::make_unique<DefaultTimeBasedSliceStore>(
        windowType->getSize().getTime(),
        windowType->getSlide().getTime());
}
```

---

## Implementation Phases

### Phase 1: Infrastructure
1. Add RocksDB to vcpkg/CMake
2. Implement `SpillConfig` struct
3. Implement `MemoryBudgetTracker` with LRU tracking
4. Implement `RocksDBSpillBackend` (put/get/delete/iterate/deleteRange)

### Phase 2: Spillable Slice
5. Implement `SpillableSlice` wrapping `AggregationSlice`
6. Implement `spill()` - serialize HashMap entries to RocksDB
7. Implement `unspill()` - restore from RocksDB to memory
8. Implement transparent `getHashMapPtrOrCreate()` with on-demand unspill

### Phase 3: Spillable Store
9. Implement `SpillableTimeBasedSliceStore` (copy structure from `DefaultTimeBasedSliceStore`)
10. Implement `checkAndSpillIfNeeded()` with dual triggers
11. Implement `checkForLargeSlices()` (strategic)
12. Implement `spillLRUSlices()` (reactive)

### Phase 4: Integration
13. Add `SpillConfig` to `QueryExecutionConfiguration`
14. Modify lowering rule to use spillable store
15. Update build system

### Phase 5: Testing & Evaluation
16. Unit tests: spill/unspill correctness, LRU ordering
17. Integration tests: results match in-memory baseline
18. Benchmarks: throughput/latency (mean, p95, p99) under memory pressure
19. Memory behavior: verify spilling activates at correct thresholds

---

## Verification Plan

| Test | Description |
|------|-------------|
| **Correctness** | Same aggregation results with/without spilling |
| **Strategic trigger** | Large slices auto-spill when exceeding threshold |
| **Reactive trigger** | LRU spilling activates at soft/hard watermarks |
| **On-demand unspill** | Accessing spilled slice brings it back transparently |
| **Memory bounds** | Usage stays within budget under sustained load |
| **OOM prevention** | System survives traffic spikes that exceed budget |

---

## Critical Files Reference

- Interface: [WindowSlicesStoreInterface.hpp](nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp)
- Reference impl: [DefaultTimeBasedSliceStore.hpp](nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp)
- Integration point: [LowerToPhysicalWindowedAggregation.cpp:217](nes-query-optimizer/src/RewriteRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp#L217)
- Current slice: [AggregationSlice.hpp](nes-physical-operators/include/Aggregation/AggregationSlice.hpp)
- HashMap iteration: [ChainedHashMap](nes-physical-operators/include/Nautilus/Interface/HashMap/ChainedHashMap/)

