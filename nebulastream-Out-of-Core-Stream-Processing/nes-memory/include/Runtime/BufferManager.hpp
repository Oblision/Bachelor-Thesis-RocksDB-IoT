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

#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <memory_resource>
#include <mutex>
#include <optional>
#include <vector>
#include <Runtime/AbstractBufferProvider.hpp>
#include <Runtime/Allocator/NesDefaultMemoryAllocator.hpp>
#include <Runtime/BufferRecycler.hpp>
#include <Runtime/UnpooledChunksManager.hpp>
#include <folly/MPMCQueue.h>

namespace NES
{

/**
 * @brief The BufferManager is responsible for:
 * 1. Pooled Buffers: preallocated fixed-size buffers of memory that must be reference counted
 * 2. Unpooled Buffers: variable sized buffers that are allocated on-the-fly. They are also subject to reference
 * counting.
 *
 * The reference counting mechanism of the TupleBuffer is explained in TupleBuffer.hpp
 *
 * The BufferManager stores the pooled buffers as MemorySegment-s. When a component asks for a Pooled buffer,
 * then the BufferManager retrieves an available buffer (it blocks the calling thread, if no buffer is available).
 * It then hands out a TupleBuffer that is constructed through the pointer stored inside a MemorySegment.
 * This is necessary because the BufferManager must keep all buffers stored to ensure that when its
 * destructor is called, all buffers that it has ever created are deallocated. Note the BufferManager will check also
 * that no reference counter is non-zero and will throw a fatal exception, if a component hasnt returned every buffers.
 * This is necessary to avoid memory leaks.
 *
 * Unpooled buffers are either allocated on the spot or served via a previously allocated, unpooled buffer that has
 * been returned to the BufferManager by some component.
 *
 */
class BufferManager final : public std::enable_shared_from_this<BufferManager>, public BufferRecycler, public AbstractBufferProvider
{
    friend class TupleBuffer;
    friend class NES::detail::MemorySegment;

    /// Hide the BufferManager constructor and only allow creation via BufferManager::create().
    /// Following: https://en.cppreference.com/w/cpp/memory/enable_shared_from_this
    struct Private
    {
        explicit Private() = default;
    };

    static constexpr auto DEFAULT_BUFFER_SIZE = 8 * 1024;
    static constexpr auto DEFAULT_NUMBER_OF_BUFFERS = 1024;
    static constexpr auto DEFAULT_ALIGNMENT = 64;

public:
    explicit BufferManager(
        Private,
        uint32_t bufferSize,
        uint32_t numOfBuffers,
        std::shared_ptr<std::pmr::memory_resource> memoryResource,
        uint32_t withAlignment);

    /// Creates a new global buffer manager
    /// @param bufferSize the size of each buffer in bytes
    /// @param numOfBuffers the total number of buffers in the pool
    /// @param withAlignment the alignment of each buffer, default is 64 so ony cache line aligned buffers, This value must be a pow of two and smaller than page size
    /// @param memoryResource resource for allocating and deallocating memory
    static std::shared_ptr<BufferManager> create(
        uint32_t bufferSize = DEFAULT_BUFFER_SIZE,
        uint32_t numOfBuffers = DEFAULT_NUMBER_OF_BUFFERS,
        const std::shared_ptr<std::pmr::memory_resource>& memoryResource = std::make_shared<NesDefaultMemoryAllocator>(),
        uint32_t withAlignment = DEFAULT_ALIGNMENT);

    BufferManager(const BufferManager&) = delete;
    BufferManager& operator=(const BufferManager&) = delete;
    ~BufferManager() override;

    BufferManagerType getBufferManagerType() const override;

private:
    /**
     * @brief Configure the BufferManager to use numOfBuffers buffers of size bufferSize bytes.
     * This is a one shot call. A second invocation of this call will fail
     * @param withAlignment
     */
    void initialize(uint32_t withAlignment);

public:
    /// This blocks until a buffer is available.
    TupleBuffer getBufferBlocking() override;

    /// invalid optional if there is no buffer.
    std::optional<TupleBuffer> getBufferNoBlocking() override;

    /**
     * @brief Returns a new Buffer wrapped in an optional or an invalid option if there is no buffer available within
     * timeoutMs.
     * @param timeoutMs the amount of time to wait for a new buffer to be retuned
     * @return a new buffer
     */
    std::optional<TupleBuffer> getBufferWithTimeout(std::chrono::milliseconds timeoutMs) override;

    std::optional<TupleBuffer> getUnpooledBuffer(size_t bufferSize) override;


    size_t getBufferSize() const override;
    size_t getNumOfPooledBuffers() const override;
    size_t getNumOfUnpooledBuffers() const override;
    size_t getNumberOfAvailableBuffers() const override;

    /// OOC stability lever 1b: pool-pressure listeners (see AbstractBufferProvider). Thread-safe; listeners fire
    /// from getBufferWithTimeout on the allocating thread when the pool is empty, OUTSIDE any BufferManager lock.
    uint64_t registerPoolPressureListener(std::function<bool()> listener) override;
    void unregisterPoolPressureListener(uint64_t listenerId) override;

    /// OOC stability lever N2 (allocation backpressure): when ms > 0, getBufferBlocking on an empty pool stalls
    /// the allocating thread for up to ms total (re-firing the pressure listeners every wait slice) instead of
    /// aborting after GET_BUFFER_TIMEOUT. A recycle-progress guard aborts early if no buffer is returned
    /// pool-wide for two consecutive timeout rounds (gridlock: the would-be drainers are blocked too).
    /// Call once at engine construction, before queries run. 0 (default) keeps the historic abort.
    void setAllocationBackpressureMs(uint32_t ms) { allocationBackpressureMs = ms; }

    /// Explicitly shuts down the buffer manager: checks for leaked buffers (fires INVARIANT on leaks),
    /// deallocates all memory, and marks the manager as destroyed. The destructor calls this automatically
    /// if it has not already been called.
    void destroy();

    /**
     * @brief Recycle a pooled buffer by making it available to others
     * @param buffer
     */
    void recyclePooledBuffer(NES::detail::MemorySegment* segment) override;

    /**
    * @brief Recycle an unpooled buffer by making it available to others
    * @param buffer
    */
    void recycleUnpooledBuffer(NES::detail::MemorySegment* segment, const AllocationThreadInfo&) override;

private:
    std::vector<NES::detail::MemorySegment> allBuffers;

    folly::MPMCQueue<NES::detail::MemorySegment*> availableBuffers;

    std::shared_ptr<NES::UnpooledChunksManager> unpooledChunksManager;

    size_t bufferSize;
    size_t numOfBuffers;

    uint8_t* basePointer{nullptr};
    size_t allocatedAreaSize;

    std::shared_ptr<std::pmr::memory_resource> memoryResource;
    std::atomic<bool> isDestroyed{false};

    /// 1b fire path: invoked from getBufferWithTimeout when a wait slice found the pool empty. Copies the
    /// callbacks out under the lock, invokes them UNLOCKED on the allocating thread. Returns true if any
    /// listener reported having freed something.
    bool firePoolPressureListeners();

    /// 1b listener registry. A plain mutex-guarded map (id → listener): registration/deregistration are rare
    /// (query setup/teardown); the fire path copies the callbacks out under the lock and invokes them unlocked,
    /// so a listener that itself touches the BufferManager (e.g. queries availability) cannot deadlock.
    mutable std::mutex pressureListenersMutex;
    std::vector<std::pair<uint64_t, std::function<bool()>>> pressureListeners;
    std::atomic<uint64_t> nextPressureListenerId{1};
    /// Count of fire invocations currently executing copied callbacks (unlocked). unregister waits on 0.
    std::atomic<uint32_t> pressureFiresInFlight{0};

    /// N2 allocation backpressure (0 = off). Set once before queries run; read only on the empty-pool path.
    uint32_t allocationBackpressureMs{0};
    /// N2 progress guard: bumped on every pooled-buffer recycle; a stalled allocator that sees no epoch
    /// movement across two full timeout rounds concludes gridlock and falls back to the historic abort.
    std::atomic<uint64_t> recycleEpoch{0};
    /// N2 observability (reported as "Backpressure metrics:" in destroy()):
    std::atomic<uint64_t> bpStallCount{0}; /// allocations that entered the stall path
    std::atomic<uint64_t> bpStallUsTotal{0}; /// total stalled wall time
    std::atomic<uint64_t> bpResolved{0}; /// stalls that got a buffer (the lever working)
    std::atomic<uint64_t> bpGridlockAborts{0}; /// stalls aborted by the progress guard
    std::atomic<uint64_t> bpBudgetAborts{0}; /// stalls aborted by the total-budget ceiling
};


}
