#!/usr/bin/env python3
"""POLY2 canonical planning preserves residuals and existing archive parameters."""

import lzma
import struct
import unittest
import zlib

import ahps


# These canonical choices and local costs were checked against the untouched
# two-trial C/Python scorers saved in experiments/poly2-dedup-20261103/reference.
# Tests use the frozen expectations below, not that development snapshot.
COEFFICIENT_PAIRS = (
    ("linear", bytes.fromhex("00072a00"), bytes.fromhex("80872a00")),
    ("quadratic", bytes.fromhex("03072a00"), bytes.fromhex("83872a00")),
    ("coefficient wrap", bytes.fromhex("7ffffa00"), bytes.fromhex("ff7ffa00")),
    ("linear coefficient wraps to zero", bytes.fromhex("0180ff00"), bytes.fromhex("8100ff00")),
)
LOCAL_COSTS = ((4, -3.4499999999999997), (257, -294.4), (513, -588.8))


def polynomial_bytes(params, length):
    """Construct samples by finite differences, independently of the lift formula."""
    a, b, value, _ = params
    delta = a + b
    result = bytearray()
    for _ in range(length):
        result.append(value & 255)
        value += delta
        delta += 2 * a
    return bytes(result)


class Poly2CanonicalTests(unittest.TestCase):
    def assert_expected_residuals(self, use_c):
        for name, canonical, equivalent in COEFFICIENT_PAIRS:
            for length in (255, 256, 257, 513):
                prediction = polynomial_bytes(canonical, length)
                expected = bytes((29 * i + i // 13) & 255 for i in range(length))
                original = bytes((p + r) & 255 for p, r in zip(prediction, expected))
                for params in (canonical, equivalent):
                    with self.subTest(case=name, length=length, params=params.hex()):
                        self.assertEqual(
                            ahps.lift_step(ahps.OP_POLY2, params, original, use_c), expected
                        )
                        self.assertEqual(
                            ahps.lift_step(ahps.OP_POLY2, params, expected, use_c, inverse=True),
                            original,
                        )

    def test_python_equivalent_coefficients_have_known_residuals(self):
        self.assert_expected_residuals(False)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires the native kernel")
    def test_native_equivalent_coefficients_have_known_residuals(self):
        self.assert_expected_residuals(True)

    def test_python_scorer_retains_frozen_canonical_choices(self):
        for name, canonical, _ in COEFFICIENT_PAIRS:
            for length, expected_cost in LOCAL_COSTS:
                with self.subTest(case=name, length=length):
                    costs, opcode, params = ahps.py_score_block(
                        polynomial_bytes(canonical, length), 0
                    )
                    self.assertEqual((opcode, params), (ahps.OP_POLY2, canonical))
                    self.assertAlmostEqual(costs[ahps.SLOT_LOCAL], expected_cost, places=10)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires the native kernel")
    def test_native_scorer_retains_frozen_canonical_choices(self):
        for name, canonical, _ in COEFFICIENT_PAIRS:
            for length, expected_cost in LOCAL_COSTS:
                with self.subTest(case=name, length=length):
                    costs, local = ahps.SIMD_BRIDGE.score_all(
                        polynomial_bytes(canonical, length), length
                    )
                    self.assertEqual(local, b"\x01" + canonical)
                    self.assertAlmostEqual(costs[ahps.SLOT_LOCAL], expected_cost, places=10)

    def test_v4_and_v5_archives_still_decode_noncanonical_coefficients(self):
        engines = (True,) if ahps.SIMD_BRIDGE.lib is None else (True, False)
        for name, canonical, equivalent in COEFFICIENT_PAIRS:
            original = polynomial_bytes(canonical, 257)
            # Build the wire format directly: decoder compatibility must not
            # depend on the current planner emitting the noncanonical choice.
            table = struct.pack("<IB", len(original), 1) + b"\x01" + equivalent
            for magic in (b"AHPS\x04", b"AHPS\x05"):
                header = magic + struct.pack(
                    "<IIQI", 1, len(table), len(original), zlib.crc32(original)
                )
                archive = lzma.compress(header + table + bytes(len(original)), preset=0)
                for force_python in engines:
                    with self.subTest(case=name, magic=magic, force_python=force_python):
                        self.assertEqual(
                            ahps.ahps_decompress_bytes(archive, force_python=force_python),
                            original,
                        )


if __name__ == "__main__":
    unittest.main()
