#!/usr/bin/env python3
"""
ahps.py - Asymmetric Hybrid Program Synthesis (AHPS-Bin v2.6) CLI
The archive format is specified in docs/format.md.
Optional --csv uses byte-exact column grouping in AHPS\\x06 archives.
Upgrades over v2.5:
- AHPS\\x05 archives, since two EXE_SPLIT meanings shipped under AHPS\\x04 and ABI 205
- Decoder limits on LZMA memory, decoded container size and restored size
- CPU check before the native library loads
- Per-chunk plan selection on files > 16 MiB
Upgrades over v2.4:
- OP_EXE_SPLIT (0x09): Length-preserving BCJ2-style segregated call/jump target plane lifter
- ABI version handshake & safe script-dir DLL loading
- Viterbi t=0 SLOT_LOCAL switch-cost initialization fix
- Zero-block v2.3 ctypes.byref guard
- Symmetric -p 9 (PRESET_EXTREME) benchmark fairness for xz
- Non-zero exit code on SHA-256 verification failure
"""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import io
import lzma
import math
import os
import pathlib
import platform
import re
import struct
import subprocess
import sys
import time
import uuid
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Callable, Deque, Dict, List, Optional, Tuple, Union

from ahps_csv import MAGIC as MAGIC_CSV, pack_csv_container, restore_csv_container
from ahps_safetensors import (
    MAGIC as MAGIC_TENSORS, MAX_CHUNK_BYTES, TensorLayout, parse_layout,
    pack_tensor_container, restore_tensor_container,
)
from ahps_progress import TerminalProgress

ProgressCallback = Optional[Callable[[float, str], None]]


def progress_range(progress: ProgressCallback, start: float, end: float) -> ProgressCallback:
    """Map completed work in a substage without changing its processing granularity."""
    if progress is None:
        return None
    return lambda fraction, stage: progress(start + (end - start) * fraction, stage)

# =====================================================================
# 1. CONSTANTS & MICRO-VM OPCODE DEFINITIONS
# =====================================================================
OP_RAW         = 0x00
OP_POLY2       = 0x01
OP_DELTA1_LANE = 0x02
OP_DELTA2_LANE = 0x03
OP_EXE_REL32   = 0x04
OP_XORSHIFT16  = 0x05
OP_TRANSPOSE4  = 0x06
OP_SPLIT       = 0x07
OP_EXE32       = 0x08
OP_EXE_SPLIT   = 0x09

OPCODE_NAMES = {
    OP_POLY2:       "POLY2",
    OP_DELTA1_LANE: "DELTA1",
    OP_DELTA2_LANE: "DELTA2",
    OP_XORSHIFT16:  "PRNG",
    OP_TRANSPOSE4:  "TRANSPOSE4",
    OP_SPLIT:       "SPLIT",
    OP_EXE32:       "EXE32",
    OP_EXE_SPLIT:   "EXE_SPLIT",
}

EXPECTED_ABI_VERSION = 206
MAGIC_HEADER    = b"AHPS\x05"
MAGIC_HEADER_V4 = b"AHPS\x04"
MAGIC_HEADER_V3 = b"AHPS\x03"
LCG_MULT     = 25173
LCG_INC      = 13849
LCG_INV_MULT = 35581  # Exact: (25173 * 35581) % 65536 == 1
MAX_BLOCK_SIZE = 0x7FFFFFFF  # The C kernel takes block_size as a signed 32-bit int

V4_HEADER_LEN     = 25        # [Magic 5B][NumSegments 4B][TableLen 4B][TotalLen 8B][CRC32 4B]
MAX_SEGMENT_LEN   = 1 << 30   # The C lifts take lengths as a signed 32-bit int
MAX_PROGRAM_STEPS = 3
STEP_OPCODES      = frozenset(OPCODE_NAMES)

# A program is a list of (opcode, 4-byte params) steps; the empty program stores bytes unchanged
Program = List[Tuple[int, bytes]]

# Planner slot layout, mirrored by the SLOT_* constants in ahps_simd.c
STRIDES = (1, 2, 4, 8, 12, 16, 24, 32)
SLOT_RAW, SLOT_EXE, SLOT_DELTA1 = 0, 1, 2
SLOT_DELTA2 = SLOT_DELTA1 + len(STRIDES)
SLOT_LOCAL  = SLOT_DELTA2 + len(STRIDES)
NUM_SLOTS   = SLOT_LOCAL + 1
COST_NONE   = 1e300

SWITCH_BITS           = 4096         # Estimated cost of changing program between adjacent blocks
GENERATOR_SWITCH_BITS = 64           # What a near-perfect POLY2 or PRNG program costs to store
GENERATOR_MARGIN      = 4            # A generator hit must shrink a block at least this many times
CHAIN_MIN_LEN         = 4 * 1024     # Allow 4 KiB+ segments to refine DELTA->SPLIT & EXE chains
CHAIN_SAMPLE_LEN      = 128 * 1024
EXTREME_LIMIT         = 16 * 1024 * 1024  # Above this, the fast preset applies unless -p asks otherwise
PLAN_CHUNK_LEN        = 15 * 1024 * 1024  # Above EXTREME_LIMIT, each chunk of this size picks its own plan
CSV_PARALLEL_MIN_LEN  = 1024 * 1024       # Amortize the extra worker over at least 1 MiB

X86_NEAR_CALL     = re.compile(rb"\xE8...[\x00\xFF]", re.DOTALL)  # CALL rel32 within +/-16 MB
X86_CALLS_PER_MIB = 128        # Random bytes give about 30, so a false alarm costs one LZMA trial

DECODE_STEP = 1024 * 1024      # Decoded bytes between limit checks
STREAM_READ_STEP = 64 * 1024   # Compressed bytes read at a time from an archive file
STREAM_RAW_STEP = 1024 * 1024  # RAW bytes restored between writes

SCORE_EXE32, SCORE_EXE_SPLIT = 1, 2  # exe_flags bits, mirrored in ahps_simd.c

PF_AVX2_INSTRUCTIONS_AVAILABLE = 40  # Windows IsProcessorFeaturePresent id


@dataclass(frozen=True)
class DecodeLimits:
    max_dict_bytes: int = 256 * 1024 * 1024
    max_container_bytes: int = (4 << 30) + 1024 * 1024
    max_output_bytes: int = 4 << 30

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.max_dict_bytes, self.max_container_bytes, self.max_output_bytes)):
            raise TypeError("Decoder limits must be integers.")
        if self.max_dict_bytes <= 0 or self.max_container_bytes < 0 or self.max_output_bytes < 0:
            raise ValueError("Decoder limits must not be negative, and the LZMA memory limit must be positive.")


DEFAULT_LIMITS = DecodeLimits()


@dataclass(frozen=True)
class PlanFeatures:
    """Encoder search options for development experiments. Every setting writes a normal archive."""
    exe32: bool = True
    exe_split: bool = True
    delta_split: bool = True
    per_chunk: bool = True

    def __post_init__(self) -> None:
        if any(type(v) is not bool for v in (self.exe32, self.exe_split, self.delta_split, self.per_chunk)):
            raise TypeError("Plan features must be booleans.")

    @property
    def exe_flags(self) -> int:
        return (SCORE_EXE32 if self.exe32 else 0) | (SCORE_EXE_SPLIT if self.exe_split else 0)


DEFAULT_FEATURES = PlanFeatures()


@dataclass(frozen=True)
class SearchMethod:
    """How chunked mode ranks each chunk's plans. It never changes the final preset or the whole-file raw trial."""
    chunk_trial_preset: Optional[int] = None  # Cap on the trial level; None uses the final preset
    exe32_chunk_rival: bool = True            # Whether each chunk tries a whole-chunk EXE32 plan

    def __post_init__(self) -> None:
        if self.chunk_trial_preset is not None and type(self.chunk_trial_preset) is not int:
            raise TypeError("chunk_trial_preset must be an int or None.")
        if self.chunk_trial_preset is not None and not 0 <= self.chunk_trial_preset <= 9:
            raise ValueError("chunk_trial_preset must be between 0 and 9.")
        if type(self.exe32_chunk_rival) is not bool:
            raise TypeError("exe32_chunk_rival must be a boolean.")


EXHAUSTIVE = SearchMethod()
FAST_CHUNKS = SearchMethod(chunk_trial_preset=1, exe32_chunk_rival=False)
AUTO_SEARCH = object()  # Lets ahps_compress_bytes choose once the final preset is known


class DecodeLimitError(ValueError):
    """An archive needs more than the decoder's limits allow. It is not necessarily corrupt."""


# =====================================================================
# 2. CTYPES C/AVX2 KERNEL BRIDGE
# =====================================================================
c_uint8_p  = ctypes.POINTER(ctypes.c_uint8)

def cpu_supports_native() -> bool:
    """True only if this CPU is known to run the AVX2 code the native library is built with."""
    if platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    try:
        if sys.platform == "win32":
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(PF_AVX2_INSTRUCTIONS_AVAILABLE))
        if sys.platform == "darwin":
            return subprocess.run(
                ["sysctl", "-n", "hw.optional.avx2_0"], capture_output=True, text=True, check=True
            ).stdout.strip() == "1"
        flags = next(line for line in open("/proc/cpuinfo", encoding="utf-8") if line.startswith("flags"))
        return "avx2" in flags.split() and "popcnt" in flags.split()
    except (AttributeError, OSError, StopIteration, subprocess.SubprocessError, ValueError):
        return False


class NativeSIMDBridge:
    def __init__(self) -> None:
        self.lib: Optional[ctypes.CDLL] = None
        self.lib_path: str = "Pure Python Fallback"
        self._try_load()

    def _try_load(self) -> None:
        # Security hardening: only load shared libraries co-located with ahps.py (never untrusted CWD)
        base_dir = pathlib.Path(__file__).resolve().parent
        candidates = [
            base_dir / "ahps_simd.dll",
            base_dir / "libahps_simd.so",
            base_dir / "libahps_simd.dylib",
        ]
        for cand in candidates:
            if cand.exists():
                if not cpu_supports_native():
                    sys.stderr.write(f"[Warning] Skipping {cand.name}: AVX2 not confirmed on this CPU.\n")
                    return
                try:
                    lib = ctypes.CDLL(str(cand))

                    if not hasattr(lib, "ahps_abi_version"):
                        sys.stderr.write(
                            f"[Warning] Skipping {cand.name}: missing ahps_abi_version() symbol (please recompile).\n"
                        )
                        continue

                    lib.ahps_abi_version.argtypes = []
                    lib.ahps_abi_version.restype = ctypes.c_int
                    abi_ver = lib.ahps_abi_version()
                    if abi_ver != EXPECTED_ABI_VERSION:
                        sys.stderr.write(
                            f"[Warning] Skipping {cand.name}: ABI version {abi_ver} != expected {EXPECTED_ABI_VERSION}.\n"
                        )
                        continue

                    lib.ahps_init_tables.argtypes = []
                    lib.ahps_init_tables.restype = None

                    lib.ahps_score_buffer.argtypes = [
                        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                        ctypes.POINTER(ctypes.c_double), c_uint8_p,
                    ]
                    lib.ahps_score_buffer.restype = ctypes.c_int

                    if hasattr(lib, "ahps_score_buffer_ex"):
                        lib.ahps_score_buffer_ex.argtypes = [
                            ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_double), c_uint8_p,
                        ]
                        lib.ahps_score_buffer_ex.restype = ctypes.c_int

                    lib.ahps_estimate_mdl_bits.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_double]
                    lib.ahps_estimate_mdl_bits.restype = ctypes.c_double

                    for fn in (lib.ahps_forward_lift, lib.ahps_inverse_lift):
                        fn.argtypes = [ctypes.c_uint8, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
                        fn.restype = None

                    lib.ahps_unlift_entire_buffer.argtypes = [
                        c_uint8_p, c_uint8_p, ctypes.c_size_t,
                        ctypes.c_size_t, ctypes.c_int, c_uint8_p, c_uint8_p,
                    ]
                    lib.ahps_unlift_entire_buffer.restype = None

                    lib.ahps_init_tables()
                    self.lib = lib
                    self.lib_path = f"Native C/AVX2 ({cand.name} [ABI v{abi_ver}])"
                    return
                except Exception as exc:
                    sys.stderr.write(f"[Warning] Failed loading {cand.name}: {exc}\n")

    def score_all(
        self, data: bytes, block_size: int, exe_flags: int = SCORE_EXE32 | SCORE_EXE_SPLIT
    ) -> Tuple[List[float], bytes]:
        assert self.lib is not None
        num_blocks = (len(data) + block_size - 1) // block_size
        out_costs = (ctypes.c_double * (num_blocks * NUM_SLOTS))()
        out_local = (ctypes.c_uint8 * (num_blocks * 5))()
        if exe_flags == SCORE_EXE32 | SCORE_EXE_SPLIT:
            ok = self.lib.ahps_score_buffer(data, len(data), block_size, out_costs, out_local)
        elif hasattr(self.lib, "ahps_score_buffer_ex"):
            ok = self.lib.ahps_score_buffer_ex(data, len(data), block_size, exe_flags, out_costs, out_local)
        else:
            raise RuntimeError("This native library cannot score with x86 lifts disabled; please recompile.")
        if not ok:
            raise MemoryError("AHPS native scorer could not allocate its work buffer.")
        return list(out_costs), bytes(out_local)

    def lift(self, opcode: int, params: bytes, data: bytes, inverse: bool) -> bytes:
        assert self.lib is not None
        if len(params) < 4:
            params = params.ljust(4, b"\x00")
        out = ctypes.create_string_buffer(len(data))
        fn = self.lib.ahps_inverse_lift if inverse else self.lib.ahps_forward_lift
        fn(opcode, params, data, out, len(data))
        return out.raw

    def lift_into(self, opcode: int, params: bytes, src_buf: bytearray, offset: int, length: int, scratch: ctypes.Array, inverse: bool) -> None:
        assert self.lib is not None
        if opcode == OP_RAW or length == 0:
            return
        if len(params) < 4:
            params = params.ljust(4, b"\x00")
        params = bytes(params)
        c_src = (ctypes.c_char * length).from_buffer(src_buf, offset)
        fn = self.lib.ahps_inverse_lift if inverse else self.lib.ahps_forward_lift
        fn(opcode, params, c_src, scratch, length)
        ctypes.memmove(ctypes.byref(c_src), scratch, length)

    def estimate(self, data: bytes) -> float:
        assert self.lib is not None
        return self.lib.ahps_estimate_mdl_bits(data, len(data), 0.0)

    def unlift_all(
        self, raw_container: bytes, num_blocks: int, block_size: int, prm_len: int, total_len: int
    ) -> bytes:
        assert self.lib is not None
        if num_blocks == 0 or total_len == 0:
            return b""

        ops_start = 17
        prm_start = ops_start + num_blocks
        res_start = prm_start + prm_len

        c_buf = (ctypes.c_uint8 * len(raw_container)).from_buffer_copy(raw_container)
        out_data = (ctypes.c_uint8 * total_len)()

        p_ops = ctypes.cast(ctypes.byref(c_buf, ops_start), c_uint8_p)
        p_prm = ctypes.cast(ctypes.byref(c_buf, prm_start), c_uint8_p) if prm_len > 0 else c_uint8_p()
        p_res = ctypes.cast(ctypes.byref(c_buf, res_start), c_uint8_p)

        self.lib.ahps_unlift_entire_buffer(
            p_ops, p_prm, num_blocks, total_len, block_size, p_res, out_data
        )
        return bytes(out_data)


SIMD_BRIDGE = NativeSIMDBridge()


# =====================================================================
# 3. PURE PYTHON FALLBACK ENGINE (DECODES EVERYTHING THE C KERNEL WRITES)
# =====================================================================
_LOG2_TABLE = [0.0] + [i * math.log2(i) for i in range(1, 65537)]

def is_rip_rel32_prefix2(b0: int, b1: int) -> bool:
    if b0 == 0x0F and (0x80 <= b1 <= 0x8F):
        return True
    if b0 == 0xFF and b1 in (0x15, 0x25):
        return True
    if b0 in (0x8B, 0x8D) and (b1 & 0xC7) == 0x05:
        return True
    return False


def py_estimate_mdl_bits(data: bytes, overhead_bits: float = 0.0) -> float:
    n = len(data)
    if n == 0:
        return 0.0
    counts = [0] * 256
    zero_runs = 0
    prev_b = -1
    for b in data:
        counts[b] += 1
        if b == 0 and prev_b == 0:
            zero_runs += 1
        prev_b = b

    hash_val: List[Optional[int]] = [None] * 512
    lz4_matches = 0
    for k in range(0, n - 3, 2):
        w = struct.unpack_from("<I", data, k)[0]
        if w != 0:
            h = ((w * 2654435761) & 0xFFFFFFFF) >> 23
            if hash_val[h] == w:
                lz4_matches += 1
            else:
                hash_val[h] = w

    log_n = math.log2(n)
    entropy_bits = sum(
        c * log_n - (_LOG2_TABLE[c] if c <= 65536 else c * math.log2(c))
        for c in counts if c > 0
    )
    discount = zero_runs * 1.15 + lz4_matches * 6.5
    return overhead_bits + entropy_bits - discount


def is_modrm_opcode(prev: int) -> bool:
    return prev in (0x89, 0x8B, 0x8D, 0x83, 0x81, 0x39, 0x3B, 0x01, 0x03, 0x29, 0x2B, 0xFF, 0xF7)


def py_forward_exe_split(base_off: int, block: bytes) -> bytes:
    n = len(block)
    skeleton = bytearray()
    tail_targets = bytearray()
    i = 0
    prev = 0
    while i < n:
        b0 = block[i]
        if i <= n - 5 and b0 in (0xE8, 0xE9) and not is_modrm_opcode(prev):
            skeleton.append(b0)
            rel = struct.unpack_from("<I", block, i + 1)[0]
            abs_addr = (rel + base_off + i + 5) & 0xFFFFFFFF
            tail_targets += struct.pack(">I", abs_addr)
            prev = b0
            i += 5
        elif i <= n - 6 and is_rip_rel32_prefix2(b0, block[i + 1]):
            skeleton.append(b0)
            skeleton.append(block[i + 1])
            rel = struct.unpack_from("<I", block, i + 2)[0]
            abs_addr = (rel + base_off + i + 6) & 0xFFFFFFFF
            skeleton += struct.pack(">I", abs_addr)
            prev = block[i + 5]
            i += 6
        else:
            skeleton.append(b0)
            prev = b0
            i += 1
    return bytes(skeleton + tail_targets)


def py_inverse_exe_split(base_off: int, res: bytes) -> bytes:
    n = len(res)
    k = 0
    skel_pos = 0
    prev = 0
    while skel_pos + 4 * k < n:
        rem_orig = n - (skel_pos + 4 * k)
        b0 = res[skel_pos]
        if rem_orig >= 5 and b0 in (0xE8, 0xE9) and not is_modrm_opcode(prev):
            k += 1
            prev = b0
            skel_pos += 1
        elif rem_orig >= 6 and skel_pos + 6 <= n - 4 * k and is_rip_rel32_prefix2(b0, res[skel_pos + 1]):
            orig_i = skel_pos + 4 * k
            abs_addr = struct.unpack_from(">I", res, skel_pos + 2)[0]
            rel = (abs_addr - (base_off + orig_i + 6)) & 0xFFFFFFFF
            prev = (rel >> 24) & 0xFF
            skel_pos += 6
        else:
            prev = b0
            skel_pos += 1

    skel_len = skel_pos
    if skel_len + 4 * k != n:
        return res

    tail_targets = res[skel_len:]
    out = bytearray(n)
    out_idx = 0
    c_idx = 0
    s_idx = 0
    prev = 0
    while s_idx < skel_len:
        b0 = res[s_idx]
        if out_idx <= n - 5 and b0 in (0xE8, 0xE9) and not is_modrm_opcode(prev) and c_idx < k:
            out[out_idx] = b0
            abs_addr = struct.unpack_from(">I", tail_targets, c_idx * 4)[0]
            rel = (abs_addr - (base_off + out_idx + 5)) & 0xFFFFFFFF
            struct.pack_into("<I", out, out_idx + 1, rel)
            prev = b0
            out_idx += 5
            c_idx += 1
            s_idx += 1
        elif out_idx <= n - 6 and s_idx <= skel_len - 6 and is_rip_rel32_prefix2(b0, res[s_idx + 1]):
            out[out_idx] = b0
            out[out_idx + 1] = res[s_idx + 1]
            abs_addr = struct.unpack_from(">I", res, s_idx + 2)[0]
            rel = (abs_addr - (base_off + out_idx + 6)) & 0xFFFFFFFF
            struct.pack_into("<I", out, out_idx + 2, rel)
            prev = out[out_idx + 5]
            out_idx += 6
            s_idx += 6
        else:
            out[out_idx] = b0
            out_idx += 1
            prev = b0
            s_idx += 1
    return bytes(out)


def py_forward_lift(opcode: int, params: bytes, block: bytes) -> bytes:
    n = len(block)
    a, b, c = params[0], params[1], params[2]
    res = bytearray(n)

    if opcode == OP_RAW:
        return block
    elif opcode == OP_SPLIT:
        s = max(1, a)
        return b"".join(block[lane::s] for lane in range(s))
    elif opcode == OP_EXE_SPLIT:
        base_off = int.from_bytes(params[:4], "big")
        return py_forward_exe_split(base_off, block)
    elif opcode == OP_POLY2:
        for i in range(n):
            res[i] = (block[i] - ((a * i * i + b * i + c) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA1_LANE:
        s = max(1, a)
        for i in range(n):
            res[i] = block[i] if i < s else (block[i] - ((block[i - s] + b) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA2_LANE:
        s = max(1, a)
        two_s = s * 2
        for i in range(n):
            res[i] = block[i] if i < two_s else (block[i] - ((2 * block[i - s] - block[i - two_s]) & 0xFF)) & 0xFF
    elif opcode in (OP_EXE_REL32, OP_EXE32):
        base_off = int.from_bytes(params[:4], "big") if opcode == OP_EXE32 else (a << 16) | (b << 8) | c
        res[:] = block
        i = 0
        while i <= n - 5:
            b0 = block[i]
            if b0 in (0xE8, 0xE9):
                rel = struct.unpack_from("<I", block, i + 1)[0]
                abs_addr = (rel + base_off + i + 5) & 0xFFFFFFFF
                struct.pack_into(">I", res, i + 1, abs_addr)
                i += 5
            elif i <= n - 6 and is_rip_rel32_prefix2(b0, block[i + 1]):
                rel = struct.unpack_from("<I", block, i + 2)[0]
                abs_addr = (rel + base_off + i + 6) & 0xFFFFFFFF
                struct.pack_into(">I", res, i + 2, abs_addr)
                i += 6
            else:
                i += 1
    elif opcode == OP_XORSHIFT16:
        state = ((a << 8) | b) or 1
        mode = c & 0x03
        for i in range(n):
            if mode == 0:
                state ^= (state << 7) & 0xFFFF
                state ^= (state >> 9) & 0xFFFF
                state ^= (state << 8) & 0xFFFF
            elif mode == 1:
                state = (state * LCG_MULT + LCG_INC) & 0xFFFF
            else:
                state ^= (state << 5) & 0xFFFF
                state ^= (state >> 3) & 0xFFFF
            res[i] = (block[i] - (state >> 8)) & 0xFF
    elif opcode == OP_TRANSPOSE4:
        words, rem = n // 4, n % 4
        idx = 0
        for lane in range(4):
            prev = 0
            for w in range(words):
                val = block[w * 4 + lane]
                res[idx] = (val - prev) & 0xFF
                prev = val
                idx += 1
        for r in range(rem):
            res[idx] = block[words * 4 + r]
            idx += 1
    return bytes(res)


def py_inverse_lift(opcode: int, params: bytes, res: bytes) -> bytes:
    n = len(res)
    a, b, c = params[0], params[1], params[2]
    out = bytearray(n)

    if opcode == OP_RAW:
        return res
    elif opcode == OP_SPLIT:
        s = max(1, a)
        idx = 0
        for lane in range(s):
            plane_len = len(range(lane, n, s))
            out[lane::s] = res[idx:idx + plane_len]
            idx += plane_len
    elif opcode == OP_EXE_SPLIT:
        base_off = int.from_bytes(params[:4], "big")
        return py_inverse_exe_split(base_off, res)
    elif opcode == OP_POLY2:
        for i in range(n):
            out[i] = (res[i] + ((a * i * i + b * i + c) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA1_LANE:
        s = max(1, a)
        for i in range(n):
            out[i] = res[i] if i < s else (res[i] + ((out[i - s] + b) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA2_LANE:
        s = max(1, a)
        two_s = s * 2
        for i in range(n):
            out[i] = res[i] if i < two_s else (res[i] + ((2 * out[i - s] - out[i - two_s]) & 0xFF)) & 0xFF
    elif opcode in (OP_EXE_REL32, OP_EXE32):
        base_off = int.from_bytes(params[:4], "big") if opcode == OP_EXE32 else (a << 16) | (b << 8) | c
        out[:] = res
        i = 0
        while i <= n - 5:
            b0 = out[i]
            if b0 in (0xE8, 0xE9):
                abs_addr = struct.unpack_from(">I", out, i + 1)[0]
                rel = (abs_addr - (base_off + i + 5)) & 0xFFFFFFFF
                struct.pack_into("<I", out, i + 1, rel)
                i += 5
            elif i <= n - 6 and is_rip_rel32_prefix2(b0, out[i + 1]):
                abs_addr = struct.unpack_from(">I", out, i + 2)[0]
                rel = (abs_addr - (base_off + i + 6)) & 0xFFFFFFFF
                struct.pack_into("<I", out, i + 2, rel)
                i += 6
            else:
                i += 1
    elif opcode == OP_XORSHIFT16:
        state = ((a << 8) | b) or 1
        mode = c & 0x03
        for i in range(n):
            if mode == 0:
                state ^= (state << 7) & 0xFFFF
                state ^= (state >> 9) & 0xFFFF
                state ^= (state << 8) & 0xFFFF
            elif mode == 1:
                state = (state * LCG_MULT + LCG_INC) & 0xFFFF
            else:
                state ^= (state << 5) & 0xFFFF
                state ^= (state >> 3) & 0xFFFF
            out[i] = (res[i] + (state >> 8)) & 0xFF
    elif opcode == OP_TRANSPOSE4:
        words, rem = n // 4, n % 4
        idx = 0
        for lane in range(4):
            prev = 0
            for w in range(words):
                val = (res[idx] + prev) & 0xFF
                out[w * 4 + lane] = val
                prev = val
                idx += 1
        for r in range(rem):
            out[words * 4 + r] = res[idx]
            idx += 1
    return bytes(out)


def py_score_block(
    block: bytes, block_offset: int, features: PlanFeatures = DEFAULT_FEATURES
) -> Tuple[List[float], int, bytes]:
    """Python twin of score_block() in ahps_simd.c: slot costs plus the best block-local program."""
    n = len(block)
    raw_bits = py_estimate_mdl_bits(block, 0.0)
    costs = [raw_bits] * NUM_SLOTS
    costs[SLOT_RAW] = raw_bits - abs(raw_bits) * 0.025
    exe_prm = (block_offset & 0xFFFFFFFF).to_bytes(4, "big")
    costs[SLOT_EXE] = COST_NONE
    if features.exe32:
        costs[SLOT_EXE] = py_estimate_mdl_bits(py_forward_lift(OP_EXE32, exe_prm, block))
    if features.exe_split:
        costs[SLOT_EXE] = min(costs[SLOT_EXE], py_estimate_mdl_bits(py_forward_lift(OP_EXE_SPLIT, exe_prm, block)))

    local: List[Tuple[int, bytes]] = []
    for si, s in enumerate(STRIDES):
        if n >= s * 4:
            prm = bytes([s, 0, 0, 0])
            costs[SLOT_DELTA1 + si] = py_estimate_mdl_bits(py_forward_lift(OP_DELTA1_LANE, prm, block))
            costs[SLOT_DELTA2 + si] = py_estimate_mdl_bits(py_forward_lift(OP_DELTA2_LANE, prm, block))
            deltas = [(block[i] - block[i - s]) & 0xFF for i in range(s, min(n, s * 16))]
            modal_d = max(set(deltas), key=deltas.count)
            if modal_d != 0:
                local.append((OP_DELTA1_LANE, bytes([s, modal_d, 0, 0])))

    if n >= 4:
        c_est = block[0]
        d1_0, d1_1 = (block[1] - block[0]) & 0xFF, (block[2] - block[1]) & 0xFF
        d2_0 = (d1_1 - d1_0) & 0xFF
        if d2_0 % 2 == 0:
            # The other root adds 128*i*(i-1), which is zero modulo 256.
            a_cand = d2_0 // 2
            local.append((OP_POLY2, bytes([a_cand, (d1_0 - a_cand) & 0xFF, c_est, 0])))

    if n >= 32:
        b0, b1, b2, b3 = block[0], block[1], block[2], block[3]
        for s1_lo in range(256):
            s1 = (b0 << 8) | s1_lo
            s2 = (s1 * LCG_MULT + LCG_INC) & 0xFFFF
            if (s2 >> 8) == b1:
                s3 = (s2 * LCG_MULT + LCG_INC) & 0xFFFF
                s4 = (s3 * LCG_MULT + LCG_INC) & 0xFFFF
                if abs((s3 >> 8) - b2) <= 2 and abs((s4 >> 8) - b3) <= 2:
                    seed = ((s1 - LCG_INC) * LCG_INV_MULT) & 0xFFFF
                    if seed != 0:
                        local.append((OP_XORSHIFT16, bytes([seed >> 8, seed & 0xFF, 1, 0])))

    best_cost, best_op, best_params = COST_NONE, OP_RAW, bytes(4)
    for opcode, params in local:
        cost = py_estimate_mdl_bits(py_forward_lift(opcode, params, block))
        if cost < best_cost:
            best_cost, best_op, best_params = cost, opcode, params
    costs[SLOT_LOCAL] = best_cost
    return costs, best_op, best_params


# =====================================================================
# 4. ENGINE-NEUTRAL LIFTING HELPERS
# =====================================================================
def lift_step(opcode: int, params: bytes, data: bytes, use_c: bool, inverse: bool = False) -> bytes:
    if use_c:
        return SIMD_BRIDGE.lift(opcode, params, data, inverse)
    return (py_inverse_lift if inverse else py_forward_lift)(opcode, params, data)


def apply_program(program: Program, data: bytes, use_c: bool) -> bytes:
    for opcode, params in program:
        data = lift_step(opcode, params, data, use_c)
    return data


def estimate_bits(data: bytes, use_c: bool) -> float:
    return SIMD_BRIDGE.estimate(data) if use_c else py_estimate_mdl_bits(data)


def lzma_raw_size(data: bytes, preset: int) -> int:
    filters = [{"id": lzma.FILTER_LZMA2, "preset": preset}]
    return len(lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters))


def resolve_lzma_preset(lzma_preset: Optional[int], data_len: int) -> int:
    if lzma_preset is None:
        return (9 | lzma.PRESET_EXTREME) if data_len <= EXTREME_LIMIT else 3
    return (9 | lzma.PRESET_EXTREME) if lzma_preset >= 9 else lzma_preset


def program_label(program: Program) -> str:
    if not program:
        return "RAW"
    parts = []
    for opcode, params in program:
        arg = f"({params[0]})" if opcode in (OP_DELTA1_LANE, OP_DELTA2_LANE, OP_SPLIT) else ""
        parts.append(OPCODE_NAMES[opcode] + arg)
    return " -> ".join(parts)


# =====================================================================
# 5. SEGMENT PLANNER (ONE PROGRAM ACROSS MANY BLOCKS)
# =====================================================================
@dataclass
class Segment:
    start: int
    length: int
    program: Program


def slot_program(
    slot: int, offset: int, local_op: int, local_params: bytes, features: PlanFeatures = DEFAULT_FEATURES
) -> Program:
    if slot == SLOT_RAW:
        return []
    if slot == SLOT_EXE:
        return [(OP_EXE32 if features.exe32 else OP_EXE_SPLIT, (offset & 0xFFFFFFFF).to_bytes(4, "big"))]
    if slot == SLOT_LOCAL:
        return [(local_op, local_params)]
    opcode = OP_DELTA1_LANE if slot < SLOT_DELTA2 else OP_DELTA2_LANE
    stride = STRIDES[(slot - SLOT_DELTA1) % len(STRIDES)]
    return [(opcode, bytes([stride, 0, 0, 0]))]


def is_generator_hit(costs: List[float], block: int, local_op: int) -> bool:
    """True when a POLY2 or PRNG program all but erases a block, so its low cost can be trusted."""
    if local_op not in (OP_POLY2, OP_XORSHIFT16):
        return False
    row = block * NUM_SLOTS
    raw_bits = costs[row + SLOT_RAW]
    return raw_bits > 0 and max(0.0, costs[row + SLOT_LOCAL]) * GENERATOR_MARGIN < raw_bits


def viterbi_path(costs: List[float], num_blocks: int, generator_hits: bytearray) -> List[int]:
    """
    Cheapest slot per block, where changing slot costs SWITCH_BITS and local programs never carry over.
    Fixes t=0 free-entry bug so block 0 also pays the entry cost for SLOT_LOCAL.
    """
    back = bytearray(num_blocks * NUM_SLOTS)
    prev = list(costs[:NUM_SLOTS])
    prev[SLOT_LOCAL] += GENERATOR_SWITCH_BITS if generator_hits[0] else SWITCH_BITS

    slots = range(NUM_SLOTS)
    for t in range(1, num_blocks):
        row = costs[t * NUM_SLOTS:(t + 1) * NUM_SLOTS]
        best = min(prev)
        best_slot = prev.index(best)
        switched = best + SWITCH_BITS
        cur = [0.0] * NUM_SLOTS
        for k in slots:
            if k != SLOT_LOCAL and prev[k] <= switched:
                cur[k] = prev[k] + row[k]
                back[t * NUM_SLOTS + k] = k
            else:
                cur[k] = switched + row[k]
                back[t * NUM_SLOTS + k] = best_slot
        if generator_hits[t]:
            cur[SLOT_LOCAL] = best + GENERATOR_SWITCH_BITS + row[SLOT_LOCAL]
        prev = cur

    path = [0] * num_blocks
    k = prev.index(min(prev))
    for t in range(num_blocks - 1, -1, -1):
        path[t] = k
        k = back[t * NUM_SLOTS + k]
    return path


def shifted_program(program: Program, offset: int) -> Program:
    """Rewrites a POLY2 or LCG step so that running it from byte `offset` continues the original stream."""
    opcode, params = program[0]
    if opcode == OP_POLY2:
        a, b, c = params[0], params[1], params[2]
        return [(opcode, bytes([a, (2 * a * offset + b) & 0xFF, (a * offset * offset + b * offset + c) & 0xFF, 0]))]
    # Closed-form LCG jump: s[n] = a^n * s[0] + c * (a^n - 1) / (a - 1) mod 2^16
    power = pow(LCG_MULT, offset, 65536 * (LCG_MULT - 1))
    state = (power * ((params[0] << 8) | params[1]) + (power - 1) // (LCG_MULT - 1) * LCG_INC) & 0xFFFF
    return [(opcode, bytes([state >> 8, state & 0xFF, params[2], 0]))]


def extend_generators(segments: List[Segment], data: bytes, use_c: bool) -> List[Segment]:
    """Lets a POLY2 or LCG program absorb following segments when its stream simply carries on."""
    out: List[Segment] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        i += 1
        if seg.program and seg.program[0][0] in (OP_POLY2, OP_XORSHIFT16):
            while i < len(segments) and seg.length + segments[i].length <= MAX_SEGMENT_LEN:
                nxt = segments[i]
                piece = data[nxt.start:nxt.start + nxt.length]
                carried = apply_program(shifted_program(seg.program, seg.length), piece, use_c)
                planned = apply_program(nxt.program, piece, use_c)
                if estimate_bits(carried, use_c) > estimate_bits(planned, use_c):
                    break
                seg.length += nxt.length
                i += 1
        out.append(seg)
    return out


def refine_chain(seg: Segment, data: bytes, use_c: bool, features: PlanFeatures = DEFAULT_FEATURES) -> None:
    """
    Picks delta order + plane split (or inline OP_EXE32 vs segregated OP_EXE_SPLIT)
    using real LZMA trials on a representative sample.
    """
    if seg.length < CHAIN_MIN_LEN or len(seg.program) != 1:
        return
    opcode, params = seg.program[0]

    if opcode in (OP_EXE32, OP_EXE_SPLIT):
        sample_len = min(seg.length, CHAIN_SAMPLE_LEN)
        sample = data[seg.start:seg.start + sample_len]
        cands: List[Program] = []
        if features.exe32:
            cands.append([(OP_EXE32, params)])
        if features.exe_split:
            cands.append([(OP_EXE_SPLIT, params)])
        seg.program = min(cands, key=lambda p: lzma_raw_size(apply_program(p, sample, use_c), 1))
        return

    if opcode not in (OP_DELTA1_LANE, OP_DELTA2_LANE):
        return

    stride = params[0]
    sample_len = min(seg.length, CHAIN_SAMPLE_LEN)
    skip = (seg.length - sample_len) // 2 // stride * stride
    sample = data[seg.start + skip:seg.start + skip + sample_len]

    plain = bytes([stride, 0, 0, 0])
    split: Program = [(OP_SPLIT, plain)] if stride > 1 and features.delta_split else []
    candidates: List[Program] = [
        [(opcode, params)],
        [(OP_DELTA1_LANE, plain)],
        [(OP_DELTA2_LANE, plain)],
    ]
    if split:
        candidates += [[(OP_DELTA1_LANE, plain)] + split, [(OP_DELTA2_LANE, plain)] + split]
    seg.program = min(candidates, key=lambda p: lzma_raw_size(apply_program(p, sample, use_c), 1))


def split_long_segments(segments: List[Segment]) -> List[Segment]:
    out: List[Segment] = []
    for seg in segments:
        for off in range(0, seg.length, MAX_SEGMENT_LEN):
            out.append(Segment(seg.start + off, min(MAX_SEGMENT_LEN, seg.length - off), seg.program))
    return out


def plan_segments(
    data: bytes, block_size: int, use_c: bool, features: PlanFeatures = DEFAULT_FEATURES
) -> List[Segment]:
    num_blocks = (len(data) + block_size - 1) // block_size
    if num_blocks == 0:
        return []

    if use_c:
        costs, local = SIMD_BRIDGE.score_all(data, block_size, features.exe_flags)
    else:
        costs, local_parts = [], bytearray()
        for idx in range(num_blocks):
            off = idx * block_size
            row, op, params = py_score_block(data[off:off + block_size], off, features)
            costs += row
            local_parts.append(op)
            local_parts += params
        local = bytes(local_parts)

    generator_hits = bytearray(is_generator_hit(costs, idx, local[idx * 5]) for idx in range(num_blocks))

    segments: List[Segment] = []
    prev_slot = -1
    for idx, slot in enumerate(viterbi_path(costs, num_blocks, generator_hits)):
        off = idx * block_size
        length = min(block_size, len(data) - off)
        program = slot_program(slot, off, local[idx * 5], local[idx * 5 + 1:idx * 5 + 5], features)
        same_delta = (
            slot == SLOT_LOCAL and segments and program == segments[-1].program
            and program[0][0] == OP_DELTA1_LANE
        )
        if (slot == prev_slot and slot != SLOT_LOCAL) or same_delta:
            segments[-1].length += length
        else:
            segments.append(Segment(off, length, program))
        prev_slot = slot

    segments = extend_generators(segments, data, use_c)
    for seg in segments:
        refine_chain(seg, data, use_c, features)
    return split_long_segments(segments)


# =====================================================================
# 6. CORE ARCHIVER PIPELINE (COMPRESS / DECOMPRESS)
# =====================================================================
@dataclass
class CompressionReport:
    input_bytes: int
    output_bytes: int
    lift_sec: float
    lzma_sec: float
    backend_used: str
    programs: Dict[str, Tuple[int, int]]
    features: PlanFeatures = DEFAULT_FEATURES
    search: SearchMethod = EXHAUSTIVE

    @property
    def elapsed_sec(self) -> float:
        return self.lift_sec + self.lzma_sec

    @property
    def ratio(self) -> float:
        return self.input_bytes / max(1, self.output_bytes)

    @property
    def lift_mb_s(self) -> float:
        return (self.input_bytes / (1024 * 1024)) / max(1e-6, self.lift_sec)


def pack_container(data: bytes, segments: List[Segment], use_c: bool) -> bytes:
    # Compact Header (25 bytes): [Magic 5B][NumSegments 4B][TableLen 4B][TotalLen 8B][CRC32 4B]
    # Pre-allocates a single contiguous bytearray to avoid 3x full-file peak memory during trials.
    table = bytearray()
    for seg in segments:
        table += struct.pack("<IB", seg.length, len(seg.program))
        for opcode, params in seg.program:
            table.append(opcode)
            table += params
    header_and_table_len = V4_HEADER_LEN + len(table)
    out = bytearray(header_and_table_len + len(data))
    out[:5] = MAGIC_HEADER
    struct.pack_into("<IIQI", out, 5, len(segments), len(table), len(data), zlib.crc32(data))
    out[V4_HEADER_LEN:header_and_table_len] = table
    cursor = header_and_table_len
    for seg in segments:
        if not seg.program:
            out[cursor:cursor + seg.length] = data[seg.start:seg.start + seg.length]
        else:
            out[cursor:cursor + seg.length] = apply_program(seg.program, data[seg.start:seg.start + seg.length], use_c)
        cursor += seg.length
    return bytes(out)


def xz_compress(raw_container: bytes, preset: int) -> bytes:
    return lzma.compress(raw_container, format=lzma.FORMAT_XZ, preset=preset)


def program_summary(segments: List[Segment]) -> Dict[str, Tuple[int, int]]:
    summary: Dict[str, Tuple[int, int]] = {}
    for seg in segments:
        count, length = summary.get(program_label(seg.program), (0, 0))
        summary[program_label(seg.program)] = (count + 1, length + seg.length)
    return summary


def looks_like_x86(data: bytes) -> bool:
    """Counts CALLs with a near target, which is how real code looks and random bytes rarely do."""
    calls = sum(1 for _ in X86_NEAR_CALL.finditer(data))
    return calls << 20 >= X86_CALLS_PER_MIB * len(data)


def rival_plans(
    data: bytes, segments: List[Segment], features: PlanFeatures = DEFAULT_FEATURES, exe32_rival: bool = True
) -> List[List[Segment]]:
    """Whole-file plans that the block estimator cannot weigh against its own segmentation."""
    rivals: List[List[Segment]] = []
    if any(seg.program for seg in segments):
        rivals.append(split_long_segments([Segment(0, len(data), [])]))
    uses_exe = any(seg.program and seg.program[0][0] in (OP_EXE32, OP_EXE_SPLIT) for seg in segments)
    if data and (uses_exe or looks_like_x86(data)):
        if features.exe32 and exe32_rival:
            rivals.append(split_long_segments([Segment(0, len(data), [(OP_EXE32, bytes(4))])]))
        if features.exe_split:
            rivals.append(split_long_segments([Segment(0, len(data), [(OP_EXE_SPLIT, bytes(4))])]))
    return rivals


def best_plan(data: bytes, plans: List[List[Segment]], preset: int, use_c: bool,
              progress: ProgressCallback = None) -> Tuple[List[Segment], bytes]:
    """Compresses each plan at the final preset, since plans differ by less than a cheap trial can rank."""
    best, comp = plans[0], b""
    for idx, plan in enumerate(plans):
        if progress is not None:
            progress(idx / len(plans), f"Compressing trial {idx + 1}/{len(plans)}")
        trial = xz_compress(pack_container(data, plan, use_c), preset)
        if idx == 0 or len(trial) < len(comp):
            best, comp = plan, trial
    if progress is not None:
        progress(1.0, "Compression trials complete")
    return best, comp


def rebased_segment(seg: Segment, offset: int) -> Segment:
    """Moves a chunk's segment to its place in the file, keeping EXE addresses absolute across chunks."""
    program: Program = []
    for opcode, params in seg.program:
        if opcode in (OP_EXE32, OP_EXE_SPLIT):
            params = ((int.from_bytes(params, "big") + offset) & 0xFFFFFFFF).to_bytes(4, "big")
        program.append((opcode, params))
    return Segment(seg.start + offset, seg.length, program)


def default_search(preset: int) -> SearchMethod:
    """Preset 9 uses cheap chunk trials, which lost nothing on the development files. Preset 3 lost 0.04% on Blender."""
    return FAST_CHUNKS if preset == 9 | lzma.PRESET_EXTREME else EXHAUSTIVE


def chunk_trial_preset(preset: int, search: SearchMethod) -> int:
    """The final preset, EXTREME included, unless the method caps it at a lower level."""
    cap = search.chunk_trial_preset
    return preset if cap is None or cap >= preset & ~lzma.PRESET_EXTREME else cap


def chunked_plan(
    data: bytes,
    block_size: int,
    preset: int,
    use_c: bool,
    features: PlanFeatures = DEFAULT_FEATURES,
    search: SearchMethod = EXHAUSTIVE,
    progress: ProgressCallback = None,
) -> List[Segment]:
    """One plan per chunk, since no single whole-file plan suits both the code and the data of a large binary."""
    trial_preset = chunk_trial_preset(preset, search)
    plan: List[Segment] = []
    for offset in range(0, len(data), PLAN_CHUNK_LEN):
        chunk = data[offset:offset + PLAN_CHUNK_LEN]
        segments = plan_segments(chunk, block_size, use_c, features)
        plans = [segments] + rival_plans(chunk, segments, features, search.exe32_chunk_rival)
        # Chunk trials only select a plan; their compressed bytes are discarded.
        selected = plans[0] if len(plans) == 1 else best_plan(chunk, plans, trial_preset, use_c)[0]
        for seg in selected:
            if plan and not seg.program and not plan[-1].program:
                plan[-1].length += seg.length
            else:
                plan.append(rebased_segment(seg, offset))
        if progress is not None:
            progress(min(offset + len(chunk), len(data)) / len(data), "Planning chunks")
    return split_long_segments(plan)


def ahps_compress_bytes(
    data: bytes,
    block_size: int = 4096,
    force_python: bool = False,
    lzma_preset: Optional[int] = None,
    features: PlanFeatures = DEFAULT_FEATURES,
    search: object = AUTO_SEARCH,
    csv_mode: bool = False,
    safetensors_layout: Optional[TensorLayout] = None,
    source_offset: int = 0,
    progress: ProgressCallback = None,
) -> Tuple[bytes, CompressionReport]:
    if type(csv_mode) is not bool:
        raise TypeError("csv_mode must be a boolean.")
    if safetensors_layout is not None and not isinstance(safetensors_layout, TensorLayout):
        raise TypeError("safetensors_layout must be a TensorLayout.")
    if type(source_offset) is not int or source_offset < 0:
        raise ValueError("source_offset must be a nonnegative integer.")
    if source_offset and safetensors_layout is None:
        raise ValueError("source_offset requires a safetensors layout.")
    if csv_mode and safetensors_layout is not None:
        raise ValueError("Select either CSV or safetensors mode.")
    if safetensors_layout is not None and len(data) > MAX_CHUNK_BYTES:
        raise ValueError("Safetensors mode currently supports chunks up to 32 MiB.")
    if safetensors_layout is not None and source_offset + len(data) > safetensors_layout.source_size:
        raise ValueError("Tensor input range exceeds the source layout.")
    if not isinstance(features, PlanFeatures):
        raise TypeError("features must be a PlanFeatures.")
    if search is not AUTO_SEARCH and not isinstance(search, SearchMethod):
        raise TypeError("search must be a SearchMethod.")
    if not 1 <= block_size <= MAX_BLOCK_SIZE:
        raise ValueError(f"Block size must be between 1 and {MAX_BLOCK_SIZE:,} bytes.")

    t0 = time.perf_counter()
    use_c = (SIMD_BRIDGE.lib is not None) and (not force_python)
    preset = resolve_lzma_preset(lzma_preset, len(data))
    if search is AUTO_SEARCH:
        search = default_search(preset)
    if progress is not None:
        progress(0.0, "Planning transforms")
    if len(data) > EXTREME_LIMIT and features.per_chunk:
        planning = {"progress": progress_range(progress, 0.0, 0.3)} if progress is not None else {}
        segments = chunked_plan(data, block_size, preset, use_c, features, search, **planning)
        plans = [segments]
        if any(seg.program for seg in segments):
            plans.append(split_long_segments([Segment(0, len(data), [])]))
    else:
        segments = plan_segments(data, block_size, use_c, features)
        plans = [segments] + rival_plans(data, segments, features)
    if progress is not None:
        progress(0.3, "Preparing representation")
    candidate_container = pack_csv_container(data) if csv_mode else None
    if safetensors_layout is not None:
        candidate_container = pack_tensor_container(data, safetensors_layout, source_offset,
                                                   force_python=force_python)
    t_lift = time.perf_counter() - t0

    t1 = time.perf_counter()
    candidate = None
    trial_end = 0.9 if candidate_container is not None else 1.0
    trials = {"progress": progress_range(progress, 0.4, trial_end)} if progress is not None else {}
    if candidate_container is not None and len(data) >= CSV_PARALLEL_MIN_LEN and (os.cpu_count() or 1) > 1:
        # The worker owns only its immutable container and independent LZMA state.
        with ThreadPoolExecutor(max_workers=1) as executor:
            candidate_trial = executor.submit(xz_compress, candidate_container, preset)
            segments, comp = best_plan(data, plans, preset, use_c, **trials)
            if progress is not None:
                progress(0.9, "Finishing format candidate")
            candidate = candidate_trial.result()
    else:
        segments, comp = best_plan(data, plans, preset, use_c, **trials)
        if candidate_container is not None:
            if progress is not None:
                progress(0.9, "Compressing format candidate")
            candidate = xz_compress(candidate_container, preset)
    # Measure the concurrent stage once so elapsed_sec remains wall time.
    t_lzma = time.perf_counter() - t1

    programs = program_summary(segments)
    if candidate is not None and len(candidate) < len(comp):
        comp = candidate
        label = "SAFETENSORS" if safetensors_layout is not None else "CSV_COLUMNS"
        programs = {label: (1, len(data))}

    report = CompressionReport(
        input_bytes=len(data),
        output_bytes=len(comp),
        lift_sec=t_lift,
        lzma_sec=t_lzma,
        backend_used=SIMD_BRIDGE.lib_path if use_c else "Pure Python Fallback",
        programs=programs,
        features=features,
        search=search,
    )
    if progress is not None:
        progress(1.0, "Encoded")
    return comp, report


def parse_container(raw: bytes) -> Tuple[List[Tuple[int, Program]], int, int, int]:
    """
    Validates an untrusted AHPS\\x04 or AHPS\\x05 container and returns (segments, res_start, total_len, crc).
    The C kernel does no bounds checking, so nothing may reach it before passing these checks.
    """
    if len(raw) < V4_HEADER_LEN:
        raise ValueError("Invalid or corrupt AHPS archive header.")
    num_segments, table_len, total_len, crc = struct.unpack_from("<IIQI", raw, 5)

    res_start = V4_HEADER_LEN + table_len
    if res_start + total_len != len(raw):
        raise ValueError("Corrupt AHPS archive: section lengths do not match the container.")
    if num_segments * 5 > table_len:
        raise ValueError("Corrupt AHPS archive: segment table is too short.")
    return parse_segments(raw, num_segments, res_start, total_len), res_start, total_len, crc


def parse_segments(raw: bytes, num_segments: int, res_start: int, total_len: int) -> List[Tuple[int, Program]]:
    """Validates a segment table that ends at res_start, with the 25-byte header still in front of it."""
    segments: List[Tuple[int, Program]] = []
    pos = V4_HEADER_LEN
    covered = 0
    for _ in range(num_segments):
        if pos + 5 > res_start:
            raise ValueError("Corrupt AHPS archive: segment table is truncated.")
        length, num_steps = struct.unpack_from("<IB", raw, pos)
        pos += 5
        if not 1 <= length <= MAX_SEGMENT_LEN:
            raise ValueError(f"Corrupt AHPS archive: invalid segment length {length}.")
        if num_steps > MAX_PROGRAM_STEPS or pos + num_steps * 5 > res_start:
            raise ValueError("Corrupt AHPS archive: invalid program length.")
        program: Program = []
        for _ in range(num_steps):
            opcode = raw[pos]
            if opcode not in STEP_OPCODES:
                raise ValueError(f"Corrupt AHPS archive: unknown opcode {opcode}.")
            program.append((opcode, raw[pos + 1:pos + 5]))
            pos += 5
        segments.append((length, program))
        covered += length

    if pos != res_start or covered != total_len:
        raise ValueError("Corrupt AHPS archive: segment table does not cover the residual.")
    return segments


def parse_container_v3(raw: bytes) -> Tuple[int, int, int, int]:
    """Validates an AHPS v2.3 container and returns (num_blocks, block_size, prm_len, total_len)."""
    if len(raw) < 17:
        raise ValueError("Invalid or corrupt AHPS v2.3 archive header.")

    num_blocks, block_size, prm_len = struct.unpack_from("<III", raw, 5)
    if not 1 <= block_size <= MAX_BLOCK_SIZE:
        raise ValueError(f"Corrupt AHPS archive: invalid block size {block_size}.")

    ops_start = 17
    prm_start = ops_start + num_blocks
    res_start = prm_start + prm_len
    if res_start > len(raw):
        raise ValueError("Corrupt AHPS archive: program tape overruns the container.")

    total_len = len(raw) - res_start
    if num_blocks != (total_len + block_size - 1) // block_size:
        raise ValueError("Corrupt AHPS archive: block count does not match the residual length.")

    ops = raw[ops_start:prm_start]
    if ops and max(ops) > OP_TRANSPOSE4:
        raise ValueError(f"Corrupt AHPS archive: unknown opcode {max(ops)}.")
    if prm_len != (num_blocks - ops.count(OP_RAW)) * 3:
        raise ValueError("Corrupt AHPS archive: parameter length does not match the opcodes.")

    return num_blocks, block_size, prm_len, total_len


def decompress_v3(raw: bytes, use_c: bool, limits: DecodeLimits = DEFAULT_LIMITS) -> bytes:
    num_blocks, block_size, prm_len, total_len = parse_container_v3(raw)
    if total_len > limits.max_output_bytes:
        raise DecodeLimitError(f"Archive restores {total_len:,} bytes, over the {limits.max_output_bytes:,} byte limit.")
    if num_blocks == 0 or total_len == 0:
        return b""

    ops_start = 17
    prm_start = ops_start + num_blocks
    res_start = prm_start + prm_len

    if use_c:
        return SIMD_BRIDGE.unlift_all(raw, num_blocks, block_size, prm_len, total_len)

    prm_cursor = prm_start
    out = bytearray()
    for idx in range(num_blocks):
        off = idx * block_size
        blk_len = min(block_size, total_len - off)
        op = raw[ops_start + idx]
        if op != OP_RAW:
            params = raw[prm_cursor:prm_cursor + 3] + b"\x00"
            prm_cursor += 3
        else:
            params = b"\x00\x00\x00\x00"
        res_slice = raw[res_start + off:res_start + off + blk_len]
        out += py_inverse_lift(op, params, res_slice)

    return bytes(out)


def check_declared_sizes(head: bytes, limits: DecodeLimits) -> None:
    """Rejects an archive from its first bytes, before the rest of it is decoded."""
    if head[:5] not in (MAGIC_HEADER, MAGIC_HEADER_V4, MAGIC_HEADER_V3, MAGIC_CSV, MAGIC_TENSORS):
        raise ValueError("Invalid or corrupt AHPS archive header.")
    if head[:5] != MAGIC_HEADER_V3:
        _, table_len, total_len, _ = struct.unpack_from("<IIQI", head, 5)
        if total_len > limits.max_output_bytes:
            raise DecodeLimitError(f"Archive restores {total_len:,} bytes, over the {limits.max_output_bytes:,} byte limit.")
        if head[:5] == MAGIC_TENSORS and total_len > MAX_CHUNK_BYTES:
            raise DecodeLimitError("Tensor archive exceeds the 32 MiB chunk limit.")
        minimum_container = V4_HEADER_LEN + table_len
        if head[:5] != MAGIC_TENSORS:
            minimum_container += total_len
        if minimum_container > limits.max_container_bytes:
            raise DecodeLimitError(f"Archive container is over the {limits.max_container_bytes:,} byte limit.")


def decode_step(decoder: lzma.LZMADecompressor, data: bytes, max_len: int, limits: DecodeLimits) -> bytes:
    try:
        return decoder.decompress(data, max_len)
    except lzma.LZMAError as exc:
        if "Memory usage limit" in str(exc):
            raise DecodeLimitError(f"Archive needs over the {limits.max_dict_bytes:,} byte LZMA memory limit.") from exc
        raise


def assemble(parts: List[bytes], size: int) -> bytearray:
    """
    Copies decoded pieces into one buffer, freeing each as it goes. The size comes from bytes that
    really decoded, never from a header, so a tiny archive cannot demand a huge allocation.
    """
    buf = bytearray(size)
    pos = 0
    parts.reverse()
    while parts:
        piece = parts.pop()
        buf[pos:pos + len(piece)] = piece
        pos += len(piece)
    return buf


def decode_container_buffer(comp_stream: bytes, limits: DecodeLimits,
                            progress: ProgressCallback = None) -> bytearray:
    """Decodes the single XZ stream, freeing the LZMA dictionary before the buffer is built."""
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=limits.max_dict_bytes)
    parts: List[bytes] = []
    size = 0
    checked = False
    expected_size = None
    pending = comp_stream
    if progress is not None:
        progress(0.0, "Decoding XZ")
    while not decoder.eof:
        wanted = DECODE_STEP if checked else V4_HEADER_LEN - size
        piece = decode_step(decoder, pending, min(wanted, limits.max_container_bytes - size + 1), limits)
        pending = b""
        if not piece and decoder.needs_input:
            raise ValueError("Corrupt AHPS archive: the XZ stream is truncated.")
        parts.append(piece)
        size += len(piece)
        if size > limits.max_container_bytes:
            raise DecodeLimitError(f"Archive container is over the {limits.max_container_bytes:,} byte limit.")
        if not checked and size >= V4_HEADER_LEN:
            head = b"".join(parts)[:V4_HEADER_LEN]
            check_declared_sizes(head, limits)
            checked = True
            if progress is not None and head[:5] not in (MAGIC_HEADER_V3, MAGIC_TENSORS):
                _, table_len, total_len, _ = struct.unpack_from("<IIQI", head, 5)
                expected_size = V4_HEADER_LEN + table_len + total_len
        if progress is not None and expected_size:
            progress(min(0.99, size / expected_size), "Decoding XZ")
    if decoder.unused_data:
        raise ValueError("Corrupt AHPS archive: data follows the XZ stream.")
    del decoder
    result = assemble(parts, size)
    if progress is not None:
        progress(1.0, "Container decoded")
    return result


def decode_container(comp_stream: bytes, limits: DecodeLimits) -> bytes:
    return bytes(decode_container_buffer(comp_stream, limits))


def ahps_decompress_bytes(
    comp_stream: bytes, force_python: bool = False, limits: DecodeLimits = DEFAULT_LIMITS,
    progress: ProgressCallback = None,
) -> bytes:
    if not isinstance(limits, DecodeLimits):
        raise TypeError("limits must be a DecodeLimits.")
    decoding = {"progress": progress_range(progress, 0.0, 0.65)} if progress is not None else {}
    raw_buf = decode_container_buffer(comp_stream, limits, **decoding)
    if progress is not None:
        progress(0.65, "Restoring original bytes")
    if raw_buf[:5] == MAGIC_TENSORS:
        out = io.BytesIO()
        restore_tensor_container(raw_buf, out, force_python=force_python)
        if progress is not None:
            progress(1.0, "Restored and verified")
        return out.getvalue()
    if raw_buf[:5] == MAGIC_CSV:
        out = io.BytesIO()
        restore_csv_container(raw_buf, out)
        if progress is not None:
            progress(1.0, "Restored and verified")
        return out.getvalue()
    use_c = (SIMD_BRIDGE.lib is not None) and (not force_python)
    if raw_buf[:5] == MAGIC_HEADER_V3:
        raw = bytes(raw_buf)
        del raw_buf
        result = decompress_v3(raw, use_c, limits)
        if progress is not None:
            progress(1.0, "Restored")
        return result
    if raw_buf[:5] not in (MAGIC_HEADER, MAGIC_HEADER_V4):
        raise ValueError("Invalid or corrupt AHPS archive header.")

    is_v4 = (raw_buf[:5] == MAGIC_HEADER_V4)
    segments, res_start, total_len, crc = parse_container(raw_buf)

    # Discard the 25-byte header + segment table in-place so `raw_buf` IS the exact residual buffer!
    del raw_buf[:res_start]

    # Allocate a single reusable C scratch buffer sized ONLY to the largest active (non-RAW) segment
    max_active_seg = max((length for length, prog in segments if prog), default=0)
    scratch = ctypes.create_string_buffer(max_active_seg) if (use_c and max_active_seg > 0) else None

    cursor = 0
    next_progress = STREAM_RAW_STEP
    for length, program in segments:
        if program:
            if use_c and scratch is not None:
                for opcode, params in reversed(program):
                    SIMD_BRIDGE.lift_into(opcode, params, raw_buf, cursor, length, scratch, inverse=True)
            else:
                piece = bytes(raw_buf[cursor:cursor + length])
                for opcode, params in reversed(program):
                    piece = py_inverse_lift(opcode, params, piece)
                raw_buf[cursor:cursor + length] = piece
                del piece
        cursor += length
        if progress is not None and cursor >= next_progress:
            progress(0.65 + 0.3 * cursor / max(1, total_len), "Restoring original bytes")
            next_progress = cursor + STREAM_RAW_STEP

    del scratch
    if zlib.crc32(raw_buf) != crc:
        raise crc_failure(is_v4, segments)
    if progress is not None:
        progress(1.0, "Restored and verified")
    return bytes(raw_buf)


def crc_failure(is_v4: bool, segments: List[Tuple[int, Program]]) -> ValueError:
    if is_v4 and any(op == OP_EXE_SPLIT for _, prog in segments for op, _ in prog):
        return ValueError(
            "Corrupt AHPS archive: restored data fails its CRC-32 check. It may also be an older "
            "AHPS\\x04 archive with unsupported EXE_SPLIT data, which needs the tool version that wrote it."
        )
    return ValueError("Corrupt AHPS archive: restored data fails its CRC-32 check.")


class ContainerStream:
    """Decoded container bytes pulled on demand from an archive file, under the same limits as the buffered path."""

    def __init__(self, source: BinaryIO, limits: DecodeLimits) -> None:
        self.source = source
        self.limits = limits
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=limits.max_dict_bytes)
        self.pieces: Deque[bytes] = deque()
        self.head = bytearray()
        self.decoded = 0
        self.checked = False

    def pull(self) -> None:
        wanted = DECODE_STEP if self.checked else V4_HEADER_LEN - self.decoded
        max_len = min(wanted, self.limits.max_container_bytes - self.decoded + 1)
        data = self.source.read(STREAM_READ_STEP) if self.decoder.needs_input else b""
        piece = decode_step(self.decoder, data, max_len, self.limits)
        if not piece and not data and self.decoder.needs_input:
            raise ValueError("Corrupt AHPS archive: the XZ stream is truncated.")
        if piece:
            self.pieces.append(piece)
        self.decoded += len(piece)
        if self.decoded > self.limits.max_container_bytes:
            raise DecodeLimitError(f"Archive container is over the {self.limits.max_container_bytes:,} byte limit.")
        if not self.checked:
            self.head += piece
            if self.decoded >= V4_HEADER_LEN:
                check_declared_sizes(bytes(self.head[:V4_HEADER_LEN]), self.limits)
                self.checked = True

    def take(self, count: int) -> List[bytes]:
        """Returns up to count decoded bytes as pieces, fewer only when the stream ends."""
        parts: List[bytes] = []
        while count > 0:
            if not self.pieces:
                if self.decoder.eof:
                    break
                self.pull()
                continue
            piece = self.pieces.popleft()
            if len(piece) > count:
                self.pieces.appendleft(piece[count:])
                piece = piece[:count]
            parts.append(piece)
            count -= len(piece)
        return parts

    def read(self, count: int) -> bytearray:
        parts = self.take(count)
        if sum(map(len, parts)) != count:
            raise ValueError("Corrupt AHPS archive: section lengths do not match the container.")
        return assemble(parts, count)

    def rest(self) -> List[bytes]:
        while not self.decoder.eof:
            self.pull()
        parts = list(self.pieces)
        self.pieces.clear()
        return parts

    def finish(self) -> None:
        """Drains the stream, which is also what makes liblzma verify its checksum. Any extra byte ends it at once."""
        while True:
            if self.pieces:
                raise ValueError("Corrupt AHPS archive: section lengths do not match the container.")
            if self.decoder.eof:
                break
            self.pull()
        if self.decoder.unused_data or self.source.read(1):
            raise ValueError("Corrupt AHPS archive: data follows the XZ stream.")


def restore_stream(stream: ContainerStream, out: BinaryIO, use_c: bool, force_python: bool = False,
                   progress: ProgressCallback = None) -> int:
    """Restore to provisional output, buffering v3/v6/v7 and streaming v4/v5 segments."""
    head = b"".join(stream.take(V4_HEADER_LEN))
    if progress is not None:
        progress(0.05, "Decoding archive")
    if head[:5] == MAGIC_TENSORS:
        parts = [head] + stream.rest()
        stream.finish()
        raw = assemble(parts, sum(map(len, parts)))
        if progress is not None:
            progress(0.7, "Restoring tensor bytes")
        return restore_tensor_container(raw, out, force_python=force_python)
    if head[:5] == MAGIC_CSV:
        parts = [head] + stream.rest()
        stream.finish()
        raw = assemble(parts, sum(map(len, parts)))
        if progress is not None:
            progress(0.7, "Restoring CSV bytes")
        return restore_csv_container(raw, out)
    if head[:5] == MAGIC_HEADER_V3:
        # v3 has no segment structure to stream, so it keeps its one decoder.
        parts = [head] + stream.rest()
        stream.finish()
        raw = bytes(assemble(parts, sum(map(len, parts))))
        if progress is not None:
            progress(0.7, "Restoring legacy bytes")
        restored = decompress_v3(raw, use_c, stream.limits)
        out.write(restored)
        return len(restored)
    if head[:5] not in (MAGIC_HEADER, MAGIC_HEADER_V4) or len(head) < V4_HEADER_LEN:
        raise ValueError("Invalid or corrupt AHPS archive header.")

    is_v4 = head[:5] == MAGIC_HEADER_V4
    num_segments, table_len, total_len, crc = struct.unpack_from("<IIQI", head, 5)
    if num_segments * 5 > table_len:
        raise ValueError("Corrupt AHPS archive: segment table is too short.")
    table = head + bytes(stream.read(table_len))
    segments = parse_segments(table, num_segments, V4_HEADER_LEN + table_len, total_len)

    running = 0
    scratch = None
    restored_bytes = 0
    next_progress = STREAM_RAW_STEP
    for length, program in segments:
        if not program:
            remaining = length
            while remaining:
                piece = stream.read(min(remaining, STREAM_RAW_STEP))
                remaining -= len(piece)
                running = zlib.crc32(piece, running)
                out.write(piece)
                if progress is not None:
                    restored_bytes += len(piece)
                    if restored_bytes >= next_progress:
                        progress(0.05 + 0.9 * restored_bytes / max(1, total_len), "Restoring original bytes")
                        next_progress = restored_bytes + STREAM_RAW_STEP
            continue
        buf = stream.read(length)
        if use_c:
            # Grown only after the segment has really decoded, so a table cannot demand it.
            if scratch is None or len(scratch) < length:
                scratch = None
                scratch = ctypes.create_string_buffer(length)
            for opcode, params in reversed(program):
                SIMD_BRIDGE.lift_into(opcode, params, buf, 0, length, scratch, inverse=True)
            piece = buf
            del buf
        else:
            piece = bytes(buf)
            del buf
            for opcode, params in reversed(program):
                piece = py_inverse_lift(opcode, params, piece)
        running = zlib.crc32(piece, running)
        out.write(piece)
        if progress is not None:
            restored_bytes += length
            if restored_bytes >= next_progress:
                progress(0.05 + 0.9 * restored_bytes / max(1, total_len), "Restoring original bytes")
                next_progress = restored_bytes + STREAM_RAW_STEP
        del piece
    stream.finish()
    if running != crc:
        raise crc_failure(is_v4, segments)
    if progress is not None:
        progress(1.0, "Restored and verified")
    return total_len


def ahps_decompress_file(
    in_path: Union[str, os.PathLike],
    out_path: Union[str, os.PathLike],
    force_python: bool = False,
    limits: DecodeLimits = DEFAULT_LIMITS,
    progress: ProgressCallback = None,
) -> int:
    """
    Restores an archive file to out_path and returns the restored length.
    v4/v5 hold one segment at a time; v3/v6/v7 buffer the complete container.
    Output goes to a temporary file beside out_path, so a failed decode leaves any existing file untouched.
    """
    if not isinstance(limits, DecodeLimits):
        raise TypeError("limits must be a DecodeLimits.")
    target = pathlib.Path(out_path)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    use_c = (SIMD_BRIDGE.lib is not None) and (not force_python)
    created = False
    if progress is not None:
        progress(0.0, "Opening archive")
    try:
        with open(in_path, "rb") as source:
            with open(temporary, "xb") as out:
                created = True
                decoding = {"progress": progress_range(progress, 0.0, 0.97)} if progress is not None else {}
                restored = restore_stream(ContainerStream(source, limits), out, use_c, force_python, **decoding)
        if progress is not None:
            progress(0.98, "Finalizing output")
        os.replace(temporary, target)
    except BaseException:
        if created:
            temporary.unlink(missing_ok=True)
        raise
    if progress is not None:
        progress(1.0, "Restored")
    return restored


# =====================================================================
# 7. CLI COMMAND HANDLERS
# =====================================================================
def print_report(report: CompressionReport) -> None:
    print(f"Engine Backend  : {report.backend_used}")
    print(f"Input Size      : {report.input_bytes:,} bytes")
    print(f"Compressed Size : {report.output_bytes:,} bytes ({report.ratio:.2f}x ratio)")
    print(
        f"Synthesis Speed : {report.lift_sec*1000:.2f} ms ({report.lift_mb_s:.1f} MB/s) | "
        f"LZMA Back-End: {report.lzma_sec*1000:.1f} ms"
    )
    print("Micro-VM Program Breakdown:")
    total_bytes = max(1, sum(length for _, length in report.programs.values()))
    for label, (count, length) in sorted(report.programs.items(), key=lambda kv: -kv[1][1]):
        pct = (length / total_bytes) * 100.0
        print(f"  {label}: {count:,} segments, {length:,} bytes ({pct:.1f}%)")


def cli_tensor_layout(args: argparse.Namespace) -> Optional[TensorLayout]:
    if args.safetensors is None:
        if args.source_offset:
            raise ValueError("--source-offset requires --safetensors.")
        return None
    if args.csv:
        raise ValueError("Select either --csv or --safetensors.")
    if pathlib.Path(args.input).stat().st_size > MAX_CHUNK_BYTES:
        raise ValueError("Safetensors mode currently supports chunks up to 32 MiB.")
    return parse_layout(args.safetensors or args.input)


def cli_progress_setting(args: argparse.Namespace) -> Optional[bool]:
    setting = getattr(args, "progress", None)
    return setting if type(setting) is bool else None


def cmd_compress(args: argparse.Namespace) -> None:
    in_path = pathlib.Path(args.input)
    out_path = pathlib.Path(args.output)
    with TerminalProgress("Compress", enabled=cli_progress_setting(args)) as bar:
        bar.update(0.0, "Reading input")
        layout = cli_tensor_layout(args)
        data = in_path.read_bytes()
        updates = {"progress": progress_range(bar.update, 0.05, 0.95)} if bar.enabled else {}
        comp, report = ahps_compress_bytes(
            data, block_size=args.block_size, force_python=args.pure_python, lzma_preset=args.preset,
            csv_mode=args.csv,
            safetensors_layout=layout, source_offset=args.source_offset, **updates,
        )
        bar.update(0.95, "Writing archive")
        out_path.write_bytes(comp)
    print(f"[+] Wrote {out_path} successfully.")
    print_report(report)


def cmd_decompress(args: argparse.Namespace) -> None:
    in_path = pathlib.Path(args.input)
    out_path = pathlib.Path(args.output)
    t0 = time.perf_counter()
    limits = DecodeLimits(args.max_dict_mb << 20, args.max_container_mb << 20, args.max_output_mb << 20)
    with TerminalProgress("Decompress", enabled=cli_progress_setting(args)) as bar:
        updates = {"progress": bar.update} if bar.enabled else {}
        restored = ahps_decompress_file(in_path, out_path, force_python=args.pure_python, limits=limits, **updates)
    elapsed = time.perf_counter() - t0
    print(f"[+] Restored {out_path} ({restored:,} bytes) in {elapsed*1000:.1f} ms.")


def run_benchmark_on_buffer(
    data: bytes,
    label: str,
    block_size: int = 4096,
    force_python: bool = False,
    lzma_preset: Optional[int] = None,
    csv_mode: bool = False,
    safetensors_layout: Optional[TensorLayout] = None,
    source_offset: int = 0,
    show_progress: Optional[bool] = None,
    ahps_only: bool = False,
) -> None:
    print(f"\n{'=' * 70}")
    print(f"BENCHMARK: {label} ({len(data):,} bytes)")
    print(f"{'=' * 70}")

    is_huge = len(data) > EXTREME_LIMIT
    gz_level = 6 if is_huge else 9
    xz_preset = resolve_lzma_preset(lzma_preset, len(data))

    if is_huge and lzma_preset is None:
        print(f"  [Info] Large file detected ({len(data)/(1024*1024):.1f} MB). Using fast entropy preset (pass -p 9 for extreme).")

    sys.stdout.flush()
    with TerminalProgress("Benchmark", enabled=show_progress) as bar:
        if not ahps_only:
            bar.update(0.0, "Benchmarking gzip")
            t0 = time.perf_counter()
            gz_bytes = zlib.compress(data, level=gz_level)
            t_gz = (time.perf_counter() - t0) * 1000.0

            bar.update(0.2, "Benchmarking XZ")
            t0 = time.perf_counter()
            xz_bytes = lzma.compress(data, format=lzma.FORMAT_XZ, preset=xz_preset)
            t_xz = (time.perf_counter() - t0) * 1000.0

        encode_start = 0.0 if ahps_only else 0.4
        bar.update(encode_start, "Benchmarking AHPS")
        updates = {"progress": progress_range(bar.update, encode_start, 0.8)} if bar.enabled else {}
        ahps_bytes, report = ahps_compress_bytes(
            data, block_size=block_size, force_python=force_python, lzma_preset=lzma_preset,
            csv_mode=csv_mode,
            safetensors_layout=safetensors_layout, source_offset=source_offset, **updates,
        )
        bar.update(0.8, "Verifying round trip")
        decoding = {"progress": progress_range(bar.update, 0.8, 0.98)} if bar.enabled else {}
        restored = ahps_decompress_bytes(ahps_bytes, force_python=force_python, **decoding)
        bar.update(0.98, "Checking SHA-256")
        sha_ok = hashlib.sha256(data).digest() == hashlib.sha256(restored).digest()
        if not sha_ok:
            sys.exit("[Error] SHA-256 round-trip verification FAILED!")

    print(f"  Raw Size        : {len(data):>12,} B (1.00x)")
    comparison = ""
    if not ahps_only:
        diff_vs_xz = len(xz_bytes) - len(ahps_bytes)
        win_str = f"({diff_vs_xz:+,} B saved vs xz)" if diff_vs_xz >= 0 else f"({-diff_vs_xz:+,} B overhead vs xz)"
        comparison = f"  {win_str}"
        print(f"  gzip -{gz_level:<2d}        : {len(gz_bytes):>12,} B ({len(data)/len(gz_bytes):.2f}x)  [{t_gz:.1f} ms]")
        print(f"  xz (LZMA2)      : {len(xz_bytes):>12,} B ({len(data)/len(xz_bytes):.2f}x)  [{t_xz:.1f} ms]")
    print(f"  AHPS-Bin v2.6   : {len(ahps_bytes):>12,} B ({report.ratio:.2f}x)  [{report.elapsed_sec*1000:.1f} ms]{comparison}")
    print(f"  SHA-256 Verified: {sha_ok}")
    print("-" * 70)
    print_report(report)

def cmd_bench(args: argparse.Namespace) -> None:
    in_path = pathlib.Path(args.input)
    layout = cli_tensor_layout(args)
    data = in_path.read_bytes()
    run_benchmark_on_buffer(
        data, label=in_path.name, block_size=args.block_size, force_python=args.pure_python, lzma_preset=args.preset,
        csv_mode=args.csv,
        safetensors_layout=layout, source_offset=args.source_offset,
        show_progress=cli_progress_setting(args),
        ahps_only=args.ahps_only,
    )


def cmd_selftest(args: argparse.Namespace) -> None:
    payload = bytearray()
    for i in range(512):
        payload += struct.pack("<QII", 1700000000000 + i * 1000 + (i % 3), 50000 + i, (i * 17 + 42) & 0xFFFFFFFF)
    subroutines = [0x00001040, 0x00001280, 0x00001500, 0x00001920, 0x00002100, 0x00002400, 0x00002800, 0x00003000]
    code_sec = bytearray()
    rng = 0x1337
    while len(code_sec) < 8192:
        cur_off = len(payload) + len(code_sec)
        target = subroutines[(cur_off // 13) % len(subroutines)]
        rel32 = (target - (cur_off + 5)) & 0xFFFFFFFF
        code_sec += b"\xE8" + struct.pack("<I", rel32)
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        pad_len = 3 + (rng % 7)
        code_sec += b"\x48\x89\xE5\x48\x83\xEC\x20\x90\xC3"[:pad_len]
    payload += code_sec[:8192]
    for i in range(2048):
        payload += struct.pack("<f", 100.0 + i * 0.015625)
    for blk in range(8):
        s = (0x3A00 + blk * 0x117) & 0xFFFF
        for _ in range(1024):
            s = (s * LCG_MULT + LCG_INC) & 0xFFFF
            payload.append((s >> 8) & 0xFF)

    run_benchmark_on_buffer(
        bytes(payload), label="32KB Synthetic Multi-Section Binary", block_size=1024, force_python=args.pure_python,
        show_progress=cli_progress_setting(args),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AHPS-Bin v2.6: Asymmetric Hybrid Program Synthesis Binary Compressor")
    parser.add_argument("--pure-python", action="store_true", help="Disable C/AVX2 DLL/SO and force Pure Python engine")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=None,
                        help="Show terminal progress (default: automatic on stderr TTY)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_comp = sub.add_parser("compress", help="Compress a file into an .ahps archive")
    p_comp.add_argument("input", help="Input file path")
    p_comp.add_argument("output", help="Output .ahps archive path")
    p_comp.add_argument("-b", "--block-size", type=int, default=4096, help="Planner block size in bytes (default: 4096)")
    p_comp.add_argument("-p", "--preset", type=int, default=None, help="LZMA preset 0-9 (default: auto)")
    p_comp.add_argument("--csv", action="store_true", help="Try lossless CSV column grouping and keep it only if smaller")
    p_comp.add_argument("--safetensors", nargs="?", const="", metavar="SOURCE",
                        help="Try tensor-aware encoding; optionally read the original model header from SOURCE")
    p_comp.add_argument("--source-offset", type=int, default=0, help="Chunk byte offset in the original safetensors file")
    p_comp.set_defaults(func=cmd_compress)

    p_decomp = sub.add_parser("decompress", help="Decompress an .ahps archive back to original file")
    p_decomp.add_argument("input", help="Input .ahps archive path")
    p_decomp.add_argument("output", help="Restored output file path")
    p_decomp.add_argument("--max-dict-mb", type=int, default=DEFAULT_LIMITS.max_dict_bytes >> 20, help="LZMA decoder memory limit in MiB")
    p_decomp.add_argument("--max-container-mb", type=int, default=DEFAULT_LIMITS.max_container_bytes >> 20, help="Decoded container limit in MiB")
    p_decomp.add_argument("--max-output-mb", type=int, default=DEFAULT_LIMITS.max_output_bytes >> 20, help="Restored size limit in MiB")
    p_decomp.set_defaults(func=cmd_decompress)

    p_bench = sub.add_parser("bench", help="Benchmark AHPS-Bin v2.6 against gzip and xz on a local file")
    p_bench.add_argument("input", help="Local file to benchmark")
    p_bench.add_argument("--ahps-only", action="store_true",
                         help="Benchmark only AHPS, skipping gzip and standalone XZ comparisons")
    p_bench.add_argument("-b", "--block-size", type=int, default=4096, help="Planner block size in bytes (default: 4096)")
    p_bench.add_argument("-p", "--preset", type=int, default=None, help="LZMA preset 0-9 (default: auto)")
    p_bench.add_argument("--csv", action="store_true", help="Try lossless CSV column grouping and keep it only if smaller")
    p_bench.add_argument("--safetensors", nargs="?", const="", metavar="SOURCE",
                         help="Try tensor-aware encoding; optionally read the original model header from SOURCE")
    p_bench.add_argument("--source-offset", type=int, default=0, help="Chunk byte offset in the original safetensors file")
    p_bench.set_defaults(func=cmd_bench)

    p_test = sub.add_parser("selftest", help="Run built-in 32KB multi-section verification benchmark")
    p_test.set_defaults(func=cmd_selftest)

    for command in (p_comp, p_decomp, p_bench, p_test):
        command.add_argument("--progress", action=argparse.BooleanOptionalAction, default=argparse.SUPPRESS,
                             help="Show terminal progress; --no-progress disables it")

    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, lzma.LZMAError) as exc:
        sys.exit(f"[Error] {exc}")


if __name__ == "__main__":
    main()
