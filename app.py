import struct
import zlib
from dataclasses import dataclass
from typing import Tuple, List

# =====================================================================
# 1. THE DETERMINISTIC MICRO-VM (Decompressor Execution Engine)
# =====================================================================
# OpCodes for our miniature generative Virtual Machine
OP_RAW        = 0x00  # Fallback: No program beat raw entropy
OP_POLY2      = 0x01  # B[i] = (a*i^2 + b*i + c) & 0xFF
OP_STRIDE_DEL = 0x02  # B[i] = (B[i - stride] + delta) & 0xFF (seeded by prefix)
OP_XOR_SYNTH  = 0x03  # B[i] = ((i * a) ^ (i >> b) + c) & 0xFF
OP_LCG_PRNG   = 0x04  # 16-bit LCG: state = (state * 25173 + 13849) & 0xFFFF

def execute_micro_program(opcode: int, params: bytes, length: int) -> bytearray:
    """Executes a 3-byte micro-program to synthesize a block of bytes."""
    out = bytearray(length)
    a, b, c = params[0], params[1], params[2]

    if opcode == OP_POLY2:
        for i in range(length):
            out[i] = (a * i * i + b * i + c) & 0xFF

    elif opcode == OP_STRIDE_DEL:
        stride = max(1, a)
        delta = b
        seed_Byte = c
        for i in range(length):
            if i < stride:
                out[i] = (seed_Byte + i * delta) & 0xFF
            else:
                out[i] = (out[i - stride] + delta) & 0xFF

    elif opcode == OP_XOR_SYNTH:
        shift = b & 0x07
        for i in range(length):
            out[i] = (((i * a) ^ (i >> shift)) + c) & 0xFF

    elif opcode == OP_LCG_PRNG:
        # 16-bit seed unpacked from (a, b), multiplier step modified by c
        state = (a << 8) | b
        mult = 25173 + c
        for i in range(length):
            state = (state * mult + 13849) & 0xFFFF
            out[i] = (state >> 8) & 0xFF

    return out


# =====================================================================
# 2. RESIDUAL MATH & COST ESTIMATION (Minimum Description Length)
# =====================================================================
def compute_residual(target: bytes, predicted: bytearray) -> bytes:
    """Computes modular error residual: R[i] = (Target[i] - Predicted[i]) mod 256."""
    return bytes((t - p) & 0xFF for t, p in zip(target, predicted))

def apply_residual(predicted: bytearray, residual: bytes) -> bytes:
    """Reconstructs exact lossless data: Target[i] = (Predicted[i] + R[i]) mod 256."""
    return bytes((p + r) & 0xFF for p, r in zip(predicted, residual))

def compress_residual_stream(data: bytes) -> bytes:
    """Raw deflate stream (wbits=-15 strips headers to measure pure bit entropy)."""
    c = zlib.compressobj(level=9, wbits=-15)
    return c.compress(data) + c.flush()

def decompress_residual_stream(comp_data: bytes) -> bytes:
    return zlib.decompress(comp_data, wbits=-15)


# =====================================================================
# 3. ASYMMETRIC PROGRAM SYNTHESIZER (Algebraic + Brute-Force Search)
# =====================================================================
@dataclass
class SynthesisWinner:
    opcode: int
    params: bytes
    compressed_payload: bytes
    total_bytes: int

def synthesize_block(block: bytes) -> SynthesisWinner:
    n = len(block)
    
    # Baseline: What if we don't use a program and just entropy-code the block?
    raw_comp = compress_residual_stream(block)
    best = SynthesisWinner(
        opcode=OP_RAW,
        params=b'\x00\x00\x00',
        compressed_payload=raw_comp,
        total_bytes=1 + len(raw_comp) # 1 byte opcode + payload
    )

    candidates: List[Tuple[int, bytes]] = []

    # --- A. Algebraic Lifting (O(1) Exact Finite-Difference Solver for OP_POLY2) ---
    # Instead of brute-forcing 256^3 = 16.7M polynomials, solve finite differences!
    if n >= 3:
        c_est = block[0]
        # 1st difference: D1[0] = b + a, D1[1] = 3a + b
        d1_0 = (block[1] - block[0]) & 0xFF
        d1_1 = (block[2] - block[1]) & 0xFF
        # 2nd difference: D2[0] = 2a (mod 256)
        d2_0 = (d1_1 - d1_0) & 0xFF
        if d2_0 % 2 == 0:
            for a_cand in (d2_0 // 2, (d2_0 // 2) + 128):
                b_cand = (d1_0 - a_cand) & 0xFF
                candidates.append((OP_POLY2, bytes([a_cand & 0xFF, b_cand, c_est])))

    # --- B. Stride & Delta Discovery ---
    for stride in range(1, min(16, n // 2)):
        delta = (block[stride] - block[0]) & 0xFF
        candidates.append((OP_STRIDE_DEL, bytes([stride, delta, block[0]])))

    # --- C. Guided Brute-Force over XOR-Synth & PRNG Space with Prefix Rejection ---
    # We test PRNG seeds & XOR rules, using a 2-byte fast filter before full evaluation
    b0, b1 = block[0], block[1] if n > 1 else 0
    
    # Search XOR cellular rules
    for a in range(1, 32):
        for shift in range(4):
            # Fast inline check: force c to match block[0] at i=0
            c = b0
            # Check if i=1 is within +/- 2 of block[1] (allows slight noise!)
            pred_1 = (((1 * a) ^ (1 >> shift)) + c) & 0xFF
            if abs(((b1 - pred_1 + 128) & 0xFF) - 128) <= 2:
                candidates.append((OP_XOR_SYNTH, bytes([a, shift, c])))

    # Search 16-bit PRNG seeds (Brute-force with 1st-byte direct inversion!)
    # Because out[0] = (state_1 >> 8), we only search seeds whose high byte matches b0
    for c_mod in (0, 1, 2):
        mult = 25173 + c_mod
        for seed_lo in range(256):
            # Algebraic shortcut: we test seeds around b0
            for seed_hi in range(256):
                s1 = (((seed_hi << 8) | seed_lo) * mult + 13849) & 0xFFFF
                if (s1 >> 8) == b0:
                    s2 = (s1 * mult + 13849) & 0xFFFF
                    if (s2 >> 8) == b1:  # 1 in 65,536 survival rate!
                        candidates.append((OP_LCG_PRNG, bytes([seed_hi, seed_lo, c_mod])))

    # --- D. Evaluate Surviving Candidates via MDL (Program + Residual) ---
    for opcode, params in candidates:
        pred = execute_micro_program(opcode, params, n)
        residual = compute_residual(block, pred)
        comp_res = compress_residual_stream(residual)
        # Cost = 1 byte opcode + 3 bytes params + compressed residual length
        total_cost = 4 + len(comp_res)
        if total_cost < best.total_bytes:
            best = SynthesisWinner(opcode, params, comp_res, total_cost)

    return best


# =====================================================================
# 4. CONTAINER ENCODER / DECODER & BENCHMARK
# =====================================================================
def ahps_compress(data: bytes, block_size: int = 256) -> Tuple[bytes, List[str]]:
    out = bytearray()
    log = []
    for offset in range(0, len(data), block_size):
        block = data[offset:offset + block_size]
        winner = synthesize_block(block)
        
        if winner.opcode == OP_RAW:
            # Header: [OP_RAW (1B)][BlockLen (2B)][PayloadLen (2B)][Payload]
            out += struct.pack("<BHH", OP_RAW, len(block), len(winner.compressed_payload))
            out += winner.compressed_payload
            log.append(f"Block {offset//block_size}: OP_RAW        -> {winner.total_bytes}B")
        else:
            # Header: [Opcode (1B)][Params (3B)][BlockLen (2B)][PayloadLen (2B)][Residual]
            out += struct.pack("<B3sHH", winner.opcode, winner.params, len(block), len(winner.compressed_payload))
            out += winner.compressed_payload
            op_names = {1: "OP_POLY2", 2: "OP_STRIDE", 3: "OP_XORSYNTH", 4: "OP_LCG_PRNG"}
            log.append(
                f"Block {offset//block_size}: {op_names[winner.opcode]:11s} "
                f"params={list(winner.params)} -> {winner.total_bytes}B ( vs Raw-Zlib {1+len(compress_residual_stream(block))}B )"
            )
    return bytes(out), log

def ahps_decompress(stream: bytes) -> bytes:
    out = bytearray()
    idx = 0
    while idx < len(stream):
        opcode = stream[idx]
        if opcode == OP_RAW:
            _, blk_len, pay_len = struct.unpack_from("<BHH", stream, idx)
            idx += 5
            payload = stream[idx:idx + pay_len]
            idx += pay_len
            out += decompress_residual_stream(payload)
        else:
            _, params, blk_len, pay_len = struct.unpack_from("<B3sHH", stream, idx)
            idx += 8
            payload = stream[idx:idx + pay_len]
            idx += pay_len
            pred = execute_micro_program(opcode, params, blk_len)
            residual = decompress_residual_stream(payload)
            out += apply_residual(pred, residual)
    return bytes(out)


if __name__ == "__main__":
    # Construct a realistic 4-block (1024-byte) heterogeneous test stream:
    # Block 0: Quadratic curve + random sensor jitter (+1/-1 noise on 15% of samples)
    b0 = bytearray((3 * i * i + 7 * i + 42 + (1 if i % 7 == 0 else 0)) & 0xFF for i in range(256))
    # Block 1: High-entropy pseudo-random LCG stream (uncompressible by gzip/zstd!)
    b1 = execute_micro_program(OP_LCG_PRNG, bytes([0xAF, 0x44, 1]), 256)
    # Block 2: Bitwise XOR texture pattern with a corrupted 4-byte burst at index 100
    b2 = execute_micro_program(OP_XOR_SYNTH, bytes([13, 2, 99]), 256)
    b2[100:104] = b"\xFF\x00\xAA\x55"
    # Block 3: Pure English prose (should automatically fall back to OP_RAW)
    b3 = (b"In information theory, Kolmogorov complexity of an object is the length "
          b"of the shortest computer program that produces the object as output. " * 2)[:256]

    original_data = bytes(b0 + b1 + b2 + b3)
    
    compressed, block_logs = ahps_compress(original_data, block_size=256)
    reconstructed = ahps_decompress(compressed)
    std_zlib_size = len(compress_residual_stream(original_data))

    print(f"Original Size      : {len(original_data)} bytes")
    print(f"Standard Zlib Size : {std_zlib_size} bytes")
    print(f"AHPS Hybrid Size   : {len(compressed)} bytes")
    print(f"Bit-Exact Lossless : {original_data == reconstructed}\n")
    for entry in block_logs:
        print(" ", entry)