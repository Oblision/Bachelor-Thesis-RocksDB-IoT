"""
Parser for RocksDB db_bench output.

Extracts throughput, latency percentiles, compaction stats, and stall metrics
from raw db_bench log text.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BenchmarkResult:
    """Structured metrics parsed from a single db_bench run."""

    # Throughput
    ops_per_sec: Optional[float] = None
    micros_per_op: Optional[float] = None
    mb_per_sec: Optional[float] = None

    # Latency percentiles (microseconds)
    p50: Optional[float] = None
    p75: Optional[float] = None
    p99: Optional[float] = None
    p99_9: Optional[float] = None
    p99_99: Optional[float] = None

    # Compaction / I/O
    write_amp: Optional[float] = None
    compaction_write_gb: Optional[float] = None
    compact_read_gb: Optional[float] = None
    flush_write_gb: Optional[float] = None
    bytes_written: Optional[int] = None
    bytes_read: Optional[int] = None

    # Block cache
    block_cache_hit_rate: Optional[float] = None

    # Stalls
    stall_percent: Optional[float] = None
    stall_micros: Optional[int] = None


def parse_ops_line(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Parse the summary ops line, e.g.:
      fillrandom : 3.175 micros/op 314982 ops/sec 3.175 seconds 1000000 operations; 245.1 MB/s

    Uses the LAST match to handle prepopulated runs (fillrandom,readrandom)
    where the first match is the prepopulation phase, not the target workload.
    """
    pattern = r"(\w+)\s+:\s+([\d.]+)\s+micros/op\s+(\d+)\s+ops/sec[^\n]+"
    matches = list(re.finditer(pattern, text))
    if not matches:
        return None, None, None

    last = matches[-1]
    micros_per_op = float(last.group(2))
    ops_per_sec = float(last.group(3))

    # MB/s appears later on the same line after a semicolon
    mb_match = re.search(r";\s+([\d.]+)\s+MB/s", last.group(0))
    mb_per_sec = float(mb_match.group(1)) if mb_match else None
    return ops_per_sec, micros_per_op, mb_per_sec


def parse_percentiles(text: str) -> dict[str, Optional[float]]:
    """
    Parse histogram percentile lines. db_bench outputs lines like:
      Percentiles: P50: 2.57 P75: 3.43 P99: 11.78 P99.9: 27.39 P99.99: 101.52

    Finds the LAST "Percentiles:" line to handle prepopulated runs.
    """
    result = {"p50": None, "p75": None, "p99": None, "p99_9": None, "p99_99": None}

    # Find all Percentiles lines and use the last one
    pct_pattern = r"Percentiles:.*"
    pct_matches = list(re.finditer(pct_pattern, text))
    if not pct_matches:
        return result

    last_pct_line = pct_matches[-1].group(0)

    patterns = {
        "p50": r"P50\s*:\s*([\d.]+)",
        "p75": r"P75\s*:\s*([\d.]+)",
        "p99": r"P99\s*:\s*([\d.]+)",
        "p99_9": r"P99\.9\s*:\s*([\d.]+)",
        "p99_99": r"P99\.99\s*:\s*([\d.]+)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, last_pct_line)
        if match:
            result[key] = float(match.group(1))

    return result


def parse_write_amp(text: str) -> Optional[float]:
    """
    Compute write amplification from statistics counters.
    W-Amp = (flush_bytes + compact_write_bytes) / user_bytes_written

    Falls back to the compaction stats 'Sum' table if counters are missing.
    """
    # Primary: compute from statistics counters
    flush_match = re.search(r"rocksdb\.flush\.write\.bytes\s+COUNT\s*:\s*(\d+)", text)
    compact_match = re.search(r"rocksdb\.compact\.write\.bytes\s+COUNT\s*:\s*(\d+)", text)
    user_match = re.search(r"rocksdb\.bytes\.written\s+COUNT\s*:\s*(\d+)", text)

    if flush_match and compact_match and user_match:
        flush_bytes = int(flush_match.group(1))
        compact_bytes = int(compact_match.group(1))
        user_bytes = int(user_match.group(1))
        if user_bytes > 0:
            return round((flush_bytes + compact_bytes) / user_bytes, 2)

    # Fallback: compaction stats table 'Sum' line
    pattern = r"^\s*Sum\b.*?\s+([\d.]+)\s*$"
    for line in text.splitlines():
        match = re.match(pattern, line)
        if match:
            numbers = re.findall(r"[\d.]+", line)
            if numbers:
                return float(numbers[-1])
    return None


def parse_compaction_write_gb(text: str) -> Optional[float]:
    """
    Compute total compaction bytes written in GB from statistics counters.

    Falls back to the compaction stats 'Sum' table if counters are missing.
    """
    # Primary: from statistics counter
    compact_match = re.search(r"rocksdb\.compact\.write\.bytes\s+COUNT\s*:\s*(\d+)", text)
    if compact_match:
        return round(int(compact_match.group(1)) / (1024 ** 3), 4)

    # Fallback: compaction stats table
    pattern = r"^\s*Sum\b"
    for line in text.splitlines():
        if re.match(pattern, line):
            numbers = re.findall(r"[\d.]+", line)
            if len(numbers) > 7:
                return float(numbers[7])
    return None


def parse_stall_micros(text: str) -> Optional[int]:
    """
    Parse stall microseconds from statistics output.
    Looks for: rocksdb.stall.micros COUNT : <value>
    """
    pattern = r"rocksdb\.stall\.micros\s+COUNT\s*:\s*(\d+)"
    match = re.search(pattern, text)
    if match:
        return int(match.group(1))
    return None


def parse_stall_percent(text: str) -> Optional[float]:
    """
    Parse stall percentage from db_bench interval stats or compute from total time.
    Looks for: Stalls(count): ... , <percent>%
    Falls back to computing from stall_micros / total runtime.
    """
    # Try direct stall percentage
    pattern = r"Stalls\(count\):.*?([\d.]+)\s*%"
    match = re.search(pattern, text)
    if match:
        return float(match.group(1))

    # Fallback: compute from stall_micros and total seconds
    # Use the LAST time match to get the actual workload time (not prepopulation)
    stall_micros = parse_stall_micros(text)
    time_matches = list(re.finditer(r"([\d.]+)\s+seconds\s+\d+\s+operations", text))
    if stall_micros is not None and time_matches:
        total_seconds = float(time_matches[-1].group(1))
        if total_seconds > 0:
            return (stall_micros / 1_000_000) / total_seconds * 100
    return None


def _parse_stat_counter(text: str, stat_name: str) -> Optional[int]:
    """Parse a rocksdb statistics counter by name, e.g. 'rocksdb.bytes.read'."""
    pattern = re.escape(stat_name) + r"\s+COUNT\s*:\s*(\d+)"
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def parse_block_cache_hit_rate(text: str) -> Optional[float]:
    """Compute block cache hit rate: hits / (hits + misses)."""
    hits = _parse_stat_counter(text, "rocksdb.block.cache.hit")
    misses = _parse_stat_counter(text, "rocksdb.block.cache.miss")
    if hits is not None and misses is not None and (hits + misses) > 0:
        return round(hits / (hits + misses), 4)
    return None


def parse_io_bytes(text: str) -> dict[str, Optional[int]]:
    """Parse user-level bytes read and written."""
    return {
        "bytes_written": _parse_stat_counter(text, "rocksdb.bytes.written"),
        "bytes_read": _parse_stat_counter(text, "rocksdb.bytes.read"),
    }


def parse_compact_read_gb(text: str) -> Optional[float]:
    """Parse total compaction bytes read in GB."""
    val = _parse_stat_counter(text, "rocksdb.compact.read.bytes")
    return round(val / (1024 ** 3), 4) if val is not None else None


def parse_flush_write_gb(text: str) -> Optional[float]:
    """Parse total memtable flush bytes written in GB."""
    val = _parse_stat_counter(text, "rocksdb.flush.write.bytes")
    return round(val / (1024 ** 3), 4) if val is not None else None


def parse_db_bench_output(text: str) -> BenchmarkResult:
    """Parse complete db_bench output into a BenchmarkResult."""
    result = BenchmarkResult()

    # Throughput
    ops, micros, mb = parse_ops_line(text)
    result.ops_per_sec = ops
    result.micros_per_op = micros
    result.mb_per_sec = mb

    # Percentiles
    pcts = parse_percentiles(text)
    result.p50 = pcts["p50"]
    result.p75 = pcts["p75"]
    result.p99 = pcts["p99"]
    result.p99_9 = pcts["p99_9"]
    result.p99_99 = pcts["p99_99"]

    # Compaction / I/O
    result.write_amp = parse_write_amp(text)
    result.compaction_write_gb = parse_compaction_write_gb(text)
    result.compact_read_gb = parse_compact_read_gb(text)
    result.flush_write_gb = parse_flush_write_gb(text)
    io = parse_io_bytes(text)
    result.bytes_written = io["bytes_written"]
    result.bytes_read = io["bytes_read"]

    # Block cache
    result.block_cache_hit_rate = parse_block_cache_hit_rate(text)

    # Stalls
    result.stall_micros = parse_stall_micros(text)
    result.stall_percent = parse_stall_percent(text)

    return result
