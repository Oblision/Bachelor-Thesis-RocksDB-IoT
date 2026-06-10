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
#include <Runtime/BufferManager.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <memory>
#include <memory_resource>
#include <mutex>
#include <optional>
#include <thread>
#include <utility>
#include <unistd.h>
#include <Runtime/AbstractBufferProvider.hpp>
#include <Runtime/BufferRecycler.hpp>
#include <Runtime/TupleBuffer.hpp>
#include <Util/Logger/Logger.hpp>
#include <folly/MPMCQueue.h>
#include <ErrorHandling.hpp>
#include <TupleBufferImpl.hpp>

namespace NES
{

BufferManager::BufferManager(
    Private,
    const uint32_t bufferSize,
    const uint32_t numOfBuffers,
    std::shared_ptr<std::pmr::memory_resource> memoryResource,
    const uint32_t withAlignment)
    : availableBuffers(numOfBuffers)
    , unpooledChunksManager(std::make_shared<UnpooledChunksManager>(memoryResource))
    , bufferSize(bufferSize)
    , numOfBuffers(numOfBuffers)
    , memoryResource(std::move(memoryResource))
{
    ((void)withAlignment);
    initialize(DEFAULT_ALIGNMENT);
}

std::shared_ptr<BufferManager> BufferManager::create(
    uint32_t bufferSize, uint32_t numOfBuffers, const std::shared_ptr<std::pmr::memory_resource>& memoryResource, uint32_t withAlignment)
{
    return std::make_shared<BufferManager>(Private{}, bufferSize, numOfBuffers, memoryResource, withAlignment);
}

BufferManager::~BufferManager()
{
    destroy();
}

void BufferManager::destroy()
{
    bool expected = false;
    NES_DEBUG("Calling BufferManager::destroy()");
    if (isDestroyed.compare_exchange_strong(expected, true))
    {
        bool success = true;
        if (allBuffers.size() != getNumberOfAvailableBuffers())
        {
            NES_ERROR("[BufferManager] total buffers {} :: available buffers {}", allBuffers.size(), getNumberOfAvailableBuffers());
            success = false;
        }
        for (auto& buffer : allBuffers)
        {
            if (!buffer.isAvailable())
            {
#ifdef NES_DEBUG_TUPLE_BUFFER_LEAKS
                buffer.controlBlock->dumpOwningThreadInfo();
#endif
                NES_ERROR("[BufferManager] leaked buffer detected: segment at {}", fmt::ptr(buffer.ptr));
                success = false;
            }
        }
        INVARIANT(
            success,
            "Requested buffer manager shutdown but a buffer is still used allBuffers={} available={}",
            allBuffers.size(),
            getNumberOfAvailableBuffers());
        /// RAII takes care of deallocating memory here
        allBuffers.clear();

        availableBuffers = decltype(availableBuffers)();
        NES_DEBUG("Shutting down Buffer Manager completed");
        memoryResource->deallocate(basePointer, allocatedAreaSize, DEFAULT_ALIGNMENT);
        allocatedAreaSize = 0;

        /// Destroying the unpooled chunks
        unpooledChunksManager.reset();
    }
}

void BufferManager::initialize(uint32_t withAlignment)
{
    const size_t pages = sysconf(_SC_PHYS_PAGES);
    size_t page_size = sysconf(_SC_PAGE_SIZE);
    auto memorySizeInBytes = pages * page_size;

    uint64_t requiredMemorySpace = this->bufferSize * this->numOfBuffers;
    double percentage = (100.0 * requiredMemorySpace) / memorySizeInBytes;
    NES_DEBUG("NES memory allocation requires {} out of {} (so {}%) available bytes", requiredMemorySpace, memorySizeInBytes, percentage);

    INVARIANT(
        requiredMemorySpace < memorySizeInBytes,
        "NES tries to allocate more memory than physically available requested={} available={}",
        requiredMemorySpace,
        memorySizeInBytes);
    if (withAlignment > 0)
    {
        PRECONDITION(
            !(withAlignment & (withAlignment - 1)), "NES tries to align memory but alignment={} is not a pow of two", withAlignment);
    }
    PRECONDITION(withAlignment <= page_size, "NES tries to align memory but alignment is invalid: {} <= {}", withAlignment, page_size);

    PRECONDITION(
        alignof(detail::BufferControlBlock) <= withAlignment,
        "Requested alignment is too small, must be at least {}",
        alignof(detail::BufferControlBlock));

    allBuffers.reserve(numOfBuffers);
    auto controlBlockSize = alignBufferSize(sizeof(detail::BufferControlBlock), withAlignment);
    auto alignedBufferSize = alignBufferSize(bufferSize, withAlignment);
    allocatedAreaSize = alignBufferSize(controlBlockSize + alignedBufferSize, withAlignment);
    const size_t offsetBetweenBuffers = allocatedAreaSize;
    allocatedAreaSize *= numOfBuffers;
    basePointer = static_cast<uint8_t*>(memoryResource->allocate(allocatedAreaSize, withAlignment));
    NES_TRACE(
        "Allocated {} bytes with alignment {} buffer size {} num buffer {} controlBlockSize {} {}",
        allocatedAreaSize,
        withAlignment,
        alignedBufferSize,
        numOfBuffers,
        controlBlockSize,
        alignof(detail::BufferControlBlock));

    INVARIANT(basePointer, "memory allocation failed, because 'basePointer' was a nullptr");
    uint8_t* ptr = basePointer;
    for (size_t i = 0; i < numOfBuffers; ++i)
    {
        uint8_t* controlBlock = ptr;
        uint8_t* payload = ptr + controlBlockSize;
        allBuffers.emplace_back(
            payload,
            bufferSize,
            [](detail::MemorySegment* segment, BufferRecycler* recycler) { recycler->recyclePooledBuffer(segment); },
            controlBlock);

        availableBuffers.write(&allBuffers.back());
        ptr += offsetBetweenBuffers;
    }
    NES_DEBUG("BufferManager configuration bufferSize={} numOfBuffers={}", this->bufferSize, this->numOfBuffers);
}

TupleBuffer BufferManager::getBufferBlocking()
{
    auto buffer = getBufferWithTimeout(GET_BUFFER_TIMEOUT);
    if (buffer.has_value())
    {
        return buffer.value();
    }
    /// Throw exception if no buffer was returned allocated after timeout.
    throw BufferAllocationFailure("Global buffer pool could not allocate buffer before timeout({})", GET_BUFFER_TIMEOUT);
}

std::optional<TupleBuffer> BufferManager::getBufferNoBlocking()
{
    detail::MemorySegment* memSegment = nullptr;
    if (!availableBuffers.read(memSegment))
    {
        return std::nullopt;
    }
    if (memSegment->controlBlock->prepare(shared_from_this()))
    {
        return TupleBuffer(memSegment->controlBlock.get(), memSegment->ptr, memSegment->size);
    }
    throw InvalidRefCountForBuffer("[BufferManager] got buffer with invalid reference counter");
}

std::optional<TupleBuffer> BufferManager::getBufferWithTimeout(const std::chrono::milliseconds timeoutMs)
{
    detail::MemorySegment* memSegment = nullptr;
    const auto deadline = std::chrono::steady_clock::now() + timeoutMs;
    /// OOC stability lever 1b: wait in short slices instead of one full-timeout block. On every empty slice,
    /// fire the registered pool-pressure listeners (the spillable slice store spills a cold slice, returning its
    /// pooled pages to `availableBuffers`), then retry until the overall deadline. With no listener registered
    /// this is just a timed-retry rendering of the historic single wait — same deadline, same outcome.
    constexpr auto PRESSURE_SLICE = std::chrono::milliseconds(50);
    while (!availableBuffers.tryReadUntil(std::min(std::chrono::steady_clock::now() + PRESSURE_SLICE, deadline), memSegment))
    {
        if (std::chrono::steady_clock::now() >= deadline)
        {
            return std::nullopt;
        }
        firePoolPressureListeners(); /// freed buffers (if any) are picked up by the next wait slice
    }
    /// OOC mem_trace gauge: throttled, read-only pool-occupancy sample (POOLGAUGE) for component
    /// attribution over time (e.g. detecting result-emission floods at stream close). One in 4096 rents
    /// (densified from 65536 for the stability-lever experiments; still amortized to noise).
    {
        static std::atomic<std::uint64_t> poolGaugeCtr{0};
        if ((poolGaugeCtr.fetch_add(1, std::memory_order_relaxed) & 0xFFFU) == 0)
        {
            const auto avail = getNumberOfAvailableBuffers();
            const auto total = allBuffers.size();
            NES_INFO("POOLGAUGE used_buf={} avail_buf={} total_buf={}", total - avail, avail, total);
        }
    }
    if (memSegment->controlBlock->prepare(shared_from_this()))
    {
        return TupleBuffer(memSegment->controlBlock.get(), memSegment->ptr, memSegment->size);
    }
    throw InvalidRefCountForBuffer("[BufferManager] got buffer with invalid reference counter");
}

uint64_t BufferManager::registerPoolPressureListener(std::function<bool()> listener)
{
    const auto id = nextPressureListenerId.fetch_add(1, std::memory_order_relaxed);
    {
        const std::scoped_lock lock(pressureListenersMutex);
        pressureListeners.emplace_back(id, std::move(listener));
    }
    NES_INFO("Registered pool-pressure listener id={}", id);
    return id;
}

void BufferManager::unregisterPoolPressureListener(const uint64_t listenerId)
{
    {
        const std::scoped_lock lock(pressureListenersMutex);
        std::erase_if(pressureListeners, [&](const auto& entry) { return entry.first == listenerId; });
    }
    /// Wait out any in-flight fire so the caller may safely destroy the listener's owner after this returns
    /// (the fire path invokes copied callbacks UNLOCKED, so erasing from the registry alone does not stop a
    /// callback that is already running). Fires are short (one cold-slice spill); bound the wait defensively.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    while (pressureFiresInFlight.load(std::memory_order_acquire) > 0)
    {
        if (std::chrono::steady_clock::now() >= deadline)
        {
            NES_ERROR("unregisterPoolPressureListener: in-flight pressure fire did not drain within 30s; proceeding");
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

bool BufferManager::firePoolPressureListeners()
{
    /// Copy the callbacks out under the lock; invoke them UNLOCKED on the allocating thread (deliberate
    /// backpressure — the worker blocks while reclaimable memory is freed). POOLRESCUE is logged throttled
    /// (1 in 8 fires) so a sustained stall cannot flood the log; the spill itself is visible in SPILLGAUGE.
    std::vector<std::function<bool()>> callbacks;
    {
        const std::scoped_lock lock(pressureListenersMutex);
        if (pressureListeners.empty())
        {
            return false;
        }
        callbacks.reserve(pressureListeners.size());
        for (const auto& [id, cb] : pressureListeners)
        {
            callbacks.push_back(cb);
        }
    }
    static std::atomic<std::uint64_t> rescueFireCtr{0};
    if ((rescueFireCtr.fetch_add(1, std::memory_order_relaxed) & 0x7U) == 0)
    {
        NES_INFO("POOLRESCUE fired listeners={} avail_buf={} total_buf={}", callbacks.size(), getNumberOfAvailableBuffers(), allBuffers.size());
    }
    /// In-flight guard: unregisterPoolPressureListener waits for this to drain before returning, so a listener's
    /// owner can be destroyed safely right after unregistering (the callbacks below run UNLOCKED).
    pressureFiresInFlight.fetch_add(1, std::memory_order_acq_rel);
    bool anyFreed = false;
    for (const auto& cb : callbacks)
    {
        anyFreed = cb() || anyFreed;
    }
    pressureFiresInFlight.fetch_sub(1, std::memory_order_acq_rel);
    return anyFreed;
}

std::optional<TupleBuffer> BufferManager::getUnpooledBuffer(const size_t bufferSize)
{
    return unpooledChunksManager->getUnpooledBuffer(bufferSize, DEFAULT_ALIGNMENT, shared_from_this());
}

void BufferManager::recyclePooledBuffer(detail::MemorySegment* segment)
{
    INVARIANT(segment->isAvailable(), "Recycling buffer callback invoked on used memory segment");
    INVARIANT(
        segment->controlBlock->owningBufferRecycler == nullptr, "Buffer should not retain a reference to its parent while not in use");
    USED_IN_DEBUG const auto couldRecycleBuffer = availableBuffers.writeIfNotFull(segment);
    INVARIANT(couldRecycleBuffer, "should always succeed");
}

void BufferManager::recycleUnpooledBuffer(detail::MemorySegment*, const AllocationThreadInfo&)
{
    INVARIANT(false, "This method should not be called!");
}

size_t BufferManager::getBufferSize() const
{
    return bufferSize;
}

size_t BufferManager::getNumOfPooledBuffers() const
{
    return numOfBuffers;
}

size_t BufferManager::getNumOfUnpooledBuffers() const
{
    return unpooledChunksManager->getNumberOfUnpooledBuffers();
}

size_t BufferManager::getNumberOfAvailableBuffers() const
{
    /// If there are pending reads the queue may report negative values. This effectivly means its empty.
    return static_cast<size_t>(std::max(availableBuffers.size(), static_cast<ssize_t>(0)));
}

BufferManagerType BufferManager::getBufferManagerType() const
{
    return BufferManagerType::GLOBAL;
}

}
