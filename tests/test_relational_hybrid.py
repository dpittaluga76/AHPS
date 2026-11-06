"""Independent wire, arithmetic, boundary, and decoder checks for AHRH1."""

import lzma
import os
import random
import struct
import unittest
from unittest import mock
import zlib

import ahps
from research import relational_hybrid as hybrid


PARAMS12 = b"\x0c\x00\x00\x00"
WORD = [(10, PARAMS12), (7, PARAMS12)]
BYTE = [(2, PARAMS12), (7, PARAMS12)]


def setUpModule():
    if os.name == "nt":
        from research.compare_relational_corpus import set_below_normal_priority, priority_class
        set_below_normal_priority()
        if priority_class() != 0x4000:
            raise RuntimeError("hybrid tests require below-normal priority")


def xz(body, check=lzma.CHECK_CRC64):
    return lzma.compress(body, format=lzma.FORMAT_XZ, check=check, preset=0)


def records(rows):
    return b"".join(struct.pack("<III", *row) for row in rows)


def shuffle12(data):
    return b"".join(data[lane::12] for lane in range(12))


def wire(original, segments=None, residual=None, **changes):
    """Build independent trusted-small or deliberately malformed container bytes."""
    if segments is None:
        segments = [(len(original), [])] if original else []
    tape = bytearray()
    for length, program in segments:
        tape.extend(struct.pack("<IB", length, len(program)))
        for opcode, params in program:
            tape.extend(bytes((opcode,)) + params)
    fields = dict(magic=b"AHRH\x01", count=len(segments), table_bytes=len(tape),
                  total=len(original), crc=zlib.crc32(original))
    fields.update(changes)
    header = fields["magic"] + struct.pack("<IIQI", fields["count"],
               fields["table_bytes"], fields["total"], fields["crc"])
    return xz(header + tape + (original if residual is None else residual))


def body_residual(archive):
    body = lzma.decompress(archive)
    return body[25 + struct.unpack_from("<I", body, 9)[0]:]


def plan_tuples(segments):
    return [(s.start, s.length, list(s.program)) for s in segments]


class HybridWireArithmeticTests(unittest.TestCase):
    def test_wire_header_program_and_word_carry_borrow_wrap(self):
        source = records(((255, 256, 0xFFFFFFFF), (256, 255, 0)))
        residual = shuffle12(source[:12] + records(((1, 0xFFFFFFFF, 1),)))
        expected = wire(source, [(24, WORD)], residual)
        actual = hybrid.encode_plan(source, [ahps.Segment(0, 24, WORD)], use_c=False, preset=0)
        self.assertEqual(actual, expected)
        body = lzma.decompress(actual)
        self.assertEqual(body[:5], b"AHRH\x01")
        self.assertEqual(struct.unpack_from("<IIQI", body, 5), (1, 15, 24, zlib.crc32(source)))
        self.assertEqual(body[25:40], struct.pack("<IB", 24, 2) + b"\x0a" + PARAMS12 + b"\x07" + PARAMS12)
        self.assertEqual(hybrid.decode(expected, use_c=False), source)

    def test_byte_control_matches_lane_delta_and_has_same_research_framing(self):
        source = records(((255, 256, 0xFFFFFFFF), (256, 255, 0)))
        residual = shuffle12(source[:12] + bytes.fromhex("01010000 ffff0000 01010101"))
        archive = hybrid.encode_plan(source, [ahps.Segment(0, 24, BYTE)], use_c=False, preset=0)
        self.assertEqual(archive, wire(source, [(24, BYTE)], residual))
        self.assertEqual(hybrid.decode(archive, use_c=False), source)

    def test_empty_source_has_only_the_25_byte_header(self):
        actual = hybrid.encode_plan(b"", [], use_c=False, preset=0)
        self.assertEqual(actual, wire(b""))
        self.assertEqual(len(lzma.decompress(actual)), 25)
        self.assertEqual(hybrid.decode(actual, use_c=False, max_output_bytes=0), b"")

    def test_word_first_record_and_fields_remain_independent(self):
        source = records(((0xFFFFFFFE, 9, 50), (1, 9, 45), (0, 12, 48)))
        expected = shuffle12(records(((0xFFFFFFFE, 9, 50), (3, 0, 0xFFFFFFFB),
                                      (0xFFFFFFFF, 3, 3))))
        archive = hybrid.encode_plan(source, [ahps.Segment(0, 36, WORD)], use_c=False, preset=0)
        self.assertEqual(body_residual(archive), expected)
        self.assertEqual(hybrid.decode(archive, use_c=False), source)

    def test_mixed_segment_roundtrips_use_one_solid_stream(self):
        rng = random.Random(6103)
        for count in (1, 2, 17):
            source = b"prefix!" + rng.randbytes(12 * count) + b"suffix\xe8\x01\0\0\0"
            plan = [ahps.Segment(0, 7, []), ahps.Segment(7, 12 * count, WORD),
                    ahps.Segment(7 + 12 * count, len(source) - 7 - 12 * count, [(8, b"\x01\x02\x03\x04")])]
            with self.subTest(records=count):
                archive = hybrid.encode_plan(source, plan, use_c=False, preset=0)
                decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
                decoder.decompress(archive)
                self.assertTrue(decoder.eof)
                self.assertEqual(decoder.unused_data, b"")
                self.assertEqual(hybrid.decode(archive, use_c=False), source)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires native ABI 206")
    def test_native_and_python_archives_and_decoders_match(self):
        source = b"\xe8\xff\xff\xff\xff\x90\x48\x8d\x05\x01\0\0\0" + random.Random(431).randbytes(83)
        for opcode in (8, 9):
            plan = [ahps.Segment(0, 24, [(opcode, b"\xff\xff\xff\xf0")]),
                    ahps.Segment(24, 48, WORD), ahps.Segment(72, 24, BYTE)]
            with self.subTest(opcode=opcode):
                py = hybrid.encode_plan(source, plan, use_c=False, preset=0)
                native = hybrid.encode_plan(source, plan, use_c=True, preset=0)
                self.assertEqual(native, py)
                self.assertEqual(hybrid.decode(native, use_c=True), source)
                self.assertEqual(hybrid.decode(native, use_c=False), source)

    def test_decoder_uses_recorded_programs_without_planning_or_encoding(self):
        source = records(((255, 7, 8), (256, 9, 2)))
        residual = shuffle12(source[:12] + records(((1, 2, 0xFFFFFFFA),)))
        archive = wire(source, [(24, WORD)], residual)
        with mock.patch.object(hybrid, "baseline_archive", side_effect=AssertionError("baseline called")), \
                mock.patch.object(hybrid, "encode_plan", side_effect=AssertionError("encode called")), \
                mock.patch.object(ahps, "plan_segments", side_effect=AssertionError("planner called")):
            self.assertEqual(hybrid.decode(archive, use_c=False), source)

    def test_v5_plan_recovery_preserves_actual_segments_and_parameters(self):
        source = bytes(range(36))
        program = [(8, b"\x12\x34\x56\x78")]
        archive = xz(ahps.pack_container(source, [ahps.Segment(0, 12, []),
                                                ahps.Segment(12, 24, program)], False))
        self.assertEqual(plan_tuples(hybrid.baseline_plan(archive)), [(0, 12, []), (12, 24, program)])
        self.assertEqual(hybrid.decode(archive, use_c=False), source)

    def test_inspection_distinguishes_structural_validation_from_crc_verification(self):
        archive = wire(bytes(range(24)))
        self.assertFalse(hybrid.inspect_archive(archive, use_c=False)["crc32_verified"])
        self.assertTrue(hybrid.inspect_archive(archive, use_c=False, verify_crc=True)["crc32_verified"])
        corrupt = wire(bytes(range(24)), crc=0)
        self.assertFalse(hybrid.inspect_archive(corrupt, use_c=False)["crc32_verified"])
        with self.assertRaises(ValueError):
            hybrid.inspect_archive(corrupt, use_c=False, verify_crc=True)


class HybridBoundarySelectionTests(unittest.TestCase):
    def test_table_replaces_multiple_baseline_segments_with_identical_outside_plan(self):
        original = [ahps.Segment(0, 8, [(2, b"\x02\0\0\0")]),
                    ahps.Segment(8, 12, []), ahps.Segment(20, 12, [(7, b"\x04\0\0\0")]),
                    ahps.Segment(32, 8, [])]
        before = plan_tuples(original)
        byte = hybrid.aligned_plan(original, 40, 4, 24, word=False)
        word = hybrid.aligned_plan(original, 40, 4, 24, word=True)
        self.assertEqual(plan_tuples(original), before)
        self.assertEqual([(s.start, s.length) for s in byte], [(0, 4), (4, 24), (28, 4), (32, 8)])
        self.assertEqual(byte[1].program, BYTE)
        self.assertEqual(word[1].program, WORD)
        self.assertEqual(plan_tuples(byte[:1] + byte[2:]), plan_tuples(word[:1] + word[2:]))
        self.assertEqual(byte[0].program, original[0].program)
        self.assertEqual(byte[2].program, original[2].program)
        word_before = plan_tuples(word)
        byte[0].program.append((7, PARAMS12))
        byte[2].start = 29
        self.assertEqual(plan_tuples(original), before)
        self.assertEqual(plan_tuples(word), word_before)

    def test_executable_fragment_bases_use_original_segment_relative_offset(self):
        for opcode in (8, 9):
            source_plan = [ahps.Segment(0, 7, []),
                           ahps.Segment(7, 61, [(opcode, b"\xff\xff\xff\xf0")])]
            result = hybrid.aligned_plan(source_plan, 68, 19, 24, word=True)
            with self.subTest(opcode=opcode):
                self.assertEqual(plan_tuples(result), [(0, 7, []),
                    (7, 12, [(opcode, b"\xff\xff\xff\xf0")]), (19, 24, WORD),
                    (43, 25, [(opcode, b"\0\0\0\x14")])])

    def test_fragments_restart_scanners_and_transform_original_bytes(self):
        # The baseline call at index2 crosses the new table boundary. Its lifted
        # target bytes cannot be reused in either independently transformed part.
        table = records(((0, 0, 0), (1, 2, 3)))
        source = b"\x90\x90\xe8\x01" + table + b"\xe8\x02\0\0\0\x90"
        base = 0x12345678
        original = [ahps.Segment(0, len(source), [(8, base.to_bytes(4, "big"))])]
        plan = hybrid.aligned_plan(original, len(source), 4, 24, word=True)
        archive = hybrid.encode_plan(source, plan, use_c=False, preset=0)
        expected_suffix = b"\xe8" + (base + 28 + 5 + 2).to_bytes(4, "big") + b"\x90"
        self.assertEqual(body_residual(archive), source[:4] + shuffle12(table) + expected_suffix)
        self.assertEqual(hybrid.decode(archive, use_c=False), source)
        old_residual = ahps.apply_program(original[0].program, source, False)
        self.assertNotEqual(body_residual(archive)[:4], old_residual[:4])

    def test_selection_preserves_original_bytes_on_ties_and_uses_stable_order(self):
        cases = [(b"original", b"byte----", b"word----", "baseline", b"original"),
                 (b"original", b"byte", b"word", "byte", b"byte"),
                 (b"original", b"byte", b"w", "word", b"w"),
                 (b"original", b"too-long-byte", b"too-long-word", "baseline", b"original")]
        for baseline, byte, word, name, expected in cases:
            with self.subTest(selected=name):
                self.assertEqual(hybrid.select_archive(baseline, byte, word), (expected, name))

    def test_convenience_encoder_calls_baseline_once_and_preserves_unavailable_fallback(self):
        source = bytes(range(36))
        with mock.patch.object(hybrid, "baseline_archive", wraps=hybrid.baseline_archive) as baseline, \
                mock.patch.object(hybrid, "encode_plan", wraps=hybrid.encode_plan) as candidates:
            archive, metadata = hybrid.encode(source, use_c=False, preset=0)
            self.assertEqual(baseline.call_count, 1)
            self.assertEqual(candidates.call_count, 0)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata["selected_name"], "baseline")
        self.assertEqual(archive, hybrid.baseline_archive(source, use_c=False, preset=0)[0])
        self.assertEqual(hybrid.decode(archive, use_c=False), source)

    def test_baseline_wrapper_calls_unchanged_compressor_once_with_frozen_search(self):
        source = bytes(range(24))
        with mock.patch.object(ahps, "ahps_compress_bytes", wraps=ahps.ahps_compress_bytes) as compressor:
            archive, _ = hybrid.baseline_archive(source, use_c=False, preset=0)
        compressor.assert_called_once_with(source, block_size=4096, force_python=True,
                                           lzma_preset=0, search=ahps.FAST_CHUNKS)
        self.assertEqual(lzma.decompress(archive)[:5], b"AHPS\x05")

    def test_candidate_errors_propagate_instead_of_disguised_fallback(self):
        with mock.patch.object(hybrid, "encode_plan", side_effect=ValueError("candidate failed")), \
                self.assertRaisesRegex(ValueError, "candidate failed"):
            hybrid.encode(bytes(range(24)), table_start=0, table_length=24, use_c=False, preset=0)

    def test_convenience_encoder_builds_two_candidates_from_source_and_never_grows(self):
        source = b"prefix!" + records(tuple((x, x + 10, x + 30) for x in range(8))) + b"suffix"
        baseline_archive, _ = hybrid.baseline_archive(source, use_c=False, preset=0)
        with mock.patch.object(hybrid, "baseline_archive", wraps=hybrid.baseline_archive) as baseline, \
                mock.patch.object(hybrid, "encode_plan", wraps=hybrid.encode_plan) as candidates:
            archive, metadata = hybrid.encode(source, table_start=7, table_length=96, use_c=False, preset=0)
            self.assertEqual(baseline.call_count, 1)
            self.assertEqual(candidates.call_count, 2)
            self.assertTrue(all(call.args[0] == source for call in candidates.call_args_list))
        self.assertIsInstance(metadata, dict)
        self.assertEqual(set(metadata["candidate_bytes"]), {"baseline", "byte", "word"})
        self.assertEqual(metadata["candidate_bytes"]["baseline"], len(baseline_archive))
        self.assertEqual(metadata["candidate_bytes"][metadata["selected_name"]], len(archive))
        self.assertEqual(len(archive), min(metadata["candidate_bytes"].values()))
        self.assertLessEqual(len(archive), len(baseline_archive))
        self.assertEqual(hybrid.decode(archive, use_c=False), source)


class HybridValidationTests(unittest.TestCase):
    def test_constants_match_frozen_research_limits(self):
        self.assertEqual((hybrid.FILE_CAP, hybrid.TABLE_CAP, hybrid.COMPRESSED_CAP,
                          hybrid.SEGMENT_CAP, hybrid.SEGMENT_TABLE_CAP),
                         (128 * 1024**2, 8 * 1024**2, 160 * 1024**2, 65536, 20 * 65536))

    def test_native_requires_actual_abi_206_and_has_no_silent_fallback(self):
        incompatible = mock.Mock()
        incompatible.ahps_abi_version.return_value = 205
        archive = wire(bytes(12))
        for library in (None, incompatible):
            with self.subTest(library=library), mock.patch.object(ahps.SIMD_BRIDGE, "lib", library):
                with self.assertRaises(RuntimeError):
                    hybrid.encode_plan(bytes(12), [ahps.Segment(0, 12, [])], use_c=True, preset=0)
                with self.assertRaises(RuntimeError):
                    hybrid.decode(archive, use_c=True)
                self.assertEqual(hybrid.decode(archive, use_c=False), bytes(12))

    def test_malformed_word_programs_and_multiple_word_segments_are_rejected(self):
        source = bytes(24)
        bad = [[(10, PARAMS12)], [(7, PARAMS12), (10, PARAMS12)],
               [(10, b"\x04\0\0\0"), (7, PARAMS12)],
               [(10, b"\x0c\x01\0\0"), (7, PARAMS12)],
               [(10, PARAMS12), (7, b"\x04\0\0\0")],
               [(10, PARAMS12), (7, PARAMS12), (7, PARAMS12)]]
        for program in bad:
            with self.subTest(program=program):
                with self.assertRaises(ValueError):
                    hybrid.decode(wire(source, [(24, program)]), use_c=False)
                with self.assertRaises(ValueError):
                    hybrid.encode_plan(source, [ahps.Segment(0, 24, program)], use_c=False, preset=0)
        for archive in (wire(bytes(13), [(13, WORD)]), wire(source, [(12, WORD), (12, WORD)]),
                        wire(source, [(24, WORD)], magic=b"AHPS\x05")):
            with self.assertRaises(ValueError):
                hybrid.decode(archive, use_c=False)

    def test_unknown_opcodes_and_all_descriptors_are_rejected_before_native(self):
        for opcode in (0, 4, 11, 255):
            archive = wire(bytes(24), [(12, [(8, bytes(4))]), (12, [(opcode, bytes(4))])])
            with self.subTest(opcode=opcode), \
                    mock.patch.object(ahps, "lift_step", side_effect=AssertionError("native reached")), \
                    self.assertRaises(ValueError):
                hybrid.decode(archive, use_c=False)

    def test_magic_crc_and_coverage_are_strict(self):
        source = bytes(range(24))
        cases = [wire(source, magic=b"AHPS\x04"), wire(source, magic=b"AHRH\x02"),
                 wire(source, crc=zlib.crc32(source) ^ 1), wire(source, [(12, [])]),
                 wire(source, [(0, []), (24, [])]), wire(source, [(24, [])], table_bytes=6),
                 wire(source, [(24, [])], count=2)]
        for archive in cases:
            with self.subTest(archive=archive[:12]), self.assertRaises(ValueError):
                hybrid.decode(archive, use_c=False)

    def test_truncation_corruption_trailing_concatenation_and_noncrc64_are_rejected(self):
        archive = wire(bytes(range(24)))
        corrupt = bytearray(archive)
        corrupt[len(corrupt) // 2] ^= 0x40
        invalid = [b"", archive[:-1], bytes(corrupt), archive + b"\0", archive + archive,
                   xz(lzma.decompress(archive), check=lzma.CHECK_CRC32),
                   xz(lzma.decompress(archive), check=lzma.CHECK_NONE)]
        for candidate in invalid:
            with self.subTest(length=len(candidate)), self.assertRaises(ValueError):
                hybrid.decode(candidate, use_c=False)

    def test_invalid_plans_and_table_bounds_are_rejected(self):
        source = bytes(36)
        invalid = [[], [ahps.Segment(1, 35, [])], [ahps.Segment(0, 12, []), ahps.Segment(13, 23, [])],
                   [ahps.Segment(0, 24, []), ahps.Segment(12, 12, [])], [ahps.Segment(0, 37, [])]]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                hybrid.encode_plan(source, plan, use_c=False, preset=0)
        plan = [ahps.Segment(0, 36, [])]
        for start, length in ((-1, 12), (0, 0), (0, 13), (30, 12), (36, 12)):
            with self.subTest(start=start, length=length), self.assertRaises(ValueError):
                hybrid.aligned_plan(plan, 36, start, length, word=True)

    def test_small_patched_limits_reject_oversize_without_large_allocations(self):
        source = bytes(36)
        with mock.patch.object(hybrid, "FILE_CAP", 24), self.assertRaises(ValueError):
            hybrid.encode_plan(source, [ahps.Segment(0, 36, [])], use_c=False, preset=0)
        with mock.patch.object(hybrid, "TABLE_CAP", 12), self.assertRaises(ValueError):
            hybrid.encode_plan(bytes(24), [ahps.Segment(0, 24, WORD)], use_c=False, preset=0)
        with mock.patch.object(hybrid, "SEGMENT_CAP", 2), self.assertRaises(ValueError):
            hybrid.encode_plan(source, [ahps.Segment(i, 12, []) for i in (0, 12, 24)],
                               use_c=False, preset=0)
        archive = wire(source)
        with self.assertRaises(ValueError):
            hybrid.decode(archive, use_c=False, max_output_bytes=24)
        with mock.patch.object(hybrid, "COMPRESSED_CAP", len(archive) - 1), \
                mock.patch.object(lzma, "LZMADecompressor", side_effect=AssertionError("decompressor created")), \
                self.assertRaises(ValueError):
            hybrid.decode(archive, use_c=False)

    def test_declared_bounds_are_rejected_after_only_the_header_expands(self):
        cases = [wire(bytes(12), total=25), wire(bytes(12), count=65537),
                 wire(bytes(12), table_bytes=20 * 65536 + 1)]
        real = lzma.LZMADecompressor
        for archive in cases:
            expanded = []

            class Spy:
                def __init__(self, *args, **kwargs):
                    self.decoder = real(*args, **kwargs)

                def decompress(self, data, max_length=-1):
                    value = self.decoder.decompress(data, max_length=max_length)
                    expanded.append(len(value))
                    return value

                def __getattr__(self, name):
                    return getattr(self.decoder, name)

            with self.subTest(archive=archive[:12]), mock.patch.object(lzma, "LZMADecompressor", Spy):
                with self.assertRaises(ValueError):
                    hybrid.decode(archive, use_c=False, max_output_bytes=24)
                self.assertLessEqual(sum(expanded), 25)

    def test_actual_expansion_is_bounded_even_with_a_small_forged_declaration(self):
        archive = wire(bytes(12), residual=bytes(1024**2))
        real = lzma.LZMADecompressor
        calls = []

        class Spy:
            def __init__(self, *args, **kwargs):
                self.decoder = real(*args, **kwargs)
                self.memlimit = kwargs.get("memlimit")

            def decompress(self, data, max_length=-1):
                value = self.decoder.decompress(data, max_length=max_length)
                calls.append((max_length, len(value), self.memlimit))
                return value

            def __getattr__(self, name):
                return getattr(self.decoder, name)

        with mock.patch.object(lzma, "LZMADecompressor", Spy), self.assertRaises(ValueError):
            hybrid.decode(archive, use_c=False, max_output_bytes=24)
        self.assertTrue(calls)
        self.assertTrue(all(0 < bound <= 43 for bound, _, _ in calls))
        self.assertLessEqual(sum(actual for _, actual, _ in calls), 43)
        self.assertTrue(all(limit == 128 * 1024**2 for _, _, limit in calls))


if __name__ == "__main__":
    unittest.main()
