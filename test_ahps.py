#!/usr/bin/env python3
"""Regression tests for ahps.py. Run with: python -m unittest test_ahps"""

import lzma
import random
import struct
import unittest
import zlib
from unittest import mock

import ahps

ENGINES = [True] if ahps.SIMD_BRIDGE.lib is None else [False, True]
PROGRAM_OPS = [
    (ahps.OP_POLY2, bytes([3, 7, 42, 0])),
    (ahps.OP_DELTA1_LANE, bytes([4, 9, 0, 0])),
    (ahps.OP_DELTA2_LANE, bytes([16, 0, 0, 0])),
    (ahps.OP_XORSHIFT16, bytes([0xAF, 0x44, 0, 0])),
    (ahps.OP_XORSHIFT16, bytes([0xAF, 0x44, 1, 0])),
    (ahps.OP_XORSHIFT16, bytes([0xAF, 0x44, 2, 0])),
    (ahps.OP_TRANSPOSE4, bytes(4)),
    (ahps.OP_SPLIT, bytes([7, 0, 0, 0])),
    (ahps.OP_EXE32, bytes([0x12, 0x34, 0x56, 0x78])),
    (ahps.OP_EXE_SPLIT, bytes([0x12, 0x34, 0x56, 0x78])),
]

# Frozen wire-format examples, independently calculated rather than encoded by
# the implementation under test. A tuple is (name, absolute base, input, residual).
EXE_SPLIT_VECTORS = [
    ("empty", 0, b"", b""),
    ("no instructions", 0x12345678, b"ordinary bytes", b"ordinary bytes"),
    ("call and negative jump", 0x12345678,
     bytes.fromhex("e8 00000000 90 e9 ffffffff"),
     bytes.fromhex("e8 90 e9 1234567d 12345682")),
    ("absolute address wraps", 0xFFFFFFF8,
     bytes.fromhex("90 e8 00000000 e9 ffffffff 90"),
     bytes.fromhex("90 e8 e9 90 fffffffe 00000002")),
    ("peeled operand is not a predecessor", 0,
     bytes.fromhex("e8 01020389 e9 00000000"),
     bytes.fromhex("e8 e9 89030206 0000000a")),
    ("original RIP operand suppresses following call", 0x1000,
     bytes.fromhex("0f80 04030289 e8 01020304 90 e9 00000000"),
     bytes.fromhex("0f80 8902130a e8 01020304 90 e9 00001011")),
    ("normalized RIP operand must not suppress call", 0x1000,
     bytes.fromhex("0f80 83000000 e8 00000000"),
     bytes.fromhex("0f80 00001089 e8 0000100b")),
]

# These instruction families keep their four target bytes in the skeleton.
RIP_PREFIXES = (
    [bytes([0x0F, condition]) for condition in range(0x80, 0x90)]
    + [bytes.fromhex("ff15"), bytes.fromhex("ff25")]
    + [bytes([opcode, modrm]) for opcode in (0x8B, 0x8D)
       for modrm in (0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D)]
)

EXE_SPLIT_CHAIN_DATA = bytes.fromhex("e8 00000000 e9 00000000")
EXE_SPLIT_CHAIN = [
    (ahps.OP_EXE_SPLIT, bytes(4)),
    (ahps.OP_DELTA1_LANE, bytes([1, 0, 0, 0])),
    (ahps.OP_SPLIT, bytes([2, 0, 0, 0])),
]
EXE_SPLIT_CHAIN_RESIDUAL = bytes.fromhex("e81700fb00 010005000a")


def lcg_block(seed: int, n: int = 1024) -> bytes:
    state = seed
    out = bytearray()
    for _ in range(n):
        state = (state * ahps.LCG_MULT + ahps.LCG_INC) & 0xFFFF
        out.append(state >> 8)
    return bytes(out)


def x86ish(rng: random.Random, n: int) -> bytes:
    pool = [0xE8, 0xE9, 0x0F, 0x80, 0xFF, 0x15, 0x8B, 0x05, 0x8D, 0x00]
    return bytes(rng.choice(pool) if rng.random() < 0.6 else rng.randrange(256) for _ in range(n))


def records(n: int) -> bytes:
    return b"".join(struct.pack("<QII", 1700000000000 + i * 1000 + i % 3, 50000 + i, i * 17) for i in range(n))


def x86_code(rng: random.Random, n: int) -> bytes:
    """Random filler with CALLs to a few hot functions, like a code section the planner may leave RAW."""
    targets = [0x2000 + i * 0x1F0 for i in range(8)]
    out = bytearray()
    while len(out) < n:
        out += rng.randbytes(rng.randrange(20, 40))
        rel = (rng.choice(targets) - (len(out) + 5)) & 0xFFFFFFFF
        out += b"\xE8" + struct.pack("<I", rel)
    return bytes(out[:n])


def wrap(raw: bytes) -> bytes:
    """Packs a container into a well-formed xz stream, as a crafted archive would be."""
    return lzma.compress(raw, format=lzma.FORMAT_XZ, preset=0)


def v3_container(num_blocks: int, block_size: int, prm_len: int, body: bytes) -> bytes:
    return wrap(ahps.MAGIC_HEADER_V3 + struct.pack("<III", num_blocks, block_size, prm_len) + body)


def v4_container(num_segments, table: bytes, total_len: int, residual: bytes, crc=None) -> bytes:
    # Deliberately independent of the writer's current version: these archives
    # must remain v4 compatibility tests after MAGIC_HEADER advances to v5.
    # Structural-negative tests should not also fail the checksum: that could
    # conceal a parser accepting an invalid opcode/table. Explicit checksum
    # tests supply their own expected CRC below.
    if crc is None:
        crc = zlib.crc32(residual)
    header = b"AHPS\x04" + struct.pack("<IIQI", num_segments, len(table), total_len, crc)
    return wrap(header + table + residual)


def step(opcode: int, params: bytes = bytes(4)) -> bytes:
    return bytes([opcode]) + params


def crafted_segments(parts, magic: bytes) -> bytes:
    """Build a valid archive from supplied original bytes and known residuals."""
    original = b"".join(data for data, _, _ in parts)
    table = b"".join(
        struct.pack("<IB", len(data), len(program))
        + b"".join(step(opcode, params) for opcode, params in program)
        for data, program, _ in parts
    )
    residual = b"".join(res for _, _, res in parts)
    header = magic + struct.pack("<IIQI", len(parts), len(table), len(original), zlib.crc32(original))
    return wrap(header + table + residual)


class LiftTests(unittest.TestCase):
    def test_every_step_inverts_on_both_engines(self) -> None:
        data = random.Random(3).randbytes(1499)
        for opcode, params in PROGRAM_OPS:
            for fwd_py in ENGINES:
                res = ahps.lift_step(opcode, params, data, not fwd_py)
                for inv_py in ENGINES:
                    self.assertEqual(ahps.lift_step(opcode, params, res, not inv_py, inverse=True), data)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "needs the C kernel")
    def test_engines_produce_identical_residuals(self) -> None:
        for data in (random.Random(4).randbytes(1499), x86ish(random.Random(5), 1499)):
            for opcode, params in PROGRAM_OPS:
                self.assertEqual(ahps.lift_step(opcode, params, data, True), ahps.lift_step(opcode, params, data, False))
            self.assertAlmostEqual(ahps.estimate_bits(data, True), ahps.estimate_bits(data, False), places=6)

    def test_split_handles_lengths_that_are_not_multiples(self) -> None:
        for n in (1, 2, 3, 4, 5, 6):
            data = bytes(range(n))
            for use_c in {not py for py in ENGINES}:
                split = ahps.lift_step(ahps.OP_SPLIT, bytes([4, 0, 0, 0]), data, use_c)
                self.assertEqual(split, b"".join(data[lane::4] for lane in range(4)))
                self.assertEqual(ahps.lift_step(ahps.OP_SPLIT, bytes([4, 0, 0, 0]), split, use_c, inverse=True), data)


class ExeSplitTests(unittest.TestCase):
    def assert_known_outputs(self, use_c: bool) -> None:
        for name, base, data, expected in EXE_SPLIT_VECTORS:
            with self.subTest(case=name, use_c=use_c):
                params = base.to_bytes(4, "big")
                self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c), expected)
                self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, expected, use_c, inverse=True), data)

    def test_python_known_outputs_and_inverse(self) -> None:
        self.assert_known_outputs(False)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "native EXE_SPLIT coverage requires the C kernel")
    def test_native_known_outputs_and_inverse(self) -> None:
        self.assert_known_outputs(True)

    def assert_instruction_boundaries(self, use_c: bool) -> None:
        params = (0xFFFFFFF8).to_bytes(4, "big")
        for prefix in (b"\xe8", b"\xe9", *RIP_PREFIXES):
            instruction = prefix + bytes.fromhex("04030201")
            for length in range(len(instruction)):
                data = b"\x90" + instruction[:length]
                with self.subTest(prefix=prefix.hex(), tail_length=length, use_c=use_c):
                    self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c), data)
                    self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c, inverse=True), data)
        for prefix in RIP_PREFIXES:
            # At absolute 0xfffffff8, a six-byte instruction with rel=0x01020304
            # has target 0x01020302 after the uint32 wrap.
            data = prefix + bytes.fromhex("04030201")
            expected = prefix + bytes.fromhex("01020302")
            with self.subTest(rip_prefix=prefix.hex(), use_c=use_c):
                self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c), expected)
                self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, expected, use_c, inverse=True), data)
        for predecessor in (0x89, 0x8B, 0x8D, 0x83, 0x81, 0x39, 0x3B, 0x01, 0x03, 0x29, 0x2B, 0xFF, 0xF7):
            for opcode in (0xE8, 0xE9):
                data = bytes([predecessor, opcode]) + bytes.fromhex("11223344")
                with self.subTest(predecessor=predecessor, opcode=opcode, use_c=use_c):
                    self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c), data)
                    self.assertEqual(ahps.lift_step(ahps.OP_EXE_SPLIT, params, data, use_c, inverse=True), data)

    def test_python_instruction_boundaries_and_modrm_exclusions(self) -> None:
        self.assert_instruction_boundaries(False)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "native EXE_SPLIT coverage requires the C kernel")
    def test_native_instruction_boundaries_and_modrm_exclusions(self) -> None:
        self.assert_instruction_boundaries(True)

    def assert_chained_output(self, use_c: bool) -> None:
        self.assertEqual(ahps.apply_program(EXE_SPLIT_CHAIN, EXE_SPLIT_CHAIN_DATA, use_c),
                         EXE_SPLIT_CHAIN_RESIDUAL)
        restored = EXE_SPLIT_CHAIN_RESIDUAL
        for opcode, params in reversed(EXE_SPLIT_CHAIN):
            restored = ahps.lift_step(opcode, params, restored, use_c, inverse=True)
        self.assertEqual(restored, EXE_SPLIT_CHAIN_DATA)

    def test_python_chained_program_has_known_output(self) -> None:
        self.assert_chained_output(False)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "native EXE_SPLIT coverage requires the C kernel")
    def test_native_chained_program_has_known_output(self) -> None:
        self.assert_chained_output(True)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "cross-engine EXE_SPLIT coverage requires the C kernel")
    def test_cross_engine_residuals_and_inverse(self) -> None:
        rng = random.Random(40)
        samples = [data for _, _, data, _ in EXE_SPLIT_VECTORS]
        samples += [x86ish(rng, n) for n in (*range(32), 63, 64, 65, 1023, 1024, 1025)]
        for base in (0, 0x12345678, 0xFFFFFFFC):
            params = base.to_bytes(4, "big")
            for index, data in enumerate(samples):
                with self.subTest(base=base, sample=index, length=len(data)):
                    python_res = ahps.py_forward_lift(ahps.OP_EXE_SPLIT, params, data)
                    native_res = ahps.SIMD_BRIDGE.lift(ahps.OP_EXE_SPLIT, params, data, False)
                    self.assertEqual(native_res, python_res)
                    self.assertEqual(ahps.py_inverse_lift(ahps.OP_EXE_SPLIT, params, native_res), data)
                    self.assertEqual(ahps.SIMD_BRIDGE.lift(ahps.OP_EXE_SPLIT, params, python_res, True), data)


class PrngSeedTests(unittest.TestCase):
    def test_inverse_is_exact(self) -> None:
        self.assertEqual((ahps.LCG_MULT * ahps.LCG_INV_MULT) % 65536, 1)

    def test_lcg_seeds_are_recovered_by_every_engine(self) -> None:
        rng = random.Random(1)
        for seed in [rng.randrange(1, 65536) for _ in range(30)]:
            block = lcg_block(seed)
            _, op, params = ahps.py_score_block(block, 0)
            self.assertEqual((op, (params[0] << 8) | params[1]), (ahps.OP_XORSHIFT16, seed))
            if ahps.SIMD_BRIDGE.lib is not None:
                local = ahps.SIMD_BRIDGE.score_all(block, 1024)[1]
                self.assertEqual((local[0], (local[1] << 8) | local[2]), (ahps.OP_XORSHIFT16, seed))

    def test_shifted_program_continues_the_stream(self) -> None:
        for program in ([(ahps.OP_XORSHIFT16, bytes([0x3A, 0x11, 1, 0]))], [(ahps.OP_POLY2, bytes([3, 7, 42, 0]))]):
            data = bytes(5000)
            whole = ahps.py_forward_lift(program[0][0], program[0][1], data)
            tail = ahps.shifted_program(program, 3000)
            self.assertEqual(ahps.py_forward_lift(tail[0][0], tail[0][1], data[3000:]), whole[3000:])


class PlannerTests(unittest.TestCase):
    def test_one_prng_program_spans_a_long_stream(self) -> None:
        data = lcg_block(0x3A00, 40 * 1024)
        for force_python in ENGINES:
            segments = ahps.plan_segments(data, 1024, not force_python)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].program[0][0], ahps.OP_XORSHIFT16)
            self.assertEqual(segments[0].length, len(data))

    def test_negative_estimates_are_not_generator_hits(self) -> None:
        costs = [0.0] * ahps.NUM_SLOTS
        costs[ahps.SLOT_RAW], costs[ahps.SLOT_LOCAL] = -5000.0, -5000.0
        self.assertFalse(ahps.is_generator_hit(costs, 0, ahps.OP_POLY2))
        costs[ahps.SLOT_RAW], costs[ahps.SLOT_LOCAL] = 8000.0, 8000.0
        self.assertFalse(ahps.is_generator_hit(costs, 0, ahps.OP_POLY2))
        costs[ahps.SLOT_LOCAL] = -40.0
        self.assertTrue(ahps.is_generator_hit(costs, 0, ahps.OP_XORSHIFT16))

    def test_records_get_a_stable_chained_program(self) -> None:
        data = records(16 * 1024)
        for force_python in ENGINES:
            segments = ahps.plan_segments(data, 4096, not force_python)
            self.assertEqual(len(segments), 1)
            self.assertEqual([op for op, _ in segments[0].program][-1], ahps.OP_SPLIT)

    def test_incompressible_data_is_never_much_larger_than_xz(self) -> None:
        data = random.Random(6).randbytes(100_000)
        comp, report = ahps.ahps_compress_bytes(data, 4096, False, 0)
        self.assertEqual(list(report.programs), ["RAW"])
        xz = lzma.compress(data, format=lzma.FORMAT_XZ, preset=0)
        self.assertLessEqual(len(comp), len(xz) + 64)

    def test_x86_detection(self) -> None:
        rng = random.Random(8)
        self.assertTrue(ahps.looks_like_x86(x86_code(rng, 64 * 1024)))
        self.assertFalse(ahps.looks_like_x86(rng.randbytes(1 << 20)))
        self.assertFalse(ahps.looks_like_x86(b"plain text without any machine code " * 4096))

    def test_x86_rival_competes_when_the_planner_stays_raw(self) -> None:
        data = x86_code(random.Random(9), 256 * 1024)
        raw_plan = [ahps.Segment(0, len(data), [])]
        plans = {
            "RAW": raw_plan,
            "EXE32": [ahps.Segment(0, len(data), [(ahps.OP_EXE32, bytes(4))])],
            "EXE_SPLIT": [ahps.Segment(0, len(data), [(ahps.OP_EXE_SPLIT, bytes(4))])],
        }
        containers = {name: ahps.pack_container(data, plan, False) for name, plan in plans.items()}
        candidates = {name: lzma.compress(raw, format=lzma.FORMAT_XZ, preset=6)
                      for name, raw in containers.items()}
        winner = min(candidates, key=lambda name: len(candidates[name]))
        trials = []
        real_xz = ahps.xz_compress

        def recording_xz(raw: bytes, preset: int) -> bytes:
            self.assertEqual(preset, 6)
            trials.append(raw)
            return real_xz(raw, preset)

        with mock.patch.object(ahps, "plan_segments", return_value=raw_plan), \
                mock.patch.object(ahps, "xz_compress", side_effect=recording_xz):
            comp, report = ahps.ahps_compress_bytes(data, 4096, True, 6)
        self.assertCountEqual(trials, containers.values())
        self.assertEqual(comp, candidates[winner])
        self.assertEqual(list(report.programs), [winner])
        self.assertEqual(ahps.ahps_decompress_bytes(comp, True), data)

    def test_every_plan_gets_a_real_trial_at_any_size(self) -> None:
        data = x86_code(random.Random(10), 256 * 1024)
        real_xz = ahps.xz_compress
        trials = []

        def recording_xz(raw: bytes, preset: int) -> bytes:
            trials.append(real_xz(raw, preset))
            return trials[-1]

        with mock.patch.object(ahps, "EXTREME_LIMIT", 1024), mock.patch.object(ahps, "xz_compress", recording_xz):
            comp, _ = ahps.ahps_compress_bytes(data, 4096, False, 6)
        self.assertGreaterEqual(len(trials), 3)
        self.assertEqual(len(comp), min(map(len, trials)))
        self.assertEqual(ahps.ahps_decompress_bytes(comp), data)

    def test_large_inputs_choose_a_plan_per_chunk(self) -> None:
        chunk = 64 * 1024
        data = x86_code(random.Random(11), chunk)[:chunk] + records(chunk // 16)[:chunk]
        with mock.patch.object(ahps, "EXTREME_LIMIT", 1024), mock.patch.object(ahps, "PLAN_CHUNK_LEN", chunk):
            comp, report = ahps.ahps_compress_bytes(data, 4096, False, 6)
        self.assertGreater(len(report.programs), 1)
        self.assertEqual(ahps.ahps_decompress_bytes(comp), data)

    def test_chunk_segments_keep_absolute_exe_addresses(self) -> None:
        delta = (ahps.OP_DELTA1_LANE, bytes([4, 0, 0, 0]))
        seg = ahps.Segment(100, 50, [(ahps.OP_EXE_SPLIT, (0x1000).to_bytes(4, "big")), delta])
        moved = ahps.rebased_segment(seg, 0x10000)
        self.assertEqual(moved.start, 0x10064)
        self.assertEqual(moved.program, [(ahps.OP_EXE_SPLIT, (0x11000).to_bytes(4, "big")), delta])

    def test_adjacent_raw_chunks_merge_into_one_segment(self) -> None:
        data = random.Random(12).randbytes(3 * 4096)
        with mock.patch.object(ahps, "PLAN_CHUNK_LEN", 4096):
            plan = ahps.chunked_plan(data, 4096, 1, ahps.SIMD_BRIDGE.lib is not None)
        self.assertEqual([(seg.start, seg.length, seg.program) for seg in plan], [(0, len(data), [])])

    def test_long_segments_are_split_and_still_decode(self) -> None:
        data = records(2048)
        with mock.patch.object(ahps, "MAX_SEGMENT_LEN", 4096):
            comp, report = ahps.ahps_compress_bytes(data, 1024, False, 0)
            self.assertGreater(sum(count for count, _ in report.programs.values()), 1)
            self.assertEqual(ahps.ahps_decompress_bytes(comp), data)


class RoundTripTests(unittest.TestCase):
    def test_every_encoder_decoder_pairing(self) -> None:
        rng = random.Random(2)
        samples = [b"", b"a", bytes(5000), rng.randbytes(5000), lcg_block(99, 3000), records(600), x86ish(rng, 6000)]
        samples.append(records(1200) + x86ish(rng, 4000) + lcg_block(7, 4000) + rng.randbytes(3000))
        for data in samples:
            for block_size in (1, 7, 64, 1024, 4096):
                for enc_py in ENGINES:
                    if enc_py and block_size < 64 and len(data) > 1000:
                        continue
                    comp, _ = ahps.ahps_compress_bytes(data, block_size, enc_py, 0)
                    for dec_py in ENGINES:
                        self.assertEqual(ahps.ahps_decompress_bytes(comp, dec_py), data)

    def test_v3_archives_still_decode(self) -> None:
        data = bytes(range(256)) * 8
        res = ahps.py_forward_lift(ahps.OP_DELTA1_LANE, bytes([1, 1, 0]), data[1024:])
        archive = v3_container(2, 1024, 3, bytes([ahps.OP_RAW, ahps.OP_DELTA1_LANE]) + bytes([1, 1, 0]) + data[:1024] + res)
        for force_python in ENGINES:
            self.assertEqual(ahps.ahps_decompress_bytes(archive, force_python), data)

    def test_compressor_rejects_bad_block_sizes(self) -> None:
        for block_size in (0, -1, ahps.MAX_BLOCK_SIZE + 1):
            with self.assertRaises(ValueError):
                ahps.ahps_compress_bytes(b"data", block_size)


class CraftedArchiveTests(unittest.TestCase):
    def test_old_v4_exe_split_lane_fixture_fails_without_decoder_guessing(self) -> None:
        # Earlier v2.5 wrote separate high-to-low target byte lanes. Three
        # zero-displacement calls target 5, 10 and 15. This fixture is hand
        # derived from that documented layout, not generated by today's code.
        original = bytes.fromhex("e800000000 e800000000 e800000000")
        old_residual = bytes.fromhex("e8e8e8 000000 000000 000000 050a0f")
        archive = crafted_segments(
            [(original, [(ahps.OP_EXE_SPLIT, bytes(4))], old_residual)], b"AHPS\x04"
        )
        for force_python in ENGINES:
            with self.subTest(force_python=force_python):
                with self.assertRaisesRegex(ValueError, "unsupported older|older.*unsupported"):
                    ahps.ahps_decompress_bytes(archive, force_python)

    def assert_valid_archives_decode(self, force_python: bool) -> None:
        raw = b"raw segment\x00\xff"
        chain = (EXE_SPLIT_CHAIN_DATA, EXE_SPLIT_CHAIN, EXE_SPLIT_CHAIN_RESIDUAL)
        cases = [("empty", []), ("raw", [(raw, [], raw)]), ("three-step chain", [chain])]
        transformed_parts = []
        for name, base, data, residual in EXE_SPLIT_VECTORS:
            if not data:
                continue
            part = (data, [(ahps.OP_EXE_SPLIT, base.to_bytes(4, "big"))], residual)
            cases.append((name, [part]))
            transformed_parts.append(part)
        cases.append(("mixed raw, EXE_SPLIT and chain segments", [(raw, [], raw), *transformed_parts, chain]))

        # Literal v4 is a permanent compatibility target; the other value tracks
        # the active writer, so a future version is checked separately as well.
        for magic in dict.fromkeys((b"AHPS\x04", ahps.MAGIC_HEADER)):
            for name, parts in cases:
                with self.subTest(version=magic, case=name, force_python=force_python):
                    expected = b"".join(data for data, _, _ in parts)
                    archive = crafted_segments(parts, magic)
                    self.assertEqual(ahps.ahps_decompress_bytes(archive, force_python), expected)

    def test_python_valid_v4_and_current_archives_with_correct_crc(self) -> None:
        self.assert_valid_archives_decode(True)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "native archive coverage requires the C kernel")
    def test_native_valid_v4_and_current_archives_with_correct_crc(self) -> None:
        self.assert_valid_archives_decode(False)

    def assert_rejected(self, archive: bytes) -> None:
        for force_python in ENGINES:
            with self.assertRaises(ValueError):
                ahps.ahps_decompress_bytes(archive, force_python)

    def test_short_or_wrong_header(self) -> None:
        self.assert_rejected(wrap(b""))
        self.assert_rejected(wrap(b"AHPS"))
        self.assert_rejected(wrap(ahps.MAGIC_HEADER))
        self.assert_rejected(wrap(ahps.MAGIC_HEADER_V3))
        self.assert_rejected(wrap(b"AHPS\x02" + bytes(100)))

    def test_v3_block_count_past_the_data(self) -> None:
        self.assert_rejected(v3_container(4000, 1024, 0, bytes(5000)))

    def test_v3_block_count_too_small(self) -> None:
        self.assert_rejected(v3_container(1, 1024, 0, bytes(5000)))

    def test_v3_block_size_out_of_range(self) -> None:
        self.assert_rejected(v3_container(4, 0, 0, bytes(5000)))
        self.assert_rejected(v3_container(4, 0xFFFFF000, 0, bytes(5000)))

    def test_v3_tape_overruns_container(self) -> None:
        self.assert_rejected(v3_container(0xFFFFFFFF, 1024, 0, bytes(100)))
        self.assert_rejected(v3_container(1, 1024, 0xFFFFFFFF, bytes(100)))

    def test_v3_unknown_opcode(self) -> None:
        self.assert_rejected(v3_container(1, 1024, 3, b"\x7E" + bytes(3) + bytes(1024)))

    def test_v3_param_length_mismatch(self) -> None:
        self.assert_rejected(v3_container(1, 1024, 0, b"\x01" + bytes(1024)))
        self.assert_rejected(v3_container(1, 1024, 3, b"\x00" + bytes(3) + bytes(1024)))

    def test_v4_lengths_must_match_the_container(self) -> None:
        table = struct.pack("<IB", 100, 0)
        self.assert_rejected(v4_container(1, table, 200, bytes(100)))
        self.assert_rejected(v4_container(1, table, 100, bytes(200)))
        self.assert_rejected(v4_container(1, table, 1 << 62, bytes(100)))

    def test_v4_table_must_cover_the_residual(self) -> None:
        self.assert_rejected(v4_container(1, struct.pack("<IB", 50, 0), 100, bytes(100)))
        self.assert_rejected(v4_container(0xFFFFFFFF, struct.pack("<IB", 100, 0), 100, bytes(100)))
        self.assert_rejected(v4_container(1, struct.pack("<IB", 100, 0) + bytes(5), 100, bytes(100)))
        self.assert_rejected(v4_container(2, struct.pack("<IB", 100, 0), 100, bytes(100)))

    def test_v4_bad_segment_lengths(self) -> None:
        self.assert_rejected(v4_container(1, struct.pack("<IB", 0, 0), 0, b""))
        big = ahps.MAX_SEGMENT_LEN + 1
        self.assert_rejected(v4_container(1, struct.pack("<IB", big, 0), 0, b""))

    def test_v4_bad_programs(self) -> None:
        head = struct.pack("<I", 100)
        self.assert_rejected(v4_container(1, head + b"\x01" + step(0x7E), 100, bytes(100)))
        self.assert_rejected(v4_container(1, head + b"\x01" + step(ahps.OP_RAW), 100, bytes(100)))
        self.assert_rejected(v4_container(1, head + b"\x01" + step(ahps.OP_EXE_REL32), 100, bytes(100)))
        self.assert_rejected(v4_container(1, head + b"\x02" + step(ahps.OP_SPLIT), 100, bytes(100)))
        too_long = head + b"\x04" + step(ahps.OP_SPLIT) * 4
        self.assert_rejected(v4_container(1, too_long, 100, bytes(100)))

    def test_v4_crc_detects_a_changed_residual(self) -> None:
        part = (EXE_SPLIT_CHAIN_DATA, EXE_SPLIT_CHAIN, EXE_SPLIT_CHAIN_RESIDUAL)
        archive = crafted_segments([part], b"AHPS\x04")
        self.assertEqual(ahps.ahps_decompress_bytes(archive, True), EXE_SPLIT_CHAIN_DATA)
        raw = bytearray(lzma.decompress(archive))
        raw[-1] ^= 0x55
        self.assert_rejected(wrap(bytes(raw)))

    def test_v4_crc_detects_a_changed_program(self) -> None:
        data = bytes(1000)
        table = struct.pack("<IB", 1000, 1) + step(ahps.OP_DELTA1_LANE, bytes([1, 5, 0, 0]))
        self.assert_rejected(v4_container(1, table, 1000, data, zlib.crc32(data)))

    def test_truncated_xz_stream(self) -> None:
        comp, _ = ahps.ahps_compress_bytes(bytes(5000), 1024, True, 0)
        with self.assertRaises((ValueError, lzma.LZMAError)):
            ahps.ahps_decompress_bytes(comp[: len(comp) // 2])


if __name__ == "__main__":
    unittest.main()
