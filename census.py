#!/usr/bin/env python3
"""
census.py — Measures how much real data has a short generator that LZMA cannot already exploit.

  scan      : samples a directory tree, runs every detector, and reports bytes saved beyond xz
  selfcheck : plants known generators to prove each detector fires, and that random data stays quiet

Detectors (none of them feed the compressor; this is measurement only):
  - AHPS language : POLY2 and 16-bit LCG blocks, using the real planner scoring
  - Word LCG      : full-state linear congruential streams of 16/32/64-bit words, any multiplier
  - GF(2) linear  : Berlekamp-Massey on bit lanes (LFSR, xorshift, xorshift128, Mersenne Twister)
  - Known tables  : generated constants such as CRC, AES, SHA and sine tables

Savings are measured, not estimated: each hit is blanked, the file is recompressed with xz, and only
the difference (less the cost of storing a program) counts. Scan also runs a holdout comparison of
AHPS against xz filters on files that were not used to tune it.

Blind spots: truncated LCGs other than AHPS's own (MSVC or glibc rand), PCG, ChaCha and AES-CTR.
"""

import argparse
import hashlib
import json
import lzma
import math
import multiprocessing
import os
import pathlib
import random
import struct
import sys
import time
import zlib
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

import ahps

BLOCK_LEN        = 4096
NEAR_RANDOM      = 0.97      # zlib -1 must leave a block at least this large to count as incompressible
BIT_WINDOW       = 1024      # Bytes per whole-stream Berlekamp-Massey window (8,192 bits)
LANE_SAMPLES     = 1024
LANE_WINDOW      = 16 * 1024 # Enough bytes for LANE_SAMPLES at the widest word
LANE_SPACING     = 48 * 1024 # Any generator stream of 64 KB or more contains a whole window
MAX_LANE_WINDOWS = 128       # Files with more than this are spaced out evenly instead
MT_SAMPLES       = 40_000    # Twice the 19,937-bit Mersenne Twister state, plus margin
MT_WINDOW        = MT_SAMPLES * 4
MT_SPACING       = 256 * 1024
MAX_MT_WINDOWS   = 4
LINEAR_MARGIN    = 32        # Random bits give L = n/2 +/- 2, so this margin has no false alarms
LCG_VERIFY_WORDS = 8

SCAN_CAP      = 16 * 1024 * 1024
HOLDOUT_CAP   = 4 * 1024 * 1024
MIN_FILE_LEN  = 16 * 1024
XZ_PRESET     = 6
GENERATOR_PROGRAM_BYTES = 8     # A stored seed or short recurrence
TABLE_PROGRAM_BYTES     = 64    # A small routine that rebuilds a table

SKIP_DIRS = {
    "users", "programdata", "home", "temp", "tmp", "prefetch", "winsxs", "softwaredistribution",
    "serviceprofiles", "logs", "winevt", "config", "$recycle.bin", "system volume information",
    "installer", "assembly", ".git", "__pycache__",
}
SKIP_EXTS = {".log", ".evtx", ".etl", ".dmp", ".tmp", ".lock", ".msp", ".wim", ".esd"}

# The files whose results chose the planner defaults, kept out of the holdout
TUNING_NAMES = {"ntdll.dll", "shell32.dll", "kernelbase.dll", "gcc.exe", "arial.ttf"}
TUNING_DIRS  = (os.sep + "media" + os.sep, os.sep + "lib" + os.sep + "python")

Hit = Tuple[str, int, int, str]  # (detector, offset, length, hex program params)


# =====================================================================
# 1. KNOWN GENERATED TABLES
# =====================================================================
def crc_table(poly: int, width: int, reflected: bool) -> List[int]:
    top, mask = 1 << (width - 1), (1 << width) - 1
    out = []
    for i in range(256):
        v = i if reflected else i << (width - 8)
        for _ in range(8):
            if reflected:
                v = (v >> 1) ^ poly if v & 1 else v >> 1
            else:
                v = ((v << 1) ^ poly) & mask if v & top else (v << 1) & mask
        out.append(v)
    return out


def slicing_tables(base: List[int], count: int) -> List[List[int]]:
    tables = [base]
    for _ in range(count - 1):
        prev = tables[-1]
        tables.append([(prev[i] >> 8) ^ base[prev[i] & 0xFF] for i in range(256)])
    return tables


def gf_tables(poly: int, generator: int) -> Tuple[bytes, bytes]:
    def mul(a: int, b: int) -> int:
        r = 0
        while b:
            if b & 1:
                r ^= a
            a <<= 1
            if a & 0x100:
                a ^= poly
            b >>= 1
        return r
    exp, log, x = [0] * 256, [0] * 256, 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = mul(x, generator)
    return bytes(exp), bytes(log)


def aes_sboxes() -> Tuple[bytes, bytes]:
    exp, log = gf_tables(0x11B, 3)
    sbox, inv = [0] * 256, [0] * 256
    for i in range(256):
        q = exp[(255 - log[i]) % 255] if i else 0
        v = q
        for k in range(1, 5):
            v ^= ((q << k) | (q >> (8 - k))) & 0xFF
        sbox[i] = v ^ 0x63
        inv[sbox[i]] = i
    return bytes(sbox), bytes(inv)


def first_primes(n: int) -> List[int]:
    primes: List[int] = []
    c = 2
    while len(primes) < n:
        if all(c % p for p in primes):
            primes.append(c)
        c += 1
    return primes


def iroot(n: int, k: int) -> int:
    lo, hi = 0, 1 << (n.bit_length() // k + 1)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid ** k <= n:
            lo = mid
        else:
            hi = mid - 1
    return lo


def sha_constants(count: int, word_bits: int) -> List[int]:
    mask = (1 << word_bits) - 1
    return [iroot(p << (3 * word_bits), 3) & mask for p in first_primes(count)]


def pi_hex_bytes(n: int) -> bytes:
    """Fractional bytes of pi by Machin's formula, as used for the Blowfish P-array and S-boxes."""
    bits = n * 8 + 64
    def arctan_inv(x: int) -> int:
        total = term = (1 << bits) // x
        k, sign = 3, -1
        while term:
            term //= x * x
            total += sign * (term // k)
            k, sign = k + 2, -sign
        return total
    pi = 4 * (4 * arctan_inv(5) - arctan_inv(239))
    frac = pi & ((1 << bits) - 1)
    return (frac >> 64).to_bytes(n, "big")


def pack_words(values: Iterable[int], fmt: str) -> bytes:
    return b"".join(struct.pack(fmt, v) for v in values)


def known_tables() -> Dict[str, bytes]:
    tables: Dict[str, bytes] = {}

    def add_words(name: str, values: List[int], size: int) -> None:
        code = {2: "H", 4: "I", 8: "Q"}[size]
        tables[name + "-le"] = pack_words(values, "<" + code)
        tables[name + "-be"] = pack_words(values, ">" + code)

    crc32 = crc_table(0xEDB88320, 32, True)
    for i, table in enumerate(slicing_tables(crc32, 8)):
        add_words(f"crc32-slice{i}", table, 4)
    add_words("crc32c", crc_table(0x82F63B78, 32, True), 4)
    add_words("crc32-mpeg", crc_table(0x04C11DB7, 32, False), 4)
    add_words("crc64-ecma", crc_table(0x42F0E1EBA9EA3693, 64, False), 8)
    add_words("crc64-xz", crc_table(0xC96C5795D7870F42, 64, True), 8)
    add_words("crc16-ibm", crc_table(0xA001, 16, True), 2)
    add_words("crc16-ccitt", crc_table(0x1021, 16, False), 2)
    add_words("crc16-kermit", crc_table(0x8408, 16, True), 2)

    tables["aes-sbox"], tables["aes-inv-sbox"] = aes_sboxes()
    tables["gf256-exp-11b"], tables["gf256-log-11b"] = gf_tables(0x11B, 3)
    tables["gf256-exp-11d"], tables["gf256-log-11d"] = gf_tables(0x11D, 2)

    add_words("md5-k", [int(abs(math.sin(i + 1)) * (1 << 32)) & 0xFFFFFFFF for i in range(64)], 4)
    add_words("sha256-k", sha_constants(64, 32), 4)
    add_words("sha512-k", sha_constants(80, 64), 8)

    pi = pi_hex_bytes(18 * 4 + 1024 * 4)
    add_words("blowfish-pi", [int.from_bytes(pi[i:i + 4], "big") for i in range(0, len(pi), 4)], 4)

    for period in (256, 512, 1024, 4096):
        for label, count in (("full", period), ("quarter", period // 4 + 1)):
            sines = [math.sin(2 * math.pi * i / period) for i in range(count)]
            tables[f"sin{period}-{label}-f32"] = struct.pack(f"<{count}f", *sines)
            tables[f"sin{period}-{label}-f64"] = struct.pack(f"<{count}d", *sines)
            tables[f"sin{period}-{label}-q15"] = struct.pack(f"<{count}h", *[round(v * 32767) for v in sines])
    return tables


TABLES: Dict[str, bytes] = {}


def find_tables(data: bytes) -> List[Hit]:
    hits: List[Hit] = []
    for name, table in TABLES.items():
        pos = data.find(table)
        while pos >= 0:
            hits.append((name, pos, len(table), ""))
            pos = data.find(table, pos + len(table))
    return hits


# =====================================================================
# 2. GENERATOR DETECTORS
# =====================================================================
def linear_complexity(bits: Iterable[int]) -> int:
    """Berlekamp-Massey over GF(2) with big-int polynomials: bit i of `conn` weighs s[n - i]."""
    conn, prev, length, gap, hist = 1, 1, 0, 1, 0
    for n, bit in enumerate(bits):
        hist = (hist << 1) | bit
        if (conn & hist).bit_count() & 1:
            old = conn
            conn ^= prev << gap
            if 2 * length <= n:
                length, prev, gap = n + 1 - length, old, 1
            else:
                gap += 1
        else:
            gap += 1
    return length


def stream_bits(window: bytes) -> List[int]:
    text = bin(int.from_bytes(b"\x01" + window, "big"))[3:]
    return [1 if ch == "1" else 0 for ch in text]


def is_linear(bits: List[int]) -> bool:
    return linear_complexity(bits) <= len(bits) // 2 - LINEAR_MARGIN


def lane_bits(data: bytes, start: int, word: int, byte: int, bit: int, samples: int) -> Optional[List[int]]:
    end = start + byte + word * samples
    if end > len(data):
        return None
    return [(b >> bit) & 1 for b in data[start + byte:end:word]]


def all_lanes_linear(data: bytes, start: int, word: int, samples: int) -> bool:
    """Only a stream with every bit lane linear is generated; one structured lane proves nothing."""
    for byte in range(word):
        for bit in range(8):
            bits = lane_bits(data, start, word, byte, bit, samples)
            if bits is None or not is_linear(bits):
                return False
    return True


def linear_window_hit(data: bytes, start: int) -> Optional[str]:
    if is_linear(stream_bits(data[start:start + BIT_WINDOW])):
        return "gf2-stream"
    for word in (2, 4, 8):
        if all_lanes_linear(data, start, word, LANE_SAMPLES):
            return f"gf2-lane{word * 8}"
    return None


def word_lcg_hit(block: bytes) -> Optional[Tuple[str, int, int]]:
    """
    Solves x[n+1] = a * x[n] + c for full-state words and checks the following words agree.
    Returns (name, offset, verified length). The words must be distinct, since a short repeating
    pattern also satisfies a recurrence and LZMA already handles those.
    """
    for size, code in ((2, "H"), (4, "I"), (8, "Q")):
        mod = 1 << (size * 8)
        need = LCG_VERIFY_WORDS + 3
        for order in "<>":
            for off in range(size):
                if off + need * size > len(block):
                    continue
                count = (len(block) - off) // size
                x = struct.unpack_from(f"{order}{count}{code}", block, off)
                step = (x[1] - x[0]) % mod
                if not step & 1:
                    continue
                a = (x[2] - x[1]) * pow(step, -1, mod) % mod
                c = (x[1] - a * x[0]) % mod
                verified = 1
                while verified < count and (a * x[verified - 1] + c) % mod == x[verified]:
                    verified += 1
                if a > 1 and verified >= need and len(set(x[:need])) == need:
                    return f"word-lcg{size * 8}", off, verified * size
    return None


def incompressible_runs(data: bytes) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    start = -1
    for off in range(0, len(data) - BLOCK_LEN + 1, BLOCK_LEN):
        block = data[off:off + BLOCK_LEN]
        if len(zlib.compress(block, 1)) >= BLOCK_LEN * NEAR_RANDOM:
            if start < 0:
                start = off
        elif start >= 0:
            runs.append((start, off - start))
            start = -1
    if start >= 0:
        runs.append((start, len(data) // BLOCK_LEN * BLOCK_LEN - start))
    return runs


def ahps_language_hits(data: bytes) -> List[Hit]:
    if ahps.SIMD_BRIDGE.lib is None:
        raise RuntimeError("census.py needs the AHPS C kernel for block scoring.")
    block = 1024
    costs, local = ahps.SIMD_BRIDGE.score_all(data, block)
    hits: List[Hit] = []
    for idx in range(len(local) // 5):
        op = local[idx * 5]
        if ahps.is_generator_hit(costs, idx, op):
            name = "ahps-prng" if op == ahps.OP_XORSHIFT16 else "ahps-poly2"
            off = idx * block
            hits.append((name, off, min(block, len(data) - off), local[idx * 5 + 1:idx * 5 + 5].hex()))
    return hits


def window_starts(runs: List[Tuple[int, int]], length: int, spacing: int, limit: int) -> List[int]:
    starts = [off for start, run in runs for off in range(start, start + run - length + 1, spacing)]
    if len(starts) > limit:
        starts = [starts[i * len(starts) // limit] for i in range(limit)]
    return starts


def scan_generators(data: bytes) -> Tuple[List[Hit], int]:
    """Runs every detector and returns (hits, incompressible bytes)."""
    hits = ahps_language_hits(data)
    runs = incompressible_runs(data)
    near_random = sum(length for _, length in runs)

    for start, length in runs:
        for off in range(start, start + length, BLOCK_LEN):
            found = word_lcg_hit(data[off:off + BLOCK_LEN])
            if found:
                hits.append((found[0], off + found[1], found[2], ""))

    # Window hits only claim the bytes that were actually tested
    for off in window_starts(runs, LANE_WINDOW, LANE_SPACING, MAX_LANE_WINDOWS):
        name = linear_window_hit(data, off)
        if name:
            hits.append((name, off, LANE_WINDOW, ""))

    for off in window_starts(runs, MT_WINDOW, MT_SPACING, MAX_MT_WINDOWS):
        if all_lanes_linear(data, off, 4, MT_SAMPLES):
            hits.append(("gf2-mt19937", off, MT_WINDOW, ""))
    return hits, near_random


def xz_size(data: bytes) -> int:
    return len(lzma.compress(data, format=lzma.FORMAT_XZ, preset=XZ_PRESET))


def hit_residual(name: str, params: str, piece: bytes) -> bytes:
    """What a program leaves behind: AHPS programs keep their real residual, exact matches leave zeros."""
    if name == "ahps-prng":
        return ahps.SIMD_BRIDGE.lift(ahps.OP_XORSHIFT16, bytes.fromhex(params), piece, False)
    if name == "ahps-poly2":
        return ahps.SIMD_BRIDGE.lift(ahps.OP_POLY2, bytes.fromhex(params), piece, False)
    return bytes(len(piece))


def measured_saving(data: bytes, hits: List[Hit]) -> Dict[str, int]:
    """Applies each detector's hits in turn and returns what xz could not already remove."""
    saved: Dict[str, int] = {}
    base = xz_size(data)
    groups: Dict[str, List[Hit]] = defaultdict(list)
    for hit in hits:
        groups[hit[0]].append(hit)
    for name, regions in groups.items():
        rewritten = bytearray(data)
        for _, off, length, params in regions:
            rewritten[off:off + length] = hit_residual(name, params, data[off:off + length])
        program = TABLE_PROGRAM_BYTES if name.split("-")[0] not in ("ahps", "word", "gf2") else GENERATOR_PROGRAM_BYTES
        # xz output jitters by kilobytes on large files, but a program cannot save more than it replaces
        covered = sum(hit[2] for hit in regions)
        saved[name] = min(covered, base - xz_size(bytes(rewritten))) - program * len(regions)
    return saved


# =====================================================================
# 3. CORPUS SAMPLING
# =====================================================================
def walk_corpus(roots: List[str]) -> Dict[str, List[Tuple[str, int]]]:
    by_ext: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for root in roots:
        for folder, dirs, names in os.walk(root, onerror=lambda _: None):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for name in names:
                ext = pathlib.Path(name).suffix.lower()
                if ext in SKIP_EXTS:
                    continue
                path = os.path.join(folder, name)
                try:
                    if os.path.islink(path):
                        continue
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size >= MIN_FILE_LEN:
                    by_ext[ext or "(none)"].append((path, size))
    return by_ext


def sample_corpus(by_ext: Dict[str, List[Tuple[str, int]]], budget: int, rng: random.Random) -> List[Tuple[str, int]]:
    """Round-robin across extensions by bytes, so a few huge formats cannot dominate the sample."""
    queues = {}
    for ext, files in sorted(by_ext.items()):
        files = sorted(files)
        rng.shuffle(files)
        queues[ext] = files
    chosen: List[Tuple[str, int]] = []
    total = 0
    while total < budget and queues:
        for ext in sorted(queues):
            taken = 0
            files = queues[ext]
            while files and taken < 4 * 1024 * 1024:
                item = files.pop()
                chosen.append(item)
                taken += min(item[1], SCAN_CAP)
            total += taken
            if not files:
                del queues[ext]
            if total >= budget:
                break
    return chosen


def is_tuning_file(path: str) -> bool:
    lowered = path.lower()
    return pathlib.Path(lowered).name in TUNING_NAMES or any(d in lowered for d in TUNING_DIRS)


# =====================================================================
# 4. WORKERS
# =====================================================================
def init_worker(
    scan_cap: int = SCAN_CAP,
    holdout_cap: int = HOLDOUT_CAP,
    lane_windows: int = MAX_LANE_WINDOWS,
    mt_windows: int = MAX_MT_WINDOWS,
) -> None:
    global SCAN_CAP, HOLDOUT_CAP, MAX_LANE_WINDOWS, MAX_MT_WINDOWS
    SCAN_CAP, HOLDOUT_CAP, MAX_LANE_WINDOWS, MAX_MT_WINDOWS = scan_cap, holdout_cap, lane_windows, mt_windows
    TABLES.update(known_tables())


def read_head(path: str, cap: int) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read(cap)
    except OSError:
        return None


def scan_worker(item: Tuple[str, int]) -> Optional[dict]:
    path, size = item
    data = read_head(path, SCAN_CAP)
    if not data:
        return None
    hits, near_random = scan_generators(data)
    hits += find_tables(data)
    return {
        "path": path,
        "ext": pathlib.Path(path).suffix.lower() or "(none)",
        "digest": hashlib.blake2b(data, digest_size=16).hexdigest(),
        "bytes": len(data),
        "near_random": near_random,
        "xz1": len(lzma.compress(data, format=lzma.FORMAT_XZ, preset=1)),
        "hits": [list(hit) for hit in hits],
        "saved": measured_saving(data, hits) if hits else {},
    }


def holdout_worker(item: Tuple[str, int]) -> Optional[dict]:
    path, _ = item
    data = read_head(path, HOLDOUT_CAP)
    if not data:
        return None
    preset = 9 | lzma.PRESET_EXTREME

    def xz(pre: list) -> int:
        filters = pre + [{"id": lzma.FILTER_LZMA2, "preset": preset}]
        return len(lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters))

    plain = xz([])
    rivals = {"plain": plain, "bcj": xz([{"id": lzma.FILTER_X86}])}
    for dist in (1, 2, 4, 8, 16):
        rivals[f"delta{dist}"] = xz([{"id": lzma.FILTER_DELTA, "dist": dist}])
    comp, report = ahps.ahps_compress_bytes(data, lzma_preset=9)
    if ahps.ahps_decompress_bytes(comp) != data:
        raise RuntimeError(f"Round-trip failure on {path}")
    return {
        "path": path,
        "ext": pathlib.Path(path).suffix.lower() or "(none)",
        "bytes": len(data),
        "plain": plain,
        "best": min(rivals.values()),
        "best_filter": min(rivals, key=rivals.get),
        "ahps": len(comp),
        "top_program": max(report.programs.items(), key=lambda kv: kv[1][1])[0],
    }


# =====================================================================
# 5. REPORTING
# =====================================================================
def summarize_scan(results: List[dict]) -> dict:
    seen = set()
    unique = []
    for row in results:
        if row["digest"] not in seen:
            seen.add(row["digest"])
            unique.append(row)

    total = sum(r["bytes"] for r in unique)
    xz_total = sum(r["xz1"] for r in unique)
    detectors: Dict[str, dict] = defaultdict(lambda: {"files": 0, "regions": 0, "hit_bytes": 0, "saved": 0})
    formats: Dict[str, dict] = defaultdict(lambda: {"files": 0, "bytes": 0, "xz1": 0, "near_random": 0, "saved": 0})
    for row in unique:
        fmt = formats[row["ext"]]
        fmt["files"] += 1
        fmt["bytes"] += row["bytes"]
        fmt["xz1"] += row["xz1"]
        fmt["near_random"] += row["near_random"]
        fmt["saved"] += sum(max(0, v) for v in row["saved"].values())
        names = {hit[0] for hit in row["hits"]}
        for name in names:
            detectors[name]["files"] += 1
            detectors[name]["saved"] += max(0, row["saved"].get(name, 0))
        for name, _, length, _ in row["hits"]:
            detectors[name]["regions"] += 1
            detectors[name]["hit_bytes"] += length
    return {
        "files": len(unique),
        "duplicates": len(results) - len(unique),
        "bytes": total,
        "xz1_bytes": xz_total,
        "near_random_bytes": sum(r["near_random"] for r in unique),
        "saved_bytes": sum(sum(max(0, v) for v in r["saved"].values()) for r in unique),
        "detectors": dict(detectors),
        "formats": dict(formats),
    }


def print_scan(s: dict) -> None:
    print(f"\nCENSUS: {s['files']:,} unique files, {s['bytes'] / 1e9:.2f} GB scanned ({s['duplicates']:,} duplicates dropped)")
    print(f"  Near-random bytes : {s['near_random_bytes'] / 1e9:.2f} GB ({s['near_random_bytes'] / max(1, s['bytes']) * 100:.1f}%)")
    print(f"  xz -1 baseline    : {s['xz1_bytes'] / 1e9:.2f} GB")
    pct = s["saved_bytes"] / max(1, s["xz1_bytes"]) * 100
    print(f"  Saved beyond xz   : {s['saved_bytes']:,} bytes ({pct:.4f}% of the baseline)")
    print(f"\n  {'detector':26s}{'files':>7s}{'regions':>9s}{'hit bytes':>14s}{'saved':>12s}")
    for name, d in sorted(s["detectors"].items(), key=lambda kv: -kv[1]["saved"]):
        print(f"  {name:26s}{d['files']:>7,}{d['regions']:>9,}{d['hit_bytes']:>14,}{d['saved']:>12,}")
    print(f"\n  {'format':14s}{'files':>7s}{'MB':>10s}{'random %':>10s}{'saved':>12s}{'% of xz':>10s}")
    rows = sorted(s["formats"].items(), key=lambda kv: -kv[1]["bytes"])[:25]
    for ext, f in rows:
        print(f"  {ext[:13]:14s}{f['files']:>7,}{f['bytes'] / 1e6:>10.1f}{f['near_random'] / max(1, f['bytes']) * 100:>10.1f}"
              f"{f['saved']:>12,}{f['saved'] / max(1, f['xz1']) * 100:>10.3f}")


def print_holdout(rows: List[dict]) -> None:
    if not rows:
        return
    def pct(a: int, b: int) -> float:
        return (a - b) / max(1, b) * 100
    vs_plain = sorted(pct(r["ahps"], r["plain"]) for r in rows)
    vs_best = sorted(pct(r["ahps"], r["best"]) for r in rows)
    total = {k: sum(r[k] for r in rows) for k in ("bytes", "plain", "best", "ahps")}
    wins = sum(r["ahps"] < r["best"] for r in rows)
    print(f"\nHOLDOUT: {len(rows)} unseen files, {total['bytes'] / 1e6:.1f} MB")
    print(f"  Totals            : plain xz {total['plain']:,} | best xz filter per file {total['best']:,} | AHPS {total['ahps']:,}")
    print(f"  AHPS total vs xz  : {pct(total['ahps'], total['plain']):+.2f}% plain, {pct(total['ahps'], total['best']):+.2f}% best filter")
    print(f"  Median vs plain   : {vs_plain[len(rows) // 2]:+.2f}%")
    print(f"  Median vs best    : {vs_best[len(rows) // 2]:+.2f}%  (beat the best filter on {wins} of {len(rows)})")
    print(f"  Range vs best     : {vs_best[0]:+.2f}% to {vs_best[-1]:+.2f}%")
    print(f"\n  {'worst losses':34s}{'bytes':>11s}{'best xz':>11s}{'AHPS':>11s}{'%':>8s}  top program")
    for r in sorted(rows, key=lambda r: -pct(r["ahps"], r["best"]))[:6]:
        print(f"  {pathlib.Path(r['path']).name[:33]:34s}{r['bytes']:>11,}{r['best']:>11,}{r['ahps']:>11,}"
              f"{pct(r['ahps'], r['best']):>+8.2f}  {r['top_program']}")


# =====================================================================
# 6. SELF-CHECK (POSITIVE AND NEGATIVE CONTROLS)
# =====================================================================
def plant_lcg16(rng: random.Random, n: int) -> bytes:
    state, out = rng.randrange(1, 65536), bytearray()
    for _ in range(n):
        state = (state * ahps.LCG_MULT + ahps.LCG_INC) & 0xFFFF
        out.append(state >> 8)
    return bytes(out)


def plant_words(step, seed: int, n: int, size: int) -> bytes:
    out, state = bytearray(), seed
    while len(out) < n:
        state = step(state)
        out += state.to_bytes(size, "little")
    return bytes(out[:n])


def xorshift32(x: int) -> int:
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17
    return x ^ ((x << 5) & 0xFFFFFFFF)


def xorshift64(x: int) -> int:
    x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 7
    return x ^ ((x << 17) & 0xFFFFFFFFFFFFFFFF)


def lfsr_stream(rng: random.Random, n: int) -> bytes:
    state, bits = rng.getrandbits(64) | 1, 0
    out = bytearray()
    for _ in range(n):
        byte = 0
        for _ in range(8):
            bit = ((state >> 63) ^ (state >> 62) ^ (state >> 60) ^ (state >> 59)) & 1
            state = ((state << 1) | bit) & 0xFFFFFFFFFFFFFFFF
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def selfcheck() -> bool:
    if not TABLES:
        init_worker()
    rng = random.Random(1234)
    host = rng.randbytes(64 * 1024)
    n = 512 * 1024
    planted = {
        "ahps-prng": plant_lcg16(rng, 64 * 1024),
        "word-lcg32": plant_words(lambda x: (x * 214013 + 2531011) & 0xFFFFFFFF, 12345, 64 * 1024, 4),
        "word-lcg64": plant_words(lambda x: (x * 6364136223846793005 + 1442695040888963407) & (2 ** 64 - 1), 99, 64 * 1024, 8),
        "gf2 (xorshift32)": plant_words(xorshift32, 2463534242, 64 * 1024, 4),
        "gf2 (xorshift64)": plant_words(xorshift64, 88172645463325252, 64 * 1024, 8),
        "gf2 (lfsr64)": lfsr_stream(rng, 64 * 1024),
        "gf2-mt19937": random.Random(7).randbytes(n),
    }
    ok = True
    for expected, stream in planted.items():
        data = host + stream + host
        names = {hit[0] for hit in scan_generators(data)[0]}
        # The linear family may fire on whichever lane catches it first
        fired = expected in names or (expected.startswith("gf2 ") and any(n.startswith("gf2") for n in names))
        print(f"  [{'ok' if fired else 'MISSED'}] planted {expected:18s} -> {sorted(names)}")
        ok = ok and fired

    poly = bytes((37 * i * i + 11 * i + 5) & 0xFF for i in range(4096))
    names = {hit[0] for hit in ahps_language_hits(host + poly + host)}
    print(f"  [{'ok' if 'ahps-poly2' in names else 'MISSED'}] planted POLY2 -> {sorted(names)}")
    ok = ok and "ahps-poly2" in names

    for name in ("crc32-slice0-le", "aes-sbox", "sha256-k-be", "blowfish-pi-be", "sin1024-full-f32", "crc64-xz-le"):
        hits = find_tables(host[:1024] + TABLES[name] + host[:1024])
        fired = any(hit[0] == name for hit in hits)
        print(f"  [{'ok' if fired else 'MISSED'}] planted table {name}")
        ok = ok and fired

    fixed = {
        "crc32 table entry 1": struct.unpack_from("<I", TABLES["crc32-slice0-le"], 4)[0] == 0x77073096,
        "aes sbox prefix": TABLES["aes-sbox"][:4] == bytes([0x63, 0x7C, 0x77, 0x7B]),
        "sha256 K[0]": sha_constants(64, 32)[0] == 0x428A2F98,
        "sha512 K[0]": sha_constants(80, 64)[0] == 0x428A2F98D728AE22,
        "md5 K[0]": struct.unpack_from("<I", TABLES["md5-k-le"], 0)[0] == 0xD76AA478,
        "pi P[0]": struct.unpack_from(">I", TABLES["blowfish-pi-be"], 0)[0] in (0x243F6A88, 0x88 << 24 | 0x6A3F24),
    }
    for label, passed in fixed.items():
        print(f"  [{'ok' if passed else 'WRONG'}] reference value: {label}")
        ok = ok and passed

    # Each of these fooled an earlier version of the detectors on real files
    # os.urandom, because Python's own randbytes is Mersenne Twister and rightly fires
    one_lane = bytearray(os.urandom(1024 * 1024))
    one_lane[::4] = bytes(b & 0xFE for b in one_lane[::4])
    pattern = (rng.randbytes(16) * 2800)[:40960]
    near_zero = bytearray(1024 * 8)
    near_zero[::97] = bytes(len(near_zero[::97]))

    noise = {
        "os.urandom": os.urandom(4 * 1024 * 1024),
        "deflate output": zlib.compress(random.Random(9).randbytes(4 * 1024 * 1024) + bytes(1024), 9),
        "lzma output": lzma.compress(b"".join(str(i).encode() * 40 for i in range(200_000))),
        "random with one constant bit lane": bytes(one_lane),
        "repeating 16-byte pattern": host[:8192] + pattern + host[:8192],
        "all-zero blocks": bytes(near_zero),
    }
    for label, data in noise.items():
        hits, near_random = scan_generators(data)
        print(f"  [{'ok' if not hits else 'FALSE ALARM'}] control {label}: {len(data):,} bytes, "
              f"{near_random:,} near-random, hits {hits[:3]}")
        ok = ok and not hits
    return ok


# =====================================================================
# 7. CLI
# =====================================================================
def cmd_scan(args: argparse.Namespace) -> int:
    limits = (args.scan_cap_mb << 20, args.holdout_cap_mb << 20, args.lane_windows, args.mt_windows)
    init_worker(*limits)
    rng = random.Random(args.seed)
    print(f"[+] Walking {len(args.roots)} roots...", flush=True)
    by_ext = walk_corpus(args.roots)
    print(f"[+] Found {sum(len(v) for v in by_ext.values()):,} files across {len(by_ext):,} extensions", flush=True)
    chosen = sample_corpus(by_ext, int(args.budget_gb * 1e9), rng)

    holdout_pool = [item for files in by_ext.values() for item in files if not is_tuning_file(item[0])]
    holdout_pool = sorted(item for item in holdout_pool if item[1] >= 64 * 1024)
    holdout = rng.sample(holdout_pool, min(args.holdout_files, len(holdout_pool)))
    print(f"[+] Scanning {len(chosen):,} files, holdout {len(holdout)} files, {args.workers} workers", flush=True)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with multiprocessing.Pool(args.workers, initializer=init_worker, initargs=limits) as pool:
        scan_rows = []
        chunksize = max(1, len(chosen) // (args.workers * 64))
        for i, row in enumerate(pool.imap_unordered(scan_worker, chosen, chunksize=chunksize)):
            if row:
                scan_rows.append(row)
            if i and i % 1000 == 0:
                print(f"    scanned {i:,} files in {time.perf_counter() - t0:.0f}s", flush=True)
        print(f"[+] Census finished in {time.perf_counter() - t0:.0f}s", flush=True)
        t1 = time.perf_counter()
        hold_rows = [row for row in pool.imap_unordered(holdout_worker, holdout) if row]
        print(f"[+] Holdout finished in {time.perf_counter() - t1:.0f}s", flush=True)

    summary = summarize_scan(scan_rows)
    (out_dir / "census.json").write_text(json.dumps({"summary": summary, "files": scan_rows, "holdout": hold_rows}), encoding="utf-8")
    print_scan(summary)
    print_holdout(hold_rows)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AHPS generator census: how much real data has a short generator?")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Sample a directory tree and measure savings beyond xz")
    p_scan.add_argument("roots", nargs="+", help="Directories to sample")
    p_scan.add_argument("--budget-gb", type=float, default=4.0, help="Bytes to scan, in GB (default: 4)")
    p_scan.add_argument("--holdout-files", type=int, default=120, help="Unseen files for the AHPS holdout (default: 120)")
    p_scan.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2), help="Worker processes")
    p_scan.add_argument("--scan-cap-mb", type=int, default=SCAN_CAP >> 20, help="Bytes read per file by the detectors")
    p_scan.add_argument("--holdout-cap-mb", type=int, default=HOLDOUT_CAP >> 20, help="Bytes read per file by the holdout")
    p_scan.add_argument("--lane-windows", type=int, default=MAX_LANE_WINDOWS, help="Most GF(2) windows per file")
    p_scan.add_argument("--mt-windows", type=int, default=MAX_MT_WINDOWS, help="Most Mersenne Twister windows per file")
    p_scan.add_argument("--seed", type=int, default=2026, help="Sampling seed (default: 2026)")
    p_scan.add_argument("--out", default="census_out", help="Output directory for census.json")
    p_scan.set_defaults(func=cmd_scan)

    p_check = sub.add_parser("selfcheck", help="Prove the detectors with planted generators and random controls")
    p_check.set_defaults(func=lambda _: 0 if selfcheck() else 1)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
