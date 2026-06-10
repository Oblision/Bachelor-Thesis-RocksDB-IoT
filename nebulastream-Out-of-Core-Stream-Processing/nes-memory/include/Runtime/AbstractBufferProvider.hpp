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

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <optional>
#include <vector>
#include <Runtime/TupleBuffer.hpp>

/// This enum reflects the different types of buffer managers in the system
/// global: overall buffer manager
/// local: buffer manager that we give to the processing
/// fixed: buffer manager that we use for sources
namespace NES
{
enum class BufferManagerType : uint8_t
{
    GLOBAL,
    LOCAL,
    FIXED
};

class AbstractBufferProvider
{
public:
    virtual ~AbstractBufferProvider() = default;

    virtual BufferManagerType getBufferManagerType() const = 0;

    virtual size_t getBufferSize() const = 0;

    virtual size_t getNumOfPooledBuffers() const = 0;
    virtual size_t getNumOfUnpooledBuffers() const = 0;

    virtual TupleBuffer getBufferBlocking() = 0;

    virtual std::optional<TupleBuffer> getBufferNoBlocking() = 0;

    virtual std::optional<TupleBuffer> getBufferWithTimeout(std::chrono::milliseconds timeout_ms) = 0;

    /// Returns an unpooled buffer of size bufferSize wrapped in an optional or an invalid option if an error
    virtual std::optional<TupleBuffer> getUnpooledBuffer(size_t bufferSize) = 0;

    /// OOC stability lever 1a (pool-pressure spill trigger): number of currently rentable pooled buffers.
    /// Providers without a fixed pool report SIZE_MAX, which reads as "never under pressure" — so consumers
    /// (the spillable slice store) can poll this unconditionally without knowing the provider's concrete type.
    virtual size_t getNumberOfAvailableBuffers() const { return std::numeric_limits<size_t>::max(); }

    /// OOC stability lever 1b (allocation-pressure rescue): register a listener invoked by the provider when a
    /// pooled-buffer rent finds the pool EMPTY, giving the owner of reclaimable pool memory (the spillable slice
    /// store, which can spill a cold slice and thereby return its pages) a chance to free buffers BEFORE the
    /// allocation times out and aborts the query. The listener runs on the ALLOCATING thread (deliberate
    /// backpressure: the worker blocks while memory is freed) and MUST NOT rent pooled buffers itself (spilling
    /// only frees them). Returns true if it (likely) freed at least one buffer. The returned id deregisters the
    /// listener (mandatory before the listener's owner dies). Default implementation: no-op (id 0, nothing fires).
    virtual uint64_t registerPoolPressureListener(std::function<bool()> /*listener*/) { return 0; }
    virtual void unregisterPoolPressureListener(uint64_t /*listenerId*/) { }
};

}
