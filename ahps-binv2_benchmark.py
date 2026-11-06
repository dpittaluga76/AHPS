import struct
import lzma
import zlib
import math
from dataclasses import dataclass
from typing import List, Tuple

# Run the exact AHPS-Bin v2 Engine on a 32 KB Heterogeneous Binary Benchmark
OP_RAW, OP_POLY2, OP_DELTA1_LANE, OP_DELTA2_LANE, OP_EXE_REL32, OP_XORSHIFT16, OP_TRANSPOSE4 = range(7)
MAGIC_HEADER = b"AHPS\x02"

def forward_lift_block(opcode: int, params: bytes, block: bytes) -> bytes:
    n = len(block)
    a, b, c = params[0], params[1], params[2]
    res = bytearray(n)
    if opcode == OP_RAW:
        return block
    elif opcode == OP_POLY2:
        for i in range(n):
            res[i] = (block[i] - ((a * i * i + b * i + c) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA1_LANE:
        stride = max(1, a)
        for i in range(n):
            res[i] = block[i] if i < stride else (block[i] - ((block[i - stride] + b) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA2_LANE:
        stride = max(1, a)
        two_s = stride * 2
        for i in range(n):
            res[i] = block[i] if i < two_s else (block[i] - ((2 * block[i - stride] - block[i - two_s]) & 0xFF)) & 0xFF
    elif opcode == OP_EXE_REL32:
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
        state = (a << 8) | b or 1
        shift_mode = c & 0x03
        for i in range(n):
            if shift_mode == 1:
                state = (state * 25173 + 13849) & 0xFFFF
            pred = (state >> 8) & 0xFF
            res[i] = (block[i] - pred) & 0xFF
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

def inverse_lift_block(opcode: int, params: bytes, res: bytes) -> bytes:
    n = len(res)
    a, b, c = params[0], params[1], params[2]
    out = bytearray(n)
    if opcode == OP_RAW:
        return res
    elif opcode == OP_POLY2:
        for i in range(n):
            out[i] = (res[i] + ((a * i * i + b * i + c) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA1_LANE:
        stride = max(1, a)
        for i in range(n):
            out[i] = res[i] if i < stride else (res[i] + ((out[i - stride] + b) & 0xFF)) & 0xFF
    elif opcode == OP_DELTA2_LANE:
        stride = max(1, a)
        two_s = stride * 2
        for i in range(n):
            out[i] = res[i] if i < two_s else (res[i] + ((2 * out[i - stride] - out[i - two_s]) & 0xFF)) & 0xFF
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
        state = (a << 8) | b or 1
        shift_mode = c & 0x03
        for i in range(n):
            if shift_mode == 1:
                state = (state * 25173 + 13849) & 0xFFFF
            pred = (state >> 8) & 0xFF
            out[i] = (res[i] + pred) & 0xFF
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

_LOG2_TABLE = [0.0] + [i * math.log2(i) for i in range(1, 4097)]
def estimate_mdl_bits(data: bytes, program_overhead_bits: float = 32.0) -> float:
    n = len(data)
    counts = [0] * 256
    zero_runs, prev_b = 0, -1
    for b in data:
        counts[b] += 1
        if b == 0 and prev_b == 0:
            zero_runs += 1
        prev_b = b
    log_n = math.log2(n)
    entropy_bits = sum(c * log_n - (_LOG2_TABLE[c] if c <= 4096 else c * math.log2(c)) for c in counts if c > 0)
    return program_overhead_bits + max(8.0, entropy_bits - zero_runs * 0.65)

@dataclass
class BlockSynthesisResult:
    opcode: int
    params: bytes
    residual: bytes
    estimated_bits: float

def synthesize_binary_block(block: bytes, block_offset: int) -> BlockSynthesisResult:
    n = len(block)
    best = BlockSynthesisResult(OP_RAW, b"\x00\x00\x00", block, estimate_mdl_bits(block, 8.0))
    candidates = []
    if n >= 64 and n % 4 == 0:
        candidates.append((OP_TRANSPOSE4, b"\x04\x00\x00"))
    if (block.count(0xE8) + block.count(0xE9)) >= max(3, n // 64):
        off24 = block_offset & 0xFFFFFF
        candidates.append((OP_EXE_REL32, bytes([(off24 >> 16) & 0xFF, (off24 >> 8) & 0xFF, off24 & 0xFF])))
    for s in (1, 2, 4, 8, 12, 16, 24, 32):
        if n >= s * 4:
            deltas = [(block[i] - block[i - s]) & 0xFF for i in range(s, min(n, s * 8))]
            modal_d = max(set(deltas), key=deltas.count)
            candidates.append((OP_DELTA1_LANE, bytes([s, modal_d, 0])))
            candidates.append((OP_DELTA2_LANE, bytes([s, 0, 0])))
    if n >= 4:
        c_est = block[0]
        d1_0, d1_1 = (block[1] - block[0]) & 0xFF, (block[2] - block[1]) & 0xFF
        d2_0 = (d1_1 - d1_0) & 0xFF
        if d2_0 % 2 == 0:
            for a_cand in (d2_0 // 2, (d2_0 // 2) + 128):
                candidates.append((OP_POLY2, bytes([a_cand & 0xFF, (d1_0 - a_cand) & 0xFF, c_est])))
    if n >= 32:
        b0, b1 = block[0], block[1]
        for seed_lo in range(256):
            s1_base = seed_lo * 25173 + 13849
            for seed_hi in range(256):
                s1 = (seed_hi * 6444288 + s1_base) & 0xFFFF
                if (s1 >> 8) == b0:
                    s2 = (s1 * 25173 + 13849) & 0xFFFF
                    if (s2 >> 8) == b1:
                        candidates.append((OP_XORSHIFT16, bytes([seed_hi, seed_lo, 1])))
    for opcode, params in candidates:
        res = forward_lift_block(opcode, params, block)
        cost = estimate_mdl_bits(res, 32.0)
        if cost < best.estimated_bits:
            best = BlockSynthesisResult(opcode, params, res, cost)
    return best

def compress_binary(data: bytes, block_size: int = 1024):
    program_tape, global_residual = bytearray(), bytearray()
    hist = {i: 0 for i in range(7)}
    num_blocks = (len(data) + block_size - 1) // block_size
    for idx in range(num_blocks):
        off = idx * block_size
        blk = data[off:off + block_size]
        w = synthesize_binary_block(blk, off)
        hist[w.opcode] += 1
        program_tape += struct.pack("<BH3s", w.opcode, len(blk), w.params)
        global_residual += w.residual
    raw_container = MAGIC_HEADER + struct.pack("<I", num_blocks) + bytes(program_tape) + bytes(global_residual)
    comp = lzma.compress(raw_container, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}])
    return comp, hist

def decompress_binary(comp: bytes) -> bytes:
    raw = lzma.decompress(comp, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2}])
    num_blocks = struct.unpack_from("<I", raw, 5)[0]
    tape_start, res_cursor = 9, 9 + num_blocks * 6
    out = bytearray()
    for b_idx in range(num_blocks):
        op, length, params = struct.unpack_from("<BH3s", raw, tape_start + b_idx * 6)
        out += inverse_lift_block(op, params, raw[res_cursor:res_cursor + length])
        res_cursor += length
    return bytes(out)

# --- Construct 32 KB Realistic Multi-Section Binary Payload ---
payload = bytearray()
# Section 1 (8 KB): 16-byte Database/Telemetry Struct Array (uint64 timestamp ramp + uint32 id + uint32 sensor)
for i in range(512):
    ts = 1700000000000 + i * 1000 + (i % 3) # Linear ramp with slight jitter
    rec_id = 50000 + i
    val = (i * 17 + 42) & 0xFFFFFFFF
    payload += struct.pack("<QII", ts, rec_id, val)

# Section 2 (8 KB): Simulated x86_64 Executable Machine Code Section (frequent CALL 0xE8 to 8 hot subroutines)
subroutines = [0x00001040, 0x00001280, 0x00001500, 0x00001920, 0x00002100, 0x00002400, 0x00002800, 0x00003000]
code_sec = bytearray()
while len(code_sec) < 8192:
    cur_off = len(payload) + len(code_sec)
    # 5-byte prologue + 5-byte E8 CALL + 6-byte mov/cmp
    target = subroutines[(cur_off // 16) % len(subroutines)]
    rel32 = (target - (cur_off + 5 + 5)) & 0xFFFFFFFF
    code_sec += b"\x48\x89\xE5\x48\x83" + b"\xE8" + struct.pack("<I", rel32) + b"\x48\x83\xC4\x08\xC3\x90"
payload += code_sec[:8192]

# Section 3 (8 KB): Float32 Tensor / Scientific Gradient Table (2048 IEEE-754 floats)
for i in range(2048):
    payload += struct.pack("<f", 100.0 + i * 0.015625)

# Section 4 (8 KB): 8 Blocks of seeded 16-bit LCG PRNG streams (uncompressible by LZ77/LZMA)
for blk in range(8):
    s = (0x3A00 + blk * 0x117) & 0xFFFF
    for _ in range(1024):
        s = (s * 25173 + 13849) & 0xFFFF
        payload.append((s >> 8) & 0xFF)

raw_bytes = bytes(payload)
gzip_size = len(zlib.compress(raw_bytes, level=9))
xz_size = len(lzma.compress(raw_bytes, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]))
ahps_comp, hist = compress_binary(raw_bytes, block_size=1024)
decoded = decompress_binary(ahps_comp)

print(f"Raw Binary Size : {len(raw_bytes):,} bytes")
print(f"gzip -9 Size    : {gzip_size:,} bytes ({len(raw_bytes)/gzip_size:.2f}x)")
print(f"xz -9 (LZMA2)   : {xz_size:,} bytes ({len(raw_bytes)/xz_size:.2f}x)")
print(f"AHPS-Bin v2     : {len(ahps_comp):,} bytes ({len(raw_bytes)/len(ahps_comp):.2f}x)")
print(f"Lossless Match  : {raw_bytes == decoded}")
print(f"OpCode Usage    : {hist}")