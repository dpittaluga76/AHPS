"""Independent arithmetic and wire checks for the fixed table comparison."""

import lzma
import random
import struct
import unittest
from unittest import mock
import zlib

import ahps
from research import relational
from research import compare_same_boundary_tables as comparison


FIXED_METHODS = (
    "ahps_raw", "ahps_split12", "ahps_delta1_split12",
    "ahps_delta2_split12", "ahrl_word_delta",
)
PARAMS = bytes((12, 0, 0, 0))
PROGRAMS = {
    "ahps_raw": [],
    "ahps_split12": [(7, PARAMS)],
    "ahps_delta1_split12": [(2, PARAMS), (7, PARAMS)],
    "ahps_delta2_split12": [(3, PARAMS), (7, PARAMS)],
}


def records(rows):
    return b"".join(struct.pack("<III", *row) for row in rows)


def shuffle12(data):
    return b"".join(data[lane::12] for lane in range(12))


def unshuffle12(data):
    count = len(data) // 12
    return bytes(data[lane * count + row] for row in range(count) for lane in range(12))


def xz(body):
    return lzma.compress(body, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=0)


def ahps_wire(original, segments, residual=None, crc=None):
    table = bytearray()
    for length, program in segments:
        table.extend(struct.pack("<IB", length, len(program)))
        for opcode, params in program:
            table.extend(bytes((opcode,)) + params)
    header = b"AHPS\x05" + struct.pack(
        "<IIQI", len(segments), len(table), len(original),
        zlib.crc32(original) if crc is None else crc,
    )
    return xz(header + table + (original if residual is None else residual))


class TableArithmeticTests(unittest.TestCase):
    def test_fixed_methods_and_programs_are_the_declared_candidates(self):
        self.assertEqual(tuple(comparison.FIXED_METHODS), FIXED_METHODS)
        self.assertIn(12, ahps.STRIDES)
        for method, expected in PROGRAMS.items():
            with self.subTest(method=method):
                self.assertEqual(comparison.fixed_program(method), expected)
        for method in ("ahrl_word_delta", "ahps_auto", "xz", "unknown"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                comparison.fixed_program(method)

    def test_split_matches_independent_lane_order_and_ahrl_permutation(self):
        original = bytes(range(60))
        archive = comparison.encode_fixed(original, "ahps_split12", preset=0, use_c=False)
        residual, _ = comparison.extract_fixed_residual(archive, "ahps_split12", use_c=False)
        self.assertEqual(residual, shuffle12(original))
        ahrl = lzma.decompress(relational.encode(original, 4, 3, "shuffle", preset=0))
        self.assertEqual(ahrl[:12], b"AHRL\0\0\0\1" + bytes((4, 3, 1, 0)))
        self.assertEqual(residual, ahrl[24:])

    def test_carry_borrow_and_wrap_differ_only_in_subtraction_arithmetic(self):
        original = records(((0xFF, 0x100, 0xFFFFFFFF), (0x100, 0xFF, 0)))
        expected_byte = original[:12] + bytes.fromhex("01010000 ffff0000 01010101")
        expected_word = original[:12] + records(((1, 0xFFFFFFFF, 1),))
        for method, expected in (("ahps_delta1_split12", expected_byte),
                                  ("ahrl_word_delta", expected_word)):
            with self.subTest(method=method):
                archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
                residual, _ = comparison.extract_fixed_residual(archive, method, use_c=False)
                self.assertEqual(residual, shuffle12(expected))
                self.assertEqual(unshuffle12(residual)[:12], original[:12])
                self.assertEqual(comparison.restore_fixed_residual(residual, method, use_c=False), original)
        self.assertNotEqual(expected_byte[12:], expected_word[12:])

    def test_d1_and_word_delta_are_identical_without_carries_or_borrows(self):
        original = records(((3, 6, 9), (5, 9, 13), (7, 12, 17)))
        expected = shuffle12(records(((3, 6, 9), (2, 3, 4), (2, 3, 4))))
        for method in ("ahps_delta1_split12", "ahrl_word_delta"):
            archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
            residual, _ = comparison.extract_fixed_residual(archive, method, use_c=False)
            with self.subTest(method=method):
                self.assertEqual(residual, expected)

    def test_delta2_is_an_existing_rival_with_two_records_of_warmup(self):
        original = records(((10, 3, 7), (20, 10, 4), (35, 25, 1)))
        expected = records(((10, 3, 7), (20, 10, 4), (5, 8, 0)))
        archive = comparison.encode_fixed(original, "ahps_delta2_split12", preset=0, use_c=False)
        residual, _ = comparison.extract_fixed_residual(archive, "ahps_delta2_split12", use_c=False)
        self.assertEqual(residual, shuffle12(expected))
        self.assertEqual(unshuffle12(residual)[:24], original[:24])
        self.assertEqual(comparison.restore_fixed_residual(residual, "ahps_delta2_split12", use_c=False), original)

    def test_all_candidates_round_trip_single_and_multiple_records(self):
        rng = random.Random(612031)
        for count in (1, 2, 3, 257):
            original = rng.randbytes(12 * count)
            for method in FIXED_METHODS:
                with self.subTest(count=count, method=method):
                    archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
                    residual, metadata = comparison.extract_fixed_residual(archive, method, use_c=False)
                    self.assertIsInstance(metadata, dict)
                    self.assertEqual(len(residual), len(original))
                    self.assertEqual(comparison.restore_fixed_residual(residual, method, use_c=False), original)
                    restored = (relational.decode(archive) if method == "ahrl_word_delta"
                                else ahps.ahps_decompress_bytes(archive, force_python=True))
                    self.assertEqual(restored, original)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires the native kernel")
    def test_native_and_python_fixed_archive_and_inverse_bytes_match(self):
        original = random.Random(2814).randbytes(12 * 97)
        for method in FIXED_METHODS:
            with self.subTest(method=method):
                python_archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
                native_archive = comparison.encode_fixed(original, method, preset=0, use_c=True)
                self.assertEqual(native_archive, python_archive)
                native_residual, _ = comparison.extract_fixed_residual(native_archive, method, use_c=True)
                python_residual, _ = comparison.extract_fixed_residual(python_archive, method, use_c=False)
                self.assertEqual(native_residual, python_residual)
                self.assertEqual(comparison.restore_fixed_residual(native_residual, method, use_c=True), original)

    def test_existing_archives_have_one_exact_canonical_segment(self):
        original = bytes(range(36))
        for method, expected_program in PROGRAMS.items():
            archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
            body = lzma.decompress(archive)
            with self.subTest(method=method):
                self.assertEqual(body[:5], b"AHPS\x05")
                count, table_length, total, crc = struct.unpack_from("<IIQI", body, 5)
                self.assertEqual((count, table_length, total, crc),
                                 (1, 5 + 5 * len(expected_program), len(original), zlib.crc32(original)))
                expected_table = struct.pack("<IB", len(original), len(expected_program))
                expected_table += b"".join(bytes((op,)) + params for op, params in expected_program)
                self.assertEqual(body[25:25 + table_length], expected_table)

    def test_payload_inverse_does_not_call_encoder_or_planner(self):
        original = records(((255, 65535, 0), (256, 65536, 0xFFFFFFFF)))
        residual = shuffle12(records(((255, 65535, 0), (1, 1, 0xFFFFFFFF))))
        with mock.patch.object(comparison, "encode_fixed", side_effect=AssertionError("encoder used")), \
                mock.patch.object(ahps, "plan_segments", side_effect=AssertionError("planner used")), \
                mock.patch.object(relational, "_adaptive_models", side_effect=AssertionError("model search used")):
            self.assertEqual(comparison.restore_fixed_residual(residual, "ahrl_word_delta", use_c=False), original)


class TableMethodValidationTests(unittest.TestCase):
    def test_empty_partial_and_unknown_inputs_are_rejected(self):
        for method in FIXED_METHODS:
            for invalid in (b"", b"x", bytes(11), bytes(13)):
                with self.subTest(method=method, size=len(invalid)):
                    with self.assertRaises(ValueError):
                        comparison.encode_fixed(invalid, method, preset=0, use_c=False)
                    with self.assertRaises(ValueError):
                        comparison.restore_fixed_residual(invalid, method, use_c=False)
        for method in ("unknown", "xz", "ahps_auto"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                comparison.encode_fixed(bytes(12), method, preset=0, use_c=False)

    def test_size_limit_is_checked_without_allocating_a_large_fixture(self):
        self.assertEqual(comparison.TABLE_LIMIT, 8 * 1024**2)
        for method in FIXED_METHODS:
            with self.subTest(method=method), mock.patch.object(comparison, "TABLE_LIMIT", 24):
                with self.assertRaises(ValueError):
                    comparison.encode_fixed(bytes(36), method, preset=0, use_c=False)
                with self.assertRaises(ValueError):
                    comparison.restore_fixed_residual(bytes(36), method, use_c=False)

    def test_valid_archives_cannot_be_mislabeled_as_another_fixed_method(self):
        original = bytes(range(24))
        archives = {method: comparison.encode_fixed(original, method, preset=0, use_c=False)
                    for method in FIXED_METHODS}
        for encoded_method, archive in archives.items():
            for declared_method in FIXED_METHODS:
                if encoded_method == declared_method:
                    continue
                with self.subTest(encoded=encoded_method, declared=declared_method), self.assertRaises(ValueError):
                    comparison.extract_fixed_residual(archive, declared_method, use_c=False)

    def test_multiple_segments_and_noncanonical_parameters_are_rejected(self):
        original = bytes(range(24))
        cases = (
            (ahps_wire(original, ((12, []), (12, []))), "ahps_raw"),
            (ahps_wire(original, ((24, [(7, bytes((12, 1, 0, 0)))]),),
                       residual=shuffle12(original)), "ahps_split12"),
            (ahps_wire(original, ((24, [(7, bytes((4, 0, 0, 0)))]),)), "ahps_split12"),
        )
        for archive, method in cases:
            with self.subTest(method=method), self.assertRaises(ValueError):
                comparison.extract_fixed_residual(archive, method, use_c=False)

    def test_ahrl_layout_and_models_must_match_the_fixed_word_delta(self):
        original = bytes(range(24))
        cases = (
            relational.encode(original, 2, 6, "delta", preset=0),
            relational.encode(original, 4, 3, "raw", preset=0),
            relational.encode(original, 4, 3, "shuffle", preset=0),
            relational.encode(original, 4, 3, "cross", preset=0),
        )
        for archive in cases:
            with self.subTest(size=len(archive)), self.assertRaises(ValueError):
                comparison.extract_fixed_residual(archive, "ahrl_word_delta", use_c=False)

    def test_restored_checksum_is_verified_before_exposing_residual(self):
        original = bytes(range(36))
        for method in FIXED_METHODS:
            archive = comparison.encode_fixed(original, method, preset=0, use_c=False)
            body = bytearray(lzma.decompress(archive))
            body[-1] ^= 1  # Re-encode valid outer XZ so this checks restored-data CRC.
            with self.subTest(method=method), self.assertRaises(ValueError):
                comparison.extract_fixed_residual(xz(body), method, use_c=False)

    def test_truncated_corrupt_trailing_and_concatenated_xz_are_rejected(self):
        for method in ("ahps_raw", "ahrl_word_delta"):
            archive = comparison.encode_fixed(bytes(range(24)), method, preset=0, use_c=False)
            changed = bytearray(archive)
            changed[len(changed) // 2] ^= 0x40
            for invalid in (b"", archive[:-1], bytes(changed), archive + b"x", archive + archive):
                with self.subTest(method=method, length=len(invalid)), self.assertRaises((ValueError, lzma.LZMAError)):
                    comparison.extract_fixed_residual(invalid, method, use_c=False)

    def test_forged_small_headers_cannot_trigger_unbounded_xz_expansion(self):
        original = bytes(12)
        ahps_header = b"AHPS\x05" + struct.pack("<IIQI", 1, 5, 12, zlib.crc32(original))
        ahps_header += struct.pack("<IB", 12, 0)
        ahrl_header = struct.pack("<8sBBBBQI", b"AHRL\0\0\0\1", 4, 3, 2, 0, 12,
                                  zlib.crc32(original))
        ahrl_header += struct.pack("<BBQQ", 1, 0, 0, 0) * 3
        cases = (("ahps_raw", xz(ahps_header + bytes(1024**2)), 24 + 40),
                 ("ahrl_word_delta", xz(ahrl_header + bytes(1024**2)), 24 + 24 + 16 * 18))
        real_decoder = lzma.LZMADecompressor
        for method, archive, expansion_cap in cases:
            calls = []

            class BoundedDecoder:
                def __init__(self, *args, **kwargs):
                    self.decoder = real_decoder(*args, **kwargs)
                    self.memlimit = kwargs.get("memlimit")

                def decompress(self, data, max_length=-1):
                    result = self.decoder.decompress(data, max_length=max_length)
                    calls.append((max_length, len(result), self.memlimit))
                    return result

                def __getattr__(self, name):
                    return getattr(self.decoder, name)

            with self.subTest(method=method), mock.patch.object(comparison, "TABLE_LIMIT", 24), \
                    mock.patch.object(lzma, "LZMADecompressor", BoundedDecoder):
                with self.assertRaises(ValueError):
                    comparison.extract_fixed_residual(archive, method, use_c=False)
                self.assertTrue(calls)
                self.assertTrue(all(0 < requested <= expansion_cap + 1 for requested, _, _ in calls))
                self.assertLessEqual(sum(actual for _, actual, _ in calls), expansion_cap + 1)
                self.assertTrue(all(limit == 128 * 1024**2 for _, _, limit in calls))


if __name__ == "__main__":
    unittest.main()
