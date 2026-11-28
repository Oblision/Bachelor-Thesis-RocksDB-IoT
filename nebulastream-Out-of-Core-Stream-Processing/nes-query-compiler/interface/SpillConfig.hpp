/*
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        https://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
*/

#pragma once

#include <string>
#include <vector>
#include <Configurations/BaseConfiguration.hpp>
#include <Configurations/BaseOption.hpp>
#include <Configurations/ScalarOption.hpp>

namespace NES
{

/// Configuration for the out-of-core (spillable) window-slice store.
///
/// Phase 1 (skeleton) only declares the knobs and wires them into
/// QueryExecutionConfiguration; nothing spills yet. When `enabled` is false
/// (the default) the lowering keeps using the in-memory DefaultTimeBasedSliceStore.
/// The remaining fields are consumed by later phases (RocksDB backend, memory
/// governor) and are validated there, so this config stays header-only and
/// dependency-light, mirroring SliceCacheConfiguration.
class SpillConfig final : public BaseConfiguration
{
public:
    SpillConfig() = default;
    SpillConfig(const std::string& name, const std::string& description) : BaseConfiguration(name, description) { }

    /// Master switch. While false, the slice store is fully in-memory.
    BoolOption enabled = {"enabled", "false", "Enable the spillable (out-of-core) window-slice store."};

    /// Directory backing the RocksDB instance used for spilled slices (Phase 3+).
    /// The /tmp default is a development placeholder; deployments should set a durable path.
    StringOption rocksdbPath = {"rocksdb_path", "/tmp/nes-spill", "Filesystem path for the RocksDB spill backend (set a durable path in production)."};

    /// Soft memory watermark (MB). Crossing it triggers background spilling (Phase 5).
    UIntOption softThresholdMB = {"soft_threshold_mb", "256", "Soft memory watermark in MB; background spill above this."};

    /// Hard memory watermark (MB). Crossing it triggers synchronous spilling (Phase 5).
    UIntOption hardThresholdMB = {"hard_threshold_mb", "384", "Hard memory watermark in MB; synchronous spill above this."};

    /// RocksDB block compression for spilled slices (Phase 3+).
    StringOption compression = {"compression", "lz4", "Compression for spilled slices [lz4|zstd|none]."};

    /// RocksDB write-buffer (memtable) size in MB (Phase 0a). RocksDB's untuned default (~64 MB per instance) is a
    /// fixed memory tax under a tight cap (see E0). 0 = leave RocksDB's own default (behavior-preserving).
    UIntOption writeBufferSizeMB
        = {"write_buffer_size_mb", "0", "RocksDB write-buffer (memtable) size in MB for spilled slices; 0 = RocksDB default."};

    /// RocksDB block-cache size in MB (Phase 0a). Bounds the read-cache footprint of the spill backend.
    /// 0 = leave RocksDB's own default (behavior-preserving).
    UIntOption blockCacheSizeMB
        = {"block_cache_size_mb", "0", "RocksDB block-cache size in MB for the spill backend; 0 = RocksDB default."};

    /// Event-time emit/retention lag L (same unit as the window/watermark, ms). Results emit on a watermark of
    /// globalWatermark - emit_lag, so completed windows in [globalWatermark - L, globalWatermark) are RETAINED and
    /// become spillable (Increment C, emit-decouple). 0 = today's behavior (emit wm == global wm).
    UIntOption emitLag
        = {"emit_lag", "0", "Event-time emit/retention lag L (ms); retain+spill completed windows behind the watermark. 0 = no lag."};

    /// Number of grace-hash partitions for spilled window state at terminal emit (Phase 2, 2d). 1 (default) bypasses
    /// partitioning entirely and is byte-identical to the non-partitioned emit; P>1 splits each spilled worker map by
    /// `hash % P` so each emitted chunk materialises only 1/P of the window state at a time (textbook grace-hash).
    UIntOption numberOfPartitions
        = {"number_of_partitions",
           "1",
           "Number of grace-hash partitions for spilled window state at terminal emit; 1 (default) = no partitioning "
           "(byte-identical to non-partitioned emit)."};

    /// Stability lever 1a+1b+1d (pool-pressure defense). Percentage of the global buffer pool the spillable store
    /// keeps FREE as a reserve: the background spiller additionally drains cold slices whenever availability drops
    /// below the reserve (1a), the store registers a pool-pressure rescue listener with the BufferManager so an
    /// allocation that finds the pool empty synchronously spills a cold slice instead of timing out into rc=141 (1b),
    /// and bucket directories are counted in the resident budget (1d). 0 (default) disables all three — byte-identical
    /// to the pre-lever store.
    UIntOption poolReservePct
        = {"pool_reserve_pct",
           "0",
           "Percentage [0-90] of the global buffer pool kept free as a spill-defended reserve; 0 (default) disables "
           "the pool-pressure levers (baseline behavior)."};

    /// Stability lever 1c (paced steady-state trigger flush). When true AND pool_reserve_pct > 0, the steady-state
    /// emit path (getTriggerableWindowSlices) re-materialises triggerable windows ONE at a time and, before each
    /// unspill, waits (bounded) for pool availability to recover above the reserve — so a watermark jump that turns
    /// the whole retained band triggerable at once becomes a paced drain instead of a pool-exhausting flood. False
    /// (default) keeps the historic batch unspill.
    BoolOption pacedTriggerFlush
        = {"paced_trigger_flush",
           "false",
           "Pace the steady-state trigger flush per-window against the pool reserve (requires pool_reserve_pct > 0); "
           "false (default) = historic batch unspill."};

    /// Stability lever 1e (growth-path spill signal). The hard inline-spill and the soft condvar wake only fire when a
    /// GENUINELY NEW slice is created (the per-slice budget check in getSlicesOrCreate). A high-cardinality aggregation,
    /// however, keeps GROWING the current slice's HashMap between window boundaries WITHOUT creating a new slice, so
    /// neither signal fires and the only backstop for that growth is the SPILLER_POLL_INTERVAL safety-net tick (~100 ms).
    /// This knob re-evaluates the SAME soft/hard thresholds on the existing-slice (cache-hit) write path, sampled every
    /// N writes so the budget read (which takes the eviction mutex and scans the tracked slices) never runs per record.
    /// N=0 (default) disables the extra signal entirely — byte-identical to the pre-lever store. A typical armed value
    /// is a few thousand (trade signal latency against the sampling cost); start at 4096 and tune from the SPILLGAUGE trace.
    UIntOption growthSpillCheckStride
        = {"growth_spill_check_stride",
           "0",
           "Re-check spill thresholds every N writes into an existing slice so intra-slice growth triggers spilling "
           "without waiting for the next new slice or the ~100 ms poll; 0 (default) disables (baseline behavior)."};

    /// Lever 1e observe-only (diagnosis) switch. With growth_spill_check_stride > 0 the growth signal normally ACTS
    /// (inline hard eviction / soft spiller wake). Set this true to make it only TRACE and COUNT the spill it WOULD
    /// have triggered — no eviction, no wake — so a re-run of a cell that died at baseline shows whether a single
    /// established slice crossed soft/hard between window boundaries BEFORE the death, without the controller changing
    /// the outcome. Meaningless (and ignored) when growth_spill_check_stride == 0. False (default) = the signal acts.
    BoolOption growthSpillObserveOnly
        = {"growth_spill_observe_only",
           "false",
           "Diagnosis arm for growth_spill_check_stride: trace/count the growth-path spill signal but take NO action "
           "(no eviction, no spiller wake); requires growth_spill_check_stride > 0; false (default) = the signal acts."};

    /// Lever 1e soft-only armed mode. When the growth signal is armed (stride>0, not observe-only), the default HARD
    /// branch runs a SYNCHRONOUS evictUntilUnderHard() on the worker thread — which the full-matrix run showed stalls
    /// the ingest pipeline into a buffer-alloc timeout (rc=11) even with the pool healthy. Set this true to make the
    /// armed signal ALWAYS just wake the background spiller (the soft action), never evict inline — defending the pool
    /// via the async spiller without blocking the worker. Trades "guaranteed under hard on return" for no worker stall.
    BoolOption growthSpillSoftOnly
        = {"growth_spill_soft_only",
           "false",
           "Armed growth signal wakes the background spiller even over hard (no synchronous inline eviction on the "
           "worker); requires growth_spill_check_stride > 0 and not observe-only; false (default) = inline evict over hard."};

private:
    std::vector<BaseOption*> getOptions() override
    {
        return {
            &enabled,
            &rocksdbPath,
            &softThresholdMB,
            &hardThresholdMB,
            &compression,
            &writeBufferSizeMB,
            &blockCacheSizeMB,
            &emitLag,
            &numberOfPartitions,
            &poolReservePct,
            &pacedTriggerFlush,
            &growthSpillCheckStride,
            &growthSpillObserveOnly,
            &growthSpillSoftOnly};
    }
};

}
