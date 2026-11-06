#!/usr/bin/env python3
"""Automatic search selection contracts using synthetic inputs only."""

import lzma
import unittest
from unittest import mock

import ahps


class AutomaticSearchTests(unittest.TestCase):
    def run_pipeline(self, data, preset, *, features=ahps.DEFAULT_FEATURES, **kwargs):
        """Observe API routing without spending time compressing boundary inputs."""
        plan = [ahps.Segment(0, len(data), [])]
        with mock.patch.object(ahps, "plan_segments", return_value=plan) as whole, \
                mock.patch.object(ahps, "chunked_plan", return_value=plan) as chunked, \
                mock.patch.object(ahps, "rival_plans", return_value=[]), \
                mock.patch.object(ahps, "best_plan", return_value=(plan, b"archive")) as final:
            _, report = ahps.ahps_compress_bytes(
                data, force_python=True, lzma_preset=preset, features=features, **kwargs
            )
        return report, whole, chunked, final

    def test_automatic_search_requires_exactly_level_nine_extreme(self):
        for level in range(10):
            for flag in (0, lzma.PRESET_EXTREME):
                with self.subTest(level=level, extreme=bool(flag)):
                    expected = ahps.FAST_CHUNKS if (level, flag) == (9, lzma.PRESET_EXTREME) else ahps.EXHAUSTIVE
                    self.assertIs(ahps.default_search(level | flag), expected)
        self.assertEqual(ahps.FAST_CHUNKS, ahps.SearchMethod(1, False))
        self.assertEqual(ahps.SearchMethod(), ahps.EXHAUSTIVE)

    def test_explicit_level_nine_resolves_before_chunk_routing(self):
        with mock.patch.object(ahps, "EXTREME_LIMIT", 64):
            report, whole, chunked, final = self.run_pipeline(bytes(65), 9)
        whole.assert_not_called()
        self.assertIs(chunked.call_args.args[5], ahps.FAST_CHUNKS)
        self.assertEqual(chunked.call_args.args[2], 9 | lzma.PRESET_EXTREME)
        self.assertEqual(final.call_args.args[2], 9 | lzma.PRESET_EXTREME,
                         "Cheap chunk trials must not lower the final encoding preset")
        self.assertIs(report.search, ahps.FAST_CHUNKS)

    def test_implicit_preset_preserves_real_sixteen_mib_boundary(self):
        boundary = 16 * 1024 * 1024
        self.assertEqual(ahps.EXTREME_LIMIT, boundary)
        for length in (boundary - 1, boundary, boundary + 1):
            with self.subTest(length=length):
                report, whole, chunked, final = self.run_pipeline(bytes(length), None)
                if length <= boundary:
                    chunked.assert_not_called()
                    whole.assert_called_once()
                    self.assertIs(report.search, ahps.FAST_CHUNKS)
                    self.assertEqual(final.call_args.args[2], 9 | lzma.PRESET_EXTREME)
                else:
                    whole.assert_not_called()
                    self.assertIs(chunked.call_args.args[5], ahps.EXHAUSTIVE)
                    self.assertEqual(chunked.call_args.args[2], 3)
                    self.assertEqual(final.call_args.args[2], 3)
                    self.assertIs(report.search, ahps.EXHAUSTIVE)

    def test_explicit_search_overrides_automatic_choice_in_both_directions(self):
        custom = ahps.SearchMethod(2, True)
        with mock.patch.object(ahps, "EXTREME_LIMIT", 64):
            for preset in (3, 9):
                for search in (ahps.EXHAUSTIVE, ahps.FAST_CHUNKS, custom):
                    with self.subTest(preset=preset, search=search):
                        report, whole, chunked, final = self.run_pipeline(bytes(65), preset, search=search)
                        whole.assert_not_called()
                        self.assertIs(chunked.call_args.args[5], search)
                        self.assertIs(report.search, search)
                        self.assertEqual(final.call_args.args[2],
                                         9 | lzma.PRESET_EXTREME if preset == 9 else 3)

    def test_inactive_chunk_search_echoes_resolved_method_without_running_it(self):
        with mock.patch.object(ahps, "EXTREME_LIMIT", 64):
            cases = ((bytes(32), ahps.DEFAULT_FEATURES),
                     (bytes(65), ahps.PlanFeatures(per_chunk=False)))
            for data, features in cases:
                with self.subTest(length=len(data), per_chunk=features.per_chunk):
                    report, whole, chunked, final = self.run_pipeline(data, 9, features=features)
                    chunked.assert_not_called()
                    whole.assert_called_once()
                    self.assertIs(report.search, ahps.FAST_CHUNKS)
                    self.assertEqual(final.call_args.args[2], 9 | lzma.PRESET_EXTREME)

    def test_direct_chunked_plan_keeps_exhaustive_default(self):
        data = bytes(64)
        plan = [ahps.Segment(0, len(data), [(ahps.OP_DELTA1_LANE, b"\x01\0\0\0")])]
        raw_plan = [ahps.Segment(0, len(data), [])]
        preset = 9 | lzma.PRESET_EXTREME
        with mock.patch.object(ahps, "plan_segments", return_value=plan), \
                mock.patch.object(ahps, "rival_plans", return_value=[raw_plan]) as rivals, \
                mock.patch.object(ahps, "best_plan", return_value=(plan, b"archive")) as trial:
            ahps.chunked_plan(data, 128, preset, False)
        self.assertIs(rivals.call_args.args[3], True)
        self.assertEqual(trial.call_args.args[2], preset,
                         "Direct chunked_plan callers retain full-strength trials")

    def test_automatic_chunked_archive_matches_explicit_candidate_and_round_trips(self):
        data = bytes(range(64)) * 8 + b"synthetic tail"
        with mock.patch.multiple(ahps, EXTREME_LIMIT=64, PLAN_CHUNK_LEN=128):
            automatic, report = ahps.ahps_compress_bytes(data, 128, True, 9)
            explicit, explicit_report = ahps.ahps_compress_bytes(
                data, 128, True, 9, search=ahps.FAST_CHUNKS
            )
        self.assertEqual(automatic, explicit)
        self.assertIs(report.search, ahps.FAST_CHUNKS)
        self.assertIs(explicit_report.search, ahps.FAST_CHUNKS)
        self.assertEqual(ahps.ahps_decompress_bytes(automatic, True), data)
        if ahps.SIMD_BRIDGE.lib is not None:
            self.assertEqual(ahps.ahps_decompress_bytes(automatic, False), data)


if __name__ == "__main__":
    unittest.main()
