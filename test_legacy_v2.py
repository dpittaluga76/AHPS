#!/usr/bin/env python3
"""Historical raw-LZMA2 archive regressions: python -m unittest test_legacy_v2."""

import importlib.util
import lzma
from pathlib import Path
import random
import struct
import sys
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "ahps_legacy_v2", Path(__file__).with_name("ahps-binv2.py")
)
legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = legacy
_SPEC.loader.exec_module(legacy)


class LegacyV2Tests(unittest.TestCase):
    def test_decodes_matches_more_than_eight_mib_back(self) -> None:
        # Only the final copy can match this prefix. The intervening zeros put
        # that match outside the old decoder's implicit 8 MiB dictionary.
        prefix = random.Random(917).randbytes(64 * 1024)
        data = prefix + bytes(9 * 1024 * 1024) + prefix
        block_size = 65535  # The historical tape stores uint16 block lengths.
        num_blocks = (len(data) + block_size - 1) // block_size
        tape = b"".join(
            struct.pack("<BH3s", legacy.OP_RAW, min(block_size, len(data) - off), bytes(3))
            for off in range(0, len(data), block_size)
        )
        container = legacy.MAGIC_HEADER + struct.pack("<I", num_blocks) + tape + data
        # Construct RAW blocks directly to avoid running legacy synthesis over
        # 9 MiB, and use the original encoder's exact raw-LZMA2 settings.
        archive = lzma.compress(
            container,
            format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}],
        )

        # Prove the fixture contains a long-distance match; otherwise even the
        # broken decoder could pass and this would not exercise the regression.
        with self.assertRaises(lzma.LZMAError):
            lzma.decompress(
                archive,
                format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 8 * 1024 * 1024}],
            )
        self.assertEqual(legacy.decompress_binary(archive), data)

    def test_small_archive_remains_compatible(self) -> None:
        data = bytes((3 * i * i + 7 * i + 42) & 0xFF for i in range(256))
        # Captured from the unmodified v2 encoder: one OP_POLY2 block.
        archive = bytes.fromhex(
            "e0010e00155d0020920646d28f5d688c230beaae94e9ac53bc2b200000"
        )
        self.assertEqual(legacy.decompress_binary(archive), data)
        newly_encoded, _ = legacy.compress_binary(data)
        self.assertEqual(legacy.decompress_binary(newly_encoded), data)


if __name__ == "__main__":
    unittest.main()
