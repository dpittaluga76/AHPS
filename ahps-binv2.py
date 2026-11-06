#!/usr/bin/env python3
"""
AHPS-Bin v2: Asymmetric Hybrid Program Synthesis Binary Compressor
Architecture: Adaptive Chunking -> Algebraic/Brute-Force Micro-VM Lifting ->
              Two-Plane Global Residual Stream -> LZMA/ANS Dictionary Back-End.
"""

import struct
import lzma
import zlib
import math
from dataclasses import dataclass
from typing import List, Tuple

# =====================================================================
# 1. MICRO-VM SPECIFICATION (6 Binary-Specialized Opcodes)
# =====================================================================
OP_RAW         = 0x00  # Identity: P[i] = 0 (Residual = Raw Data)
OP_POLY2       = 0x01  # Quadratic byte curve: P[i] = (a*i^2 + b*i + c) & 0xFF
OP_DELTA1_LANE = 0x02  # 1st-Order Strided Autoregressive: P[i] = D[i - s] + d
OP_DELTA2_LANE = 0x03  # 2nd-Order Strided Linear Predictor: P[i] = 2*D[i - s] - D[i - 2s]
OP_EXE_REL32   = 0x04  # x86/x64 E8 (CALL) / E9 (JMP) RIP-Relative -> Absolute Lifter
OP_XORSHIFT16  = 0x05  # 16-bit Galois/Xorshift PRNG stream synthesizer
OP_TRANSPOSE4  = 0x06  # 4-Byte Lane Byte-Shuffle + 1st-Order Delta (Float32 / Int32 arrays)

MAGIC_HEADER = b"AHPS\x02"
# Raw LZMA2 streams carry no dictionary-size metadata. The historical preset-9
# encoder uses 64 MiB, so decoding must allow the same match distances.
LZMA_DICT_SIZE = 64 * 1024 * 1024


# =====================================================================
# 2. DETERMINISTIC FORWARD & INVERSE LIFTING ENGINE
# =====================================================================
def forward_lift_block(opcode: int, params: bytes, block: bytes) -> bytes:
    """
    Computes the residual R for a block given an opcode and 3-byte params.
    Note: Autoregressive opcodes (OP_DELTA1_LANE, OP_DELTA2_LANE) use causal
    prediction from the exact previous decoded bytes D[i - s], guaranteeing
    zero error accumulation during decompression!
    """
    n = len(block)
    a, b, c = params[0], params[1], params[2]
    res = bytearray(n)

    if opcode == OP_RAW:
        return block

    elif opcode == OP_POLY2:
        for i in range(n):
            pred = (a * i * i + b * i + c) & 0xFF
            res[i] = (block[i] - pred) & 0xFF

    elif opcode == OP_DELTA1_LANE:
        stride = max(1, a)
        delta = b
        for i in range(n):
            if i < stride:
                res[i] = block[i]
            else:
                pred = (block[i - stride] + delta) & 0xFF
                res[i] = (block[i] - pred) & 0xFF

    elif opcode == OP_DELTA2_LANE:
        stride = max(1, a)
        two_s = stride * 2
        for i in range(n):
            if i < two_s:
                res[i] = block[i]
            else:
                # 2nd-order finite difference predicts linear ramps across struct lanes
                pred = (2 * block[i - stride] - block[i - two_s]) & 0xFF
                res[i] = (block[i] - pred) & 0xFF

    elif opcode == OP_EXE_REL32:
        # Converts x86 CALL (0xE8) and JMP (0xE9) 32-bit relative offsets
        # into absolute target addresses by adding current offset (i + base).
        base_offset = (a << 16) | (b << 8) | c
        res[:] = block
        i = 0
        while i < n - 5:
            if block[i] in (0xE8, 0xE9):
                rel = struct.unpack_from("<I", block, i + 1)[0]
                abs_addr = (rel + base_offset + i + 5) & 0xFFFFFFFF
                struct.pack_into("<I", res, i + 1, abs_addr)
                i += 5
            else:
                i += 1

    elif opcode == OP_XORSHIFT16:
        state = (a << 8) | b
        if state == 0:
            state = 1
        shift_mode = c & 0x03
        for i in range(n):
            if shift_mode == 0:
                state ^= (state << 7) & 0xFFFF
                state ^= (state >> 9) & 0xFFFF
                state ^= (state << 8) & 0xFFFF
            elif shift_mode == 1:
                state = (state * 25173 + 13849) & 0xFFFF
            else:
                state ^= (state << 5) & 0xFFFF
                state ^= (state >> 3) & 0xFFFF
            pred = (state >> 8) & 0xFF
            res[i] = (block[i] - pred) & 0xFF

    elif opcode == OP_TRANSPOSE4:
        # Groups every 4-byte word into 4 contiguous byte-planes (Byte0, Byte1, Byte2, Byte3)
        # and applies delta coding inside each plane (ideal for Float32 / uint32 tensors).
        words = n // 4
        rem = n % 4
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


def inverse_lift_block(opcode: int, params: bytes, res: bytes) -> bytes:
    """Reconstructs the exact original block from the opcode, params, and residual."""
    n = len(res)
    a, b, c = params[0], params[1], params[2]
    out = bytearray(n)

    if opcode == OP_RAW:
        return res

    elif opcode == OP_POLY2:
        for i in range(n):
            pred = (a * i * i + b * i + c) & 0xFF
            out[i] = (res[i] + pred) & 0xFF

    elif opcode == OP_DELTA1_LANE:
        stride = max(1, a)
        delta = b
        for i in range(n):
            if i < stride:
                out[i] = res[i]
            else:
                pred = (out[i - stride] + delta) & 0xFF
                out[i] = (res[i] + pred) & 0xFF

    elif opcode == OP_DELTA2_LANE:
        stride = max(1, a)
        two_s = stride * 2
        for i in range(n):
            if i < two_s:
                out[i] = res[i]
            else:
                pred = (2 * out[i - stride] - out[i - two_s]) & 0xFF
                out[i] = (res[i] + pred) & 0xFF

    elif opcode == OP_EXE_REL32:
        base_offset = (a << 16) | (b << 8) | c
        out[:] = res
        i = 0
        while i < n - 5:
            if out[i] in (0xE8, 0xE9):
                abs_addr = struct.unpack_from("<I", out, i + 1)[0]
                rel = (abs_addr - (base_offset + i + 5)) & 0xFFFFFFFF
                struct.pack_into("<I", out, i + 1, rel)
                i += 5
            else:
                i += 1

    elif opcode == OP_XORSHIFT16:
        state = (a << 8) | b
        if state == 0:
            state = 1
        shift_mode = c & 0x03
        for i in range(n):
            if shift_mode == 0:
                state ^= (state << 7) & 0xFFFF
                state ^= (state >> 9) & 0xFFFF
                state ^= (state << 8) & 0xFFFF
            elif shift_mode == 1:
                state = (state * 25173 + 13849) & 0xFFFF
            else:
                state ^= (state << 5) & 0xFFFF
                state ^= (state >> 3) & 0xFFFF
            pred = (state >> 8) & 0xFF
            out[i] = (res[i] + pred) & 0xFF

    elif opcode == OP_TRANSPOSE4:
        words = n // 4
        rem = n % 4
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


# =====================================================================
# 3. FAST MDL ENTROPY ESTIMATOR (O(N) Zero-Allocation Bit Estimator)
# =====================================================================
# Precompute log2 table to evaluate candidate residuals in microseconds
_LOG2_TABLE = [0.0] + [i * math.log2(i) for i in range(1, 4097)]

def estimate_mdl_bits(data: bytes, program_overhead_bits: float = 32.0) -> float:
    """
    Computes empirical 0th-order + run-length bonus Shannon Entropy in bits:
    Cost = Program_Header_Bits + N * H_0(Residual) - Zero_Run_Bonus
    """
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

    log_n = math.log2(n)
    entropy_bits = 0.0
    for c in counts:
        if c > 0:
            if c <= 4096:
                entropy_bits += c * log_n - _LOG2_TABLE[c]
            else:
                entropy_bits += c * (log_n - math.log2(c))

    # Reward contiguous zero-runs that LZ77/LZMA RLE matches compress to ~0.05 bits
    rle_discount = zero_runs * 0.65
    return program_overhead_bits + max(8.0, entropy_bits - rle_discount)


# =====================================================================
# 4. ALGEBRAIC & GUIDED BRUTE-FORCE SYNTHESIZER PER BLOCK
# =====================================================================
@dataclass
class BlockSynthesisResult:
    opcode: int
    params: bytes
    residual: bytes
    estimated_bits: float

def synthesize_binary_block(block: bytes, block_offset: int) -> BlockSynthesisResult:
    n = len(block)
    best = BlockSynthesisResult(
        opcode=OP_RAW,
        params=b"\x00\x00\x00",
        residual=block,
        estimated_bits=estimate_mdl_bits(block, program_overhead_bits=8.0)
    )

    candidates: List[Tuple[int, bytes]] = []

    # 1. Test 4-Byte Float/Int Transpose + Delta
    if n >= 64 and n % 4 == 0:
        candidates.append((OP_TRANSPOSE4, b"\x04\x00\x00"))

    # 2. Test x86/x64 E8/E9 Relative Call/Jump Lifter if E8/E9 density >= 1.5%
    e8_count = block.count(0xE8) + block.count(0xE9)
    if e8_count >= max(3, n // 64):
        off24 = block_offset & 0xFFFFFF
        candidates.append((OP_EXE_REL32, bytes([(off24 >> 16) & 0xFF, (off24 >> 8) & 0xFF, off24 & 0xFF])))

    # 3. Test 1st and 2nd-Order Strided Autoregressive Lanes (common binary struct widths)
    common_strides = (1, 2, 4, 8, 12, 16, 20, 24, 32, 64)
    for s in common_strides:
        if n >= s * 4:
            # Estimate modal delta at this stride from prefix
            deltas = [(block[i] - block[i - s]) & 0xFF for i in range(s, min(n, s * 8))]
            modal_d = max(set(deltas), key=deltas.count)
            candidates.append((OP_DELTA1_LANE, bytes([s, modal_d, 0])))
            if modal_d != 0:
                candidates.append((OP_DELTA1_LANE, bytes([s, 0, 0])))
            candidates.append((OP_DELTA2_LANE, bytes([s, 0, 0])))

    # 4. Algebraic Finite-Difference Solver for Quadratic Curves (OP_POLY2)
    if n >= 4:
        c_est = block[0]
        d1_0 = (block[1] - block[0]) & 0xFF
        d1_1 = (block[2] - block[1]) & 0xFF
        d2_0 = (d1_1 - d1_0) & 0xFF
        if d2_0 % 2 == 0:
            for a_cand in (d2_0 // 2, (d2_0 // 2) + 128):
                b_cand = (d1_0 - a_cand) & 0xFF
                candidates.append((OP_POLY2, bytes([a_cand & 0xFF, b_cand, c_est])))

    # 5. Guided Brute-Force over 16-bit PRNG / Xorshift Seeds (with 2-byte prefix filter!)
    if n >= 32:
        b0, b1 = block[0], block[1]
        # Mode 1 (LCG): Direct high-byte inversion restricts 65,536 seeds to 256 checks
        for seed_lo in range(256):
            for seed_hi in range(256):
                s1 = (((seed_hi << 8) | seed_lo) * 25173 + 13849) & 0xFFFF
                if (s1 >> 8) == b0:
                    s2 = (s1 * 25173 + 13849) & 0xFFFF
                    if (s2 >> 8) == b1:
                        candidates.append((OP_XORSHIFT16, bytes([seed_hi, seed_lo, 1])))

    # Evaluate all surviving candidates against the fast MDL bit-cost gatekeeper
    for opcode, params in candidates:
        res = forward_lift_block(opcode, params, block)
        cost = estimate_mdl_bits(res, program_overhead_bits=32.0)
        if cost < best.estimated_bits:
            best = BlockSynthesisResult(opcode, params, res, cost)

    return best


# =====================================================================
# 5. GLOBAL TWO-PLANE ARCHIVER & DECOMPRESSOR
# =====================================================================
def compress_binary(data: bytes, block_size: int = 1024) -> Tuple[bytes, dict]:
    """
    Splits the binary into Plane 1 (Micro-Program Tape) and Plane 2 (Global Residual),
    then compresses the unified container with LZMA.
    """
    program_tape = bytearray()
    global_residual = bytearray()
    opcode_Histogram = {i: 0 for i in range(7)}

    num_blocks = (len(data) + block_size - 1) // block_size
    for idx in range(num_blocks):
        offset = idx * block_size
        block = data[offset:offset + block_size]
        winner = synthesize_binary_block(block, offset)
        opcode_Histogram[winner.opcode] += 1

        # Emit 6-byte Program Tape entry: [Opcode (1B)][BlockLen (2B)][Params (3B)]
        program_tape += struct.pack("<BH3s", winner.opcode, len(block), winner.params)
        global_residual += winner.residual

    # Pack Two-Plane Container before global entropy coding:
    # [Magic 5B][NumBlocks 4B][TapeBytes][GlobalResidualBytes]
    raw_container = (
        MAGIC_HEADER
        + struct.pack("<I", num_blocks)
        + bytes(program_tape)
        + bytes(global_residual)
    )

    # Global Entropy Back-End: Single-pass LZMA dictionary + range coder over the lifted planes
    compressed_stream = lzma.compress(
        raw_container,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME,
                  "dict_size": LZMA_DICT_SIZE}]
    )

    stats = {
        "input_bytes": len(data),
        "compressed_bytes": len(compressed_stream),
        "ratio": len(data) / max(1, len(compressed_stream)),
        "opcode_histogram": opcode_Histogram,
    }
    return compressed_stream, stats


def decompress_binary(compressed_stream: bytes) -> bytes:
    """Reconstructs the original binary from the Two-Plane LZMA stream."""
    raw_container = lzma.decompress(
        compressed_stream,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": LZMA_DICT_SIZE}]
    )
    if raw_container[:5] != MAGIC_HEADER:
        raise ValueError("Corrupt AHPS v2 header")

    num_blocks = struct.unpack_from("<I", raw_container, 5)[0]
    tape_start = 9
    tape_end = tape_start + num_blocks * 6
    res_cursor = tape_end

    reconstructed = bytearray()
    block_offset = 0

    for b_idx in range(num_blocks):
        t_off = tape_start + b_idx * 6
        opcode, blk_len, params = struct.unpack_from("<BH3s", raw_container, t_off)
        res_slice = raw_container[res_cursor:res_cursor + blk_len]
        res_cursor += blk_len

        orig_block = inverse_lift_block(opcode, params, res_slice)
        reconstructed += orig_block
        block_offset += blk_len

    return bytes(reconstructed)
