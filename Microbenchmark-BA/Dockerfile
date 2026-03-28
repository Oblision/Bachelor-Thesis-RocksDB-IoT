# Stage 1: Build db_bench from RocksDB source
FROM ubuntu:24.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ make libgflags-dev libsnappy-dev liblz4-dev libzstd-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY rocksdb/ /rocksdb/

WORKDIR /rocksdb/build

RUN cmake -DCMAKE_BUILD_TYPE=Release \
      -DWITH_GFLAGS=1 \
      -DWITH_SNAPPY=1 \
      -DWITH_LZ4=1 \
      -DWITH_ZSTD=1 \
      -DWITH_ZLIB=1 \
      -DWITH_BENCHMARK_TOOLS=ON \
      -DWITH_ALL_TESTS=OFF \
      -DROCKSDB_BUILD_SHARED=0 .. \
    && make -j$(nproc) db_bench

# Stage 2: Slim runtime with just the db_bench binary
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgflags2.2 libsnappy1v5 liblz4-1 libzstd1 zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /rocksdb/build/db_bench /usr/local/bin/db_bench

RUN mkdir -p /data/rocksdb

ENTRYPOINT ["db_bench"]
