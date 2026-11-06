"""Progress integration preserves archives, reports, and failure semantics."""

import contextlib
import io
import lzma
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest import mock

import ahps
import ahps_safetensors as tensors
from tests.test_csv import fixture as csv_fixture
from tests.test_safetensors import v7_fixture


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class ProgressCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "input.bin"
        self.archive_path = self.folder / "input.ahps"
        self.target = self.folder / "restored.bin"

    def assert_progress(self, updates, completed=True):
        self.assertTrue(updates)
        fractions = [fraction for fraction, _ in updates]
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(all(0 <= value <= 1 for value in fractions))
        self.assertTrue(all(isinstance(stage, str) and stage for _, stage in updates))
        if completed:
            self.assertEqual(fractions[-1], 1.0)
        else:
            self.assertLess(max(fractions), 1.0)

    def run_cli(self, arguments, stderr=None):
        stdout = io.StringIO()
        stderr = io.StringIO() if stderr is None else stderr
        with mock.patch.object(sys, "argv", ["ahps.py"] + arguments), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            ahps.main()
        return stdout.getvalue(), stderr.getvalue()


class CodecProgressTests(ProgressCase):
    def test_callback_preserves_archive_bytes_for_each_encoder_mode(self):
        rng = random.Random(42)
        floats = b"".join(b"\0\0" + rng.getrandbits(16).to_bytes(2, "little")
                          for _ in range(256))
        layout = tensors.TensorLayout(len(floats), 0, (
            tensors.TensorSpan("weights", "F32", (256,), 0, len(floats)),))
        cases = [
            ("ordinary", b"ordinary binary data\x00\x01\x02" * 16, {}),
            ("empty", b"", {}),
            ("csv", b'first,second\r\n"one,two","three\nfour"\r\nfive,six', {"csv_mode": True}),
            ("tensor", floats, {"safetensors_layout": layout}),
        ]
        for name, data, options in cases:
            with self.subTest(mode=name):
                baseline, baseline_report = ahps.ahps_compress_bytes(
                    data, force_python=True, lzma_preset=0, **options)
                updates = []
                observed, report = ahps.ahps_compress_bytes(
                    data, force_python=True, lzma_preset=0,
                    progress=lambda value, stage: updates.append((value, stage)), **options)
                self.assertEqual(observed, baseline)
                self.assertEqual(report.programs, baseline_report.programs)
                self.assert_progress(updates)
                self.assertEqual(updates[0][0], 0.0)
                self.assertEqual(ahps.ahps_decompress_bytes(observed), data)

    def test_buffered_and_file_callbacks_for_all_container_types(self):
        ordinary = b"ordinary bytes\x00" * 8
        ordinary_archive, _ = ahps.ahps_compress_bytes(ordinary, lzma_preset=0)
        csv_data, csv_raw = csv_fixture()
        tensor_data, tensor_raw = v7_fixture()
        cases = ((ordinary, ordinary_archive), (csv_data, lzma.compress(csv_raw, preset=0)),
                 (tensor_data, lzma.compress(tensor_raw, preset=0)))
        for data, encoded in cases:
            with self.subTest(magic=lzma.decompress(encoded)[:5]):
                buffered_updates = []
                restored = ahps.ahps_decompress_bytes(
                    encoded, force_python=True,
                    progress=lambda value, stage: buffered_updates.append((value, stage)))
                self.assertEqual(restored, ahps.ahps_decompress_bytes(encoded, force_python=True))
                self.assertEqual(restored, data)
                self.assert_progress(buffered_updates)
                self.archive_path.write_bytes(encoded)
                file_updates = []
                self.assertEqual(ahps.ahps_decompress_file(
                    self.archive_path, self.target, force_python=True,
                    progress=lambda value, stage: file_updates.append((value, stage))), len(data))
                self.assertEqual(self.target.read_bytes(), data)
                self.assert_progress(file_updates)

    def test_chunk_planning_callbacks_remain_monotonic(self):
        data = bytes(range(64)) * 8
        updates = []
        with mock.patch.multiple(ahps, EXTREME_LIMIT=128, PLAN_CHUNK_LEN=128):
            baseline, _ = ahps.ahps_compress_bytes(data, block_size=64, force_python=True, lzma_preset=0)
            encoded, _ = ahps.ahps_compress_bytes(
                data, block_size=64, force_python=True, lzma_preset=0,
                progress=lambda value, stage: updates.append((value, stage)))
        self.assertEqual(encoded, baseline)
        self.assert_progress(updates)
        self.assertTrue(any(stage == "Planning chunks" for _, stage in updates))

    def test_failed_atomic_replace_never_reports_completion(self):
        data, raw = v7_fixture()
        self.archive_path.write_bytes(lzma.compress(raw, preset=0))
        self.target.write_bytes(b"existing output")
        updates = []
        with mock.patch.object(ahps.os, "replace", side_effect=OSError("destination unavailable")):
            with self.assertRaisesRegex(OSError, "destination unavailable"):
                ahps.ahps_decompress_file(
                    self.archive_path, self.target,
                    progress=lambda value, stage: updates.append((value, stage)))
        self.assert_progress(updates, completed=False)
        self.assertEqual(self.target.read_bytes(), b"existing output")
        self.assertFalse(list(self.folder.glob("*.tmp")))

    def test_crc_failure_does_not_report_completed_decode(self):
        _, raw = v7_fixture()
        damaged = bytearray(raw)
        damaged[21] ^= 1
        encoded = lzma.compress(damaged, preset=0)
        updates = []
        with self.assertRaisesRegex(ValueError, "CRC"):
            ahps.ahps_decompress_bytes(encoded,
                progress=lambda value, stage: updates.append((value, stage)))
        self.assert_progress(updates, completed=False)


class CliProgressTests(ProgressCase):
    def setUp(self):
        super().setUp()
        self.source.write_bytes(b"small progress integration fixture\n" * 4)

    def compression_arguments(self):
        return ["compress", str(self.source), str(self.archive_path), "-p", "0"]

    def test_default_non_tty_and_explicit_disabled_are_silent_on_stderr(self):
        for prefix, suffix, stderr in (([], [], io.StringIO()),
                                      (["--no-progress"], [], TtyBuffer()),
                                      ([], ["--no-progress"], TtyBuffer()),
                                      (["--progress"], ["--no-progress"], TtyBuffer())):
            with self.subTest(prefix=prefix, suffix=suffix):
                stdout, progress = self.run_cli(prefix + self.compression_arguments() + suffix, stderr)
                self.assertEqual(progress, "")
                self.assertIn("Compressed Size", stdout)
                self.assertIn("[+] Wrote", stdout)

    def test_forced_and_auto_tty_success_preserve_stdout_report(self):
        # Freeze report timings only, so the complete stdout can be compared.
        with mock.patch.object(ahps.time, "perf_counter", return_value=10.0):
            expected, _ = self.run_cli(self.compression_arguments() + ["--no-progress"])
            baseline = self.archive_path.read_bytes()
            for prefix, suffix, stderr in ((["--progress"], [], io.StringIO()),
                                          ([], ["--progress"], io.StringIO()),
                                          ([], [], TtyBuffer())):
                with self.subTest(prefix=prefix, suffix=suffix):
                    stdout, progress = self.run_cli(prefix + self.compression_arguments() + suffix, stderr)
                    self.assertEqual(stdout, expected)
                    self.assertEqual(self.archive_path.read_bytes(), baseline)
                    self.assertIn("100%", progress)
                    self.assertIn("Done", progress)
                    self.assertNotIn("100%", stdout)

    def test_decompress_success_and_corrupt_input_failure(self):
        self.run_cli(self.compression_arguments() + ["--no-progress"])
        stdout, progress = self.run_cli(["decompress", str(self.archive_path), str(self.target), "--progress"])
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertIn("[+] Restored", stdout)
        self.assertIn("100%", progress)
        self.archive_path.write_bytes(b"corrupt archive")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["ahps.py", "decompress", str(self.archive_path),
                                             str(self.target), "--progress"]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                ahps.main()
        self.assertNotIn("100%", stderr.getvalue())
        self.assertIn("Failed", stderr.getvalue())
        self.assertNotIn("[+] Restored", stdout.getvalue())
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())

    def test_benchmark_success_and_hash_failure_have_correct_completion(self):
        arguments = ["bench", str(self.source), "-p", "0", "--progress"]
        stdout, progress = self.run_cli(arguments)
        self.assertIn("SHA-256 Verified: True", stdout)
        self.assertIn("100%", progress)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["ahps.py"] + arguments), \
                mock.patch.object(ahps, "ahps_decompress_bytes", return_value=b"wrong bytes"), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaisesRegex(SystemExit, "SHA-256 round-trip verification FAILED"):
                ahps.main()
        self.assertNotIn("100%", stderr.getvalue())
        self.assertIn("Failed", stderr.getvalue())
        self.assertNotIn("SHA-256 Verified: True", stdout.getvalue())

    def test_selftest_progress_flags_reach_benchmark(self):
        for arguments, expected in ((["--progress", "selftest"], True),
                                    (["selftest", "--progress"], True),
                                    (["--progress", "selftest", "--no-progress"], False)):
            with self.subTest(arguments=arguments):
                with mock.patch.object(ahps, "run_benchmark_on_buffer") as benchmark:
                    self.run_cli(arguments)
                self.assertIs(benchmark.call_args.kwargs["show_progress"], expected)


if __name__ == "__main__":
    unittest.main()
