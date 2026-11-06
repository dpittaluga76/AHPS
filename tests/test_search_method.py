#!/usr/bin/env python3
"""Synthetic contracts for chunk search; no corpus, benchmarks or fixture generation.

The independent default archive regression lives in test_plan_features.py.
Controlled plans below exercise search plumbing without assuming an optimizer
will select a particular transform for an arbitrary small input.
"""

import dataclasses
import lzma
import struct
from types import SimpleNamespace
import unittest
from unittest import mock

import ahps


def code_bytes(records=100):
    """Sixteen-byte CALL/RIP-relative records with explicit global targets."""
    data = bytearray()
    for index in range(records):
        data += b"\x55\x48\x89\xe5\xe8"
        target = 0x2000 + (index % 7) * 32
        data += struct.pack("<I", (target - (len(data) + 4)) & 0xFFFFFFFF)
        data += b"\x48\x8b\x05"
        data += struct.pack("<I", (0x4000 - (len(data) + 4)) & 0xFFFFFFFF)
    return bytes(data)


def container_segments(raw):
    """Read the documented v5 table independently of the production parser."""
    if raw[:5] != b"AHPS\x05":
        raise AssertionError("Search choices must retain the v5 format")
    count, table_len, total, _ = struct.unpack_from("<IIQI", raw, 5)
    cursor, start, segments = 25, 0, []
    for _ in range(count):
        length, steps = struct.unpack_from("<IB", raw, cursor)
        cursor += 5
        program = []
        for _ in range(steps):
            program.append((raw[cursor], raw[cursor + 1:cursor + 5]))
            cursor += 5
        segments.append((start, length, tuple(program)))
        start += length
    if cursor != 25 + table_len or start != total or cursor + total != len(raw):
        raise AssertionError("Malformed generated container")
    return total, segments


def plan_signature(plan):
    return tuple((seg.start, seg.length, tuple(seg.program)) for seg in plan)


def choose_first_with_real_encoding(data, plans, preset, use_c):
    """Control selection only; container writing, lifts and XZ remain real."""
    return plans[0], ahps.xz_compress(ahps.pack_container(data, plans[0], use_c), preset)


class SearchConfigurationTests(unittest.TestCase):
    def test_default_is_exhaustive_and_both_fields_are_frozen(self):
        search = ahps.SearchMethod()
        self.assertEqual(search, ahps.EXHAUSTIVE)
        self.assertEqual(dataclasses.asdict(search), {
            "chunk_trial_preset": None, "exe32_chunk_rival": True,
        })
        for field, value in (("chunk_trial_preset", 0), ("exe32_chunk_rival", False)):
            with self.subTest(field=field):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(search, field, value)

    def test_all_valid_caps_and_rival_values_are_retained(self):
        for cap in (None, *range(10)):
            for rival in (False, True):
                with self.subTest(cap=cap, rival=rival):
                    search = ahps.SearchMethod(cap, rival)
                    self.assertEqual(search.chunk_trial_preset, cap)
                    self.assertIs(search.exe32_chunk_rival, rival)

    def test_trial_cap_rejects_nonintegers_booleans_subclasses_and_flags(self):
        class IntegerSubclass(int):
            pass

        for invalid in (False, True, 0.0, 1.5, "1", [], {}, IntegerSubclass(1)):
            with self.subTest(cap=repr(invalid)):
                with self.assertRaises(TypeError):
                    ahps.SearchMethod(chunk_trial_preset=invalid)
        for invalid in (-1, 10, 9 | lzma.PRESET_EXTREME):
            with self.subTest(cap=invalid):
                with self.assertRaises(ValueError):
                    ahps.SearchMethod(chunk_trial_preset=invalid)

    def test_rival_setting_requires_a_genuine_boolean(self):
        for invalid in (None, 0, 1, -1, 0.0, "false", [], {}):
            with self.subTest(rival=repr(invalid)):
                with self.assertRaises(TypeError):
                    ahps.SearchMethod(exe32_chunk_rival=invalid)

    def test_public_api_rejects_wrong_search_objects_before_planning(self):
        invalid_values = (None, False, 0, {}, "exhaustive", ahps.PlanFeatures(),
                          SimpleNamespace(chunk_trial_preset=None, exe32_chunk_rival=True))
        with mock.patch.object(ahps, "plan_segments") as planner, \
                mock.patch.object(ahps, "chunked_plan") as chunk_planner, \
                mock.patch.object(ahps, "xz_compress") as compressor:
            for invalid in invalid_values:
                with self.subTest(search=repr(invalid)):
                    with self.assertRaises(TypeError):
                        ahps.ahps_compress_bytes(b"synthetic", search=invalid)
        planner.assert_not_called()
        chunk_planner.assert_not_called()
        compressor.assert_not_called()

    def test_caps_compare_levels_and_preserve_flags_when_not_lowering(self):
        extreme = lzma.PRESET_EXTREME
        cases = (
            (9 | extreme, None, 9 | extreme), (9 | extreme, 9, 9 | extreme),
            (9 | extreme, 8, 8), (3 | extreme, 3, 3 | extreme),
            (3 | extreme, 9, 3 | extreme), (3 | extreme, 2, 2),
            (extreme, 0, extreme), (extreme, 9, extreme),
            (3, None, 3), (3, 3, 3), (3, 9, 3), (3, 0, 0),
            (0, 0, 0), (9, 9, 9),
        )
        for final, cap, expected in cases:
            with self.subTest(final=final, cap=cap):
                self.assertEqual(ahps.chunk_trial_preset(final, ahps.SearchMethod(cap)), expected)


class SearchPipelineTests(unittest.TestCase):
    def test_single_raw_plan_skips_chunk_trials_but_encodes_final_archive(self):
        data = b"synthetic raw chunks"

        def raw_plan(chunk, block_size, use_c, features):
            return [ahps.Segment(0, len(chunk), [])]

        expected = ahps.xz_compress(
            ahps.pack_container(data, [ahps.Segment(0, len(data), [])], False), 0
        )
        with mock.patch.multiple(ahps, EXTREME_LIMIT=4, PLAN_CHUNK_LEN=6), \
                mock.patch.object(ahps, "plan_segments", side_effect=raw_plan), \
                mock.patch.object(ahps, "rival_plans", return_value=[]), \
                mock.patch.object(ahps, "xz_compress", wraps=ahps.xz_compress) as compress:
            encoded, report = ahps.ahps_compress_bytes(data, force_python=True, lzma_preset=0)
        compress.assert_called_once()
        self.assertEqual(encoded, expected)
        self.assertEqual(report.programs, {"RAW": (1, len(data))})
        self.assertEqual(ahps.ahps_decompress_bytes(encoded), data)

    def test_single_transformed_plan_still_rebases_each_chunk(self):
        data = bytes(14)
        base = 0xFFFFFFFC

        def executable_plan(chunk, block_size, use_c, features):
            return [ahps.Segment(0, len(chunk), [(ahps.OP_EXE32, base.to_bytes(4, "big"))])]

        with mock.patch.object(ahps, "PLAN_CHUNK_LEN", 6), \
                mock.patch.object(ahps, "plan_segments", side_effect=executable_plan), \
                mock.patch.object(ahps, "rival_plans", return_value=[]), \
                mock.patch.object(ahps, "best_plan") as choose:
            plan = ahps.chunked_plan(data, 4, 0, False)
        choose.assert_not_called()
        self.assertEqual([segment.start for segment in plan], [0, 6, 12])
        self.assertEqual([segment.length for segment in plan], [6, 6, 2])
        self.assertEqual([int.from_bytes(segment.program[0][1], "big") for segment in plan],
                         [0xFFFFFFFC, 2, 8])

    def test_only_chunk_trials_use_cap_and_whole_file_raw_is_still_tried(self):
        data = bytes(range(256)) * 6 + bytes(range(37))
        features = ahps.PlanFeatures(exe32=False, exe_split=False)
        recorded = []
        original_xz = ahps.xz_compress

        def delta_plan(chunk, block_size, use_c, features):
            return [ahps.Segment(0, len(chunk), [(ahps.OP_DELTA1_LANE, b"\x01\0\0\0")])]

        def record_trial(raw, preset):
            total, segments = container_segments(raw)
            recorded.append((total, preset, any(program for _, _, program in segments)))
            return original_xz(raw, preset)

        cases = ((ahps.EXHAUSTIVE, 1), (ahps.SearchMethod(0, False), 0),
                 (ahps.SearchMethod(9, False), 1))
        for search, expected_chunk_preset in cases:
            with self.subTest(search=search):
                recorded.clear()
                with mock.patch.multiple(ahps, EXTREME_LIMIT=256, PLAN_CHUNK_LEN=512), \
                        mock.patch.object(ahps, "plan_segments", side_effect=delta_plan), \
                        mock.patch.object(ahps, "xz_compress", side_effect=record_trial):
                    archive, report = ahps.ahps_compress_bytes(
                        data, 128, True, 1, features=features, search=search
                    )
                chunks = [row for row in recorded if row[0] < len(data)]
                final = [row for row in recorded if row[0] == len(data)]
                self.assertTrue(chunks)
                self.assertEqual({row[1] for row in chunks}, {expected_chunk_preset})
                self.assertEqual({row[1] for row in final}, {1})
                self.assertEqual({row[2] for row in final}, {False, True},
                                 "Both transformed and complete-file RAW plans must reach final trials")
                self.assertEqual(report.search, search)
                self.assertEqual(report.features, features)
                self.assertEqual(ahps.ahps_decompress_bytes(archive, True), data)

    def test_small_files_and_disabled_chunking_ignore_search_but_echo_it(self):
        methods = (ahps.EXHAUSTIVE, ahps.SearchMethod(0), ahps.SearchMethod(None, False),
                   ahps.SearchMethod(0, False), ahps.SearchMethod(9, False))
        cases = ((b"", ahps.PlanFeatures()), (code_bytes(8), ahps.PlanFeatures()),
                 (code_bytes(), ahps.PlanFeatures(per_chunk=False)))
        with mock.patch.object(ahps, "EXTREME_LIMIT", 256), \
                mock.patch.object(ahps, "chunked_plan", side_effect=AssertionError("chunk path ran")):
            for data, features in cases:
                reference, default_report = ahps.ahps_compress_bytes(data, 128, True, 0, features)
                self.assertEqual(default_report.search, ahps.EXHAUSTIVE)
                for search in methods:
                    with self.subTest(length=len(data), per_chunk=features.per_chunk, search=search):
                        archive, report = ahps.ahps_compress_bytes(data, 128, True, 0, features, search)
                        self.assertEqual(archive, reference)
                        self.assertEqual(report.search, search)
                        self.assertEqual(report.features, features)
                        self.assertEqual(ahps.ahps_decompress_bytes(archive, True), data)

    def test_rival_false_removes_only_extra_exe32_even_without_exe_split(self):
        data = code_bytes(64)
        self.assertTrue(ahps.looks_like_x86(data))

        def raw_plan(chunk, block_size, use_c, features):
            return [ahps.Segment(0, len(chunk), [])]

        for split_enabled in (False, True):
            features = ahps.PlanFeatures(exe_split=split_enabled)
            snapshots = {}
            for rival in (True, False):
                captured = []
                original_rivals = ahps.rival_plans

                def capture_rivals(chunk, segments, features, exe32_rival):
                    rivals = original_rivals(chunk, segments, features, exe32_rival)
                    captured.append([plan_signature(plan) for plan in [segments] + rivals])
                    return rivals

                def choose_first(chunk, plans, preset, use_c):
                    return plans[0], b""

                with mock.patch.object(ahps, "PLAN_CHUNK_LEN", 512), \
                        mock.patch.object(ahps, "plan_segments", side_effect=raw_plan), \
                        mock.patch.object(ahps, "rival_plans", side_effect=capture_rivals), \
                        mock.patch.object(ahps, "best_plan", side_effect=choose_first):
                    ahps.chunked_plan(data, 128, 0, False, features, ahps.SearchMethod(0, rival))
                snapshots[rival] = captured
            self.assertTrue(snapshots[True])
            self.assertEqual(len(snapshots[True]), len(snapshots[False]))
            for with_rival, without_rival in zip(snapshots[True], snapshots[False]):
                with self.subTest(exe_split=split_enabled):
                    exe32_plan = ((0, 512, ((ahps.OP_EXE32, bytes(4)),)),)
                    self.assertIn(exe32_plan, with_rival)
                    self.assertEqual(without_rival, [plan for plan in with_rival if plan != exe32_plan])
                    remaining_ops = {op for plan in without_rival for _, _, program in plan for op, _ in program}
                    self.assertEqual(remaining_ops, {ahps.OP_EXE_SPLIT} if split_enabled else set())

    def test_planner_and_real_refinement_can_keep_exe32_without_extra_rival(self):
        data = code_bytes(64)
        features = ahps.PlanFeatures(exe_split=False)
        captured = []

        def capture_plans(chunk, plans, preset, use_c):
            captured.append([plan_signature(plan) for plan in plans])
            return plans[0], b""

        with mock.patch.multiple(ahps, PLAN_CHUNK_LEN=512, CHAIN_MIN_LEN=128), \
                mock.patch.object(ahps, "viterbi_path", side_effect=lambda costs, count, hits: [ahps.SLOT_EXE] * count), \
                mock.patch.object(ahps, "apply_program", wraps=ahps.apply_program) as applied, \
                mock.patch.object(ahps, "refine_chain", wraps=ahps.refine_chain) as refined, \
                mock.patch.object(ahps, "best_plan", side_effect=capture_plans):
            plan = ahps.chunked_plan(data, 128, 0, False, features, ahps.SearchMethod(0, False))
        self.assertTrue(refined.called)
        self.assertTrue(any(call.args[0][0][0] == ahps.OP_EXE32 for call in applied.call_args_list),
                        "The real x86 refinement must still trial EXE32")
        self.assertTrue(plan)
        self.assertTrue(all(seg.program[0][0] == ahps.OP_EXE32 for seg in plan))
        for plans in captured:
            exe32_plans = [candidate for candidate in plans
                           if any(op == ahps.OP_EXE32 for _, _, program in candidate for op, _ in program)]
            self.assertEqual(len(exe32_plans), 1, "Keep the planner's EXE32 but omit its extra rival")


class SearchRoundTripTests(unittest.TestCase):
    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires native/Python interoperability")
    def test_small_chunk_search_methods_interoperate_with_native_and_python(self):
        data = code_bytes() + bytes(range(37))
        methods = (ahps.EXHAUSTIVE, ahps.SearchMethod(0), ahps.SearchMethod(None, False),
                   ahps.SearchMethod(0, False))
        engines = (True, False)
        with mock.patch.multiple(ahps, EXTREME_LIMIT=256, PLAN_CHUNK_LEN=512):
            for search in methods:
                for force_python in engines:
                    with self.subTest(search=search, encode_python=force_python):
                        archive, report = ahps.ahps_compress_bytes(data, 128, force_python, 1, search=search)
                        self.assertEqual(report.search, search)
                        self.assertEqual(report.backend_used == "Pure Python Fallback", force_python)
                        container_segments(lzma.decompress(archive))
                        for decode_python in engines:
                            self.assertEqual(ahps.ahps_decompress_bytes(archive, decode_python), data)

    def test_exe_chunk_bases_rebase_with_wraparound(self):
        data = code_bytes(96)
        local_base = 0xFFFFFF00
        engines = (True, False) if ahps.SIMD_BRIDGE.lib is not None else (True,)
        for opcode in (ahps.OP_EXE32, ahps.OP_EXE_SPLIT):
            def executable_plan(chunk, block_size, use_c, features):
                return [ahps.Segment(0, len(chunk), [(opcode, local_base.to_bytes(4, "big"))])]

            for force_python in engines:
                with self.subTest(opcode=opcode, encode_python=force_python):
                    with mock.patch.multiple(ahps, EXTREME_LIMIT=256, PLAN_CHUNK_LEN=512), \
                            mock.patch.object(ahps, "plan_segments", side_effect=executable_plan), \
                            mock.patch.object(ahps, "best_plan", side_effect=choose_first_with_real_encoding):
                        archive, report = ahps.ahps_compress_bytes(
                            data, 128, force_python, 1, search=ahps.SearchMethod(0, False)
                        )
                    total, segments = container_segments(lzma.decompress(archive))
                    self.assertEqual(total, len(data))
                    self.assertEqual([start for start, _, _ in segments], [0, 512, 1024])
                    self.assertEqual([program for _, _, program in segments], [
                        ((opcode, bytes.fromhex("ffffff00")),),
                        ((opcode, bytes.fromhex("00000100")),),
                        ((opcode, bytes.fromhex("00000300")),),
                    ])
                    self.assertEqual(report.search, ahps.SearchMethod(0, False))
                    for decode_python in engines:
                        self.assertEqual(ahps.ahps_decompress_bytes(archive, decode_python), data)


if __name__ == "__main__":
    unittest.main()
