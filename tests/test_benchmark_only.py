"""AHPS-only benchmarks skip external baselines and retain verification."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import ahps


class AhpsOnlyBenchmarkTests(unittest.TestCase):
    def test_baselines_run_only_in_comparison_mode(self):
        data = b"benchmark fixture\n" * 16
        archive, report = ahps.ahps_compress_bytes(data, lzma_preset=0)
        for ahps_only in (False, True):
            with self.subTest(ahps_only=ahps_only), \
                    mock.patch.object(ahps, "ahps_compress_bytes", return_value=(archive, report)), \
                    mock.patch.object(ahps.zlib, "compress", wraps=ahps.zlib.compress) as gzip, \
                    mock.patch.object(ahps.lzma, "compress", wraps=ahps.lzma.compress) as xz, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                ahps.run_benchmark_on_buffer(data, "fixture", lzma_preset=0,
                                             show_progress=False, ahps_only=ahps_only)
            self.assertEqual(gzip.call_count, 0 if ahps_only else 1)
            self.assertEqual(xz.call_count, 0 if ahps_only else 1)
            self.assertIn("SHA-256 Verified: True", output.getvalue())
            self.assertEqual("gzip -" in output.getvalue(), not ahps_only)
            self.assertEqual("xz (LZMA2)" in output.getvalue(), not ahps_only)
            self.assertEqual("vs xz)" in output.getvalue(), not ahps_only)

    def test_cli_ahps_only_with_csv_and_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            source.write_bytes(b"name,value\na,1\nb,2\n" * 8)
            with mock.patch.object(sys, "argv", ["ahps.py", "bench", str(source), "--csv",
                                                 "--ahps-only", "--progress", "-p", "0"]), \
                    mock.patch.object(ahps.zlib, "compress", side_effect=AssertionError("gzip ran")), \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()) as progress:
                ahps.main()
            self.assertIn("AHPS-Bin v2.6", output.getvalue())
            self.assertIn("SHA-256 Verified: True", output.getvalue())
            self.assertNotIn("gzip -", output.getvalue())
            self.assertNotIn("xz (LZMA2)", output.getvalue())
            self.assertIn("100%", progress.getvalue())

    def test_verification_failure_does_not_report_success(self):
        with mock.patch.object(ahps, "ahps_decompress_bytes", return_value=b"incorrect"), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as progress:
            with self.assertRaisesRegex(SystemExit, "SHA-256 round-trip verification FAILED"):
                ahps.run_benchmark_on_buffer(b"fixture", "fixture", lzma_preset=0,
                                             ahps_only=True, show_progress=True)
        self.assertNotIn("100%", progress.getvalue())
        self.assertIn("Failed", progress.getvalue())
        self.assertNotIn("SHA-256 Verified: True", output.getvalue())

    def test_empty_input_retains_verified_result(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            ahps.run_benchmark_on_buffer(b"", "empty", lzma_preset=0,
                                         ahps_only=True, show_progress=False)
        self.assertIn("SHA-256 Verified: True", output.getvalue())


if __name__ == "__main__":
    unittest.main()
