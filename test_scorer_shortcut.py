#!/usr/bin/env python3
"""The native scorer skips x86 lifts on blocks with no opcode; its costs must still match Python."""

import unittest

import ahps


FILLER = 0x41


def block(size, *placed):
    data = bytearray([FILLER]) * size
    for offset, value in placed:
        data[offset:offset + len(value)] = value
    return bytes(data)


CASES = {
    "no opcode": block(64),
    "zero block": bytes(64),
    "E8 at the last liftable offset": block(64, (59, b"\xe8")),
    "E8 one byte too late": block(64, (60, b"\xe8")),
    "E9 in the final byte": block(64, (63, b"\xe9")),
    "E8 after a ModRM opcode": block(64, (10, b"\x89\xe8")),
    "conditional jump at the last liftable offset": block(64, (58, b"\x0f\x80")),
    "conditional jump one byte too late": block(64, (59, b"\x0f\x80")),
    "FF 15 at the last liftable offset": block(64, (58, b"\xff\x15")),
    "FF 25 one byte too late": block(64, (59, b"\xff\x25")),
    "RIP-relative MOV": block(64, (20, b"\x8b\x05")),
    "RIP-relative LEA one byte too late": block(64, (59, b"\x8d\x05")),
    "opcode-like bytes that form no 6-byte instruction": block(64, (20, b"\x0f\x7f\xff\x14\x8b\x04")),
    "shorter than a call": block(4, (0, b"\xe8")),
    "exactly one call": block(5, (0, b"\xe8")),
}


@unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "requires the native kernel")
class ScorerShortcutTests(unittest.TestCase):
    def test_native_x86_cost_matches_python_at_every_boundary(self):
        for name, data in CASES.items():
            for flags in (1, 2, 3):
                features = ahps.PlanFeatures(exe32=bool(flags & 1), exe_split=bool(flags & 2))
                with self.subTest(case=name, flags=flags):
                    native, _ = ahps.SIMD_BRIDGE.score_all(data, len(data), flags)
                    python, _, _ = ahps.py_score_block(data, 0, features)
                    self.assertAlmostEqual(native[ahps.SLOT_EXE], python[ahps.SLOT_EXE], places=6)

    def test_a_block_with_no_opcode_really_is_an_identity_for_both_lifts(self):
        for name in ("no opcode", "zero block", "E8 one byte too late", "E9 in the final byte",
                     "conditional jump one byte too late", "FF 25 one byte too late",
                     "RIP-relative LEA one byte too late", "shorter than a call"):
            data = CASES[name]
            for opcode in (ahps.OP_EXE32, ahps.OP_EXE_SPLIT):
                with self.subTest(case=name, opcode=opcode):
                    self.assertEqual(ahps.lift_step(opcode, bytes(4), data, True), data)

    def test_disabled_lifts_still_cost_nothing_usable(self):
        native, _ = ahps.SIMD_BRIDGE.score_all(CASES["no opcode"], 64, 0)
        self.assertEqual(native[ahps.SLOT_EXE], ahps.COST_NONE)


if __name__ == "__main__":
    unittest.main()
