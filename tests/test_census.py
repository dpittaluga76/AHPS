#!/usr/bin/env python3
"""Regression tests for census.py. Run with: python -m unittest tests.test_census"""

import random
import struct
import unittest

import ahps
import census


class DetectorTests(unittest.TestCase):
    def test_linear_complexity_of_known_sequences(self) -> None:
        self.assertEqual(census.linear_complexity([0] * 64), 0)
        self.assertEqual(census.linear_complexity([1] * 64), 1)
        self.assertEqual(census.linear_complexity([1, 0] * 32), 2)
        rng = random.Random(1)
        self.assertAlmostEqual(census.linear_complexity([rng.getrandbits(1) for _ in range(4096)]), 2048, delta=8)

    def test_word_lcg_rejects_repeating_patterns(self) -> None:
        pattern = random.Random(2).randbytes(16) * 256
        self.assertIsNone(census.word_lcg_hit(pattern))

    def test_word_lcg_reports_only_the_verified_bytes(self) -> None:
        stream = census.plant_words(lambda x: (x * 214013 + 2531011) & 0xFFFFFFFF, 12345, 2048, 4)
        block = stream + random.Random(3).randbytes(2048)
        self.assertEqual(census.word_lcg_hit(block), ("word-lcg32", 0, 2048))

    def test_one_structured_lane_is_not_a_generator(self) -> None:
        data = bytearray(random.Random(4).randbytes(64 * 1024))
        data[::4] = bytes(b & 0xFE for b in data[::4])
        self.assertIsNone(census.linear_window_hit(bytes(data), 0))

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "census residual checks require the optional native kernel")
    def test_poly2_saving_uses_the_real_residual(self) -> None:
        piece = bytes(1024)
        params = bytes([3, 7, 42, 0])
        residual = census.hit_residual("ahps-poly2", params.hex(), piece)
        self.assertEqual(residual, ahps.SIMD_BRIDGE.lift(ahps.OP_POLY2, params, piece, False))
        self.assertEqual(census.hit_residual("crc32-slice0-le", "", piece), bytes(1024))


class TableTests(unittest.TestCase):
    def test_tables_match_published_values(self) -> None:
        tables = census.known_tables()
        self.assertEqual(struct.unpack_from("<I", tables["crc32-slice0-le"], 4)[0], 0x77073096)
        self.assertEqual(tables["aes-sbox"][:4], bytes([0x63, 0x7C, 0x77, 0x7B]))
        self.assertEqual(struct.unpack_from(">I", tables["sha256-k-be"], 0)[0], 0x428A2F98)
        self.assertEqual(struct.unpack_from("<I", tables["md5-k-le"], 0)[0], 0xD76AA478)
        self.assertEqual(struct.unpack_from(">I", tables["blowfish-pi-be"], 0)[0], 0x243F6A88)


class SelfCheckTests(unittest.TestCase):
    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "census scoring controls require the optional native kernel")
    def test_controls_pass(self) -> None:
        self.assertTrue(census.selfcheck())


if __name__ == "__main__":
    unittest.main()
