#!/usr/bin/env python3
"""Decode preserved v3 archives: python -m unittest tests.test_golden_archives."""

import hashlib
import lzma
from pathlib import Path
import unittest

import ahps


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v3"
CASES = ("empty", "lcg", "struct", "tiny")
OPTIONAL_DLL_FILES = ("dll_c.ahps", "dll_py.ahps", "dll.sha256")


class GoldenArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.archives = {}
        cls.expected_hashes = {}
        optional_present = [(FIXTURES / name).is_file() for name in OPTIONAL_DLL_FILES]
        if any(optional_present) and not all(optional_present):
            raise AssertionError("The optional historical DLL fixture requires both archives and its SHA-256 sidecar")
        cls.cases = CASES + (("dll",) if all(optional_present) else ())
        cls.archive_names = tuple(f"{case}_{engine}.ahps" for case in cls.cases for engine in ("c", "py"))
        for case in cls.cases:
            digest = (FIXTURES / f"{case}.sha256").read_text(encoding="ascii").strip()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise AssertionError(f"Invalid restored-data SHA-256 for {case}")
            cls.expected_hashes[case] = digest
        for name in cls.archive_names:
            path = FIXTURES / name
            # These handed-over archives are 76--13,616 bytes. Check their size
            # before reading, then bound XZ expansion before the public decoder.
            if path.stat().st_size > 16 * 1024:
                raise AssertionError(f"Unexpectedly large fixture: {name}")
            archive = path.read_bytes()
            decoder = lzma.LZMADecompressor(
                format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024
            )
            raw = decoder.decompress(archive, max_length=64 * 1024 + 1)
            if len(raw) > 64 * 1024 or not decoder.eof or decoder.unused_data:
                raise AssertionError(f"Fixture exceeds its bound or has trailing data: {name}")
            if raw[:5] != b"AHPS\x03":
                raise AssertionError(f"Fixture is not a historical v3 archive: {name}")
            cls.archives[name] = archive

    def test_fixture_inventory(self) -> None:
        self.assertEqual({p.name for p in FIXTURES.glob("*.ahps")}, set(self.archive_names))
        self.assertEqual(
            {p.name for p in FIXTURES.glob("*.sha256")},
            {f"{case}.sha256" for case in self.cases},
        )

    def check_decoding(self, force_python: bool, cases=CASES) -> None:
        for name, archive in self.archives.items():
            case = name.rsplit("_", 1)[0]
            if case not in cases:
                continue
            with self.subTest(archive=name):
                restored = ahps.ahps_decompress_bytes(archive, force_python=force_python)
                self.assertEqual(hashlib.sha256(restored).hexdigest(), self.expected_hashes[case])

    def test_python_decoder(self) -> None:
        self.check_decoding(force_python=True)

    @unittest.skipIf(ahps.SIMD_BRIDGE.lib is None, "native C kernel is unavailable")
    def test_native_decoder(self) -> None:
        self.check_decoding(force_python=False)

    def test_optional_historical_dll_fixture(self) -> None:
        if "dll" not in self.cases:
            self.skipTest("Historical DLL fixture is optional and excluded from the public source export")
        self.check_decoding(force_python=True, cases=("dll",))
        if ahps.SIMD_BRIDGE.lib is not None:
            self.check_decoding(force_python=False, cases=("dll",))


if __name__ == "__main__":
    unittest.main()
