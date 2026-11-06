"""CSV byte preservation, v6 validation, and optional encoder selection."""

import contextlib
import io
import lzma
from pathlib import Path
import random
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zlib

import ahps
import ahps_csv


def fixture():
    """Independently encoded two-row CSV with an empty final field at EOF."""
    data = b"a,b\r\nx,"
    table = struct.pack("<IIIII", 2, 2, 3, 2, 0)
    head = struct.pack("<5sIIQI", b"AHPS\x06", 2, len(table), len(data), zlib.crc32(data))
    return data, head + table + b"a,x,b\r\n"


def archive(raw):
    return lzma.compress(raw, preset=0)


class CsvTransformTests(unittest.TestCase):
    def test_exact_lexical_round_trips(self):
        samples = [
            b"a,b\n1,2\n",
            b"a,b\r\n1,2",
            b"a,b\r1,2\r",
            b"a,b\r\n1,2\n3,4\r5,6",
            b"a,b\nx,",
            b",\n,",
            b'a,b\n"",\n,""\n',
            b'a,b\n"one,two","line 1\nline 2\r\nline 3"\n',
            b'a,b\n"He said ""yes""",""""\n',
            b'\xef\xbb\xbf"a",b\r\n"uno,dos",tres\r\n',
            'nombre,texto\nDaniel,"niño, café 日本語"\n'.encode("utf-8"),
        ]
        for data in samples:
            with self.subTest(data=data):
                packed = ahps_csv.pack_csv_container(data)
                self.assertIsNotNone(packed)
                restored = io.BytesIO()
                self.assertEqual(ahps_csv.restore_csv_container(packed, restored), len(data))
                self.assertEqual(restored.getvalue(), data)

    def test_wire_format_matches_independent_fixture(self):
        data, raw = fixture()
        self.assertEqual(ahps_csv.pack_csv_container(data), raw)
        output = io.BytesIO()
        self.assertEqual(ahps_csv.restore_csv_container(raw, output), len(data))
        self.assertEqual(output.getvalue(), data)

    def test_unsupported_inputs_decline_candidate(self):
        samples = [
            b"", b"a,b", b"a\n1\n", b"a;b\n1;2\n",
            b"a,b\n1,2,3\n", b"a,b\n1\n", b"a,b\n\n",
            b'a,b\n"unterminated,2\n', b'a,b\nx"y,2\n',
            b'a,b\n"value"junk,2\n', b'a,b\n"value" ,2\n',
        ]
        for data in samples:
            with self.subTest(data=data):
                self.assertIsNone(ahps_csv.pack_csv_container(data))


class CsvDecoderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "input.ahps"
        self.target = self.folder / "restored.csv"

    def assert_rejected(self, encoded, limits=ahps.DEFAULT_LIMITS):
        self.source.write_bytes(encoded)
        self.target.write_bytes(b"existing contents")
        with self.assertRaises((ValueError, lzma.LZMAError)):
            ahps.ahps_decompress_bytes(encoded, limits=limits)
        with self.assertRaises((ValueError, lzma.LZMAError)):
            ahps.ahps_decompress_file(self.source, self.target, limits=limits)
        self.assertEqual(self.target.read_bytes(), b"existing contents")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["input.ahps", "restored.csv"])

    def test_both_public_decoders_accept_v6(self):
        data, raw = fixture()
        encoded = archive(raw)
        self.source.write_bytes(encoded)
        for force_python in (False, True):
            with self.subTest(force_python=force_python):
                self.assertEqual(ahps.ahps_decompress_bytes(encoded, force_python), data)
                self.assertEqual(ahps.ahps_decompress_file(self.source, self.target, force_python), len(data))
                self.assertEqual(self.target.read_bytes(), data)

    def test_dimensions_lengths_crc_and_trailing_data_are_rejected(self):
        _, raw = fixture()
        damaged = [raw[:-1], raw + b"x", raw[:20]]
        for offset, value in ((5, 0), (5, 0xffffffff), (9, 0),
                              (25, 0), (25, 0xffffffff), (29, 0xffffffff), (29, 1)):
            edited = bytearray(raw)
            struct.pack_into("<I", edited, offset, value)
            damaged.append(bytes(edited))
        edited = bytearray(raw)
        edited[21] ^= 1
        damaged.append(bytes(edited))
        for index, candidate in enumerate(damaged):
            with self.subTest(index=index):
                self.assert_rejected(archive(candidate))
        self.assert_rejected(archive(raw) + b"x")
        self.assert_rejected(archive(raw)[:-1])

    def test_declared_output_and_container_limits_apply(self):
        data, raw = fixture()
        encoded = archive(raw)
        for limits in (
            ahps.DecodeLimits(max_output_bytes=len(data) - 1),
            ahps.DecodeLimits(max_container_bytes=len(raw) - 1),
            ahps.DecodeLimits(max_dict_bytes=1),
        ):
            with self.subTest(limits=limits):
                self.assert_rejected(encoded, limits)
        exact = ahps.DecodeLimits(max_container_bytes=len(raw), max_output_bytes=len(data))
        self.assertEqual(ahps.ahps_decompress_bytes(encoded, limits=exact), data)


class CsvEncoderTests(unittest.TestCase):
    def test_opt_in_preserves_smaller_baseline_and_invalid_csv(self):
        for data in (b"a,b\nx,y\n", b"plain non-CSV bytes", b"a,b\nx,y,z\n"):
            with self.subTest(data=data):
                baseline, _ = ahps.ahps_compress_bytes(data, lzma_preset=0)
                selected = []
                for threshold in (sys.maxsize, 0):
                    with mock.patch.object(ahps, "CSV_PARALLEL_MIN_LEN", threshold), \
                            mock.patch.object(ahps.os, "cpu_count", return_value=2):
                        encoded, _ = ahps.ahps_compress_bytes(data, lzma_preset=0, csv_mode=True)
                    selected.append(encoded)
                    self.assertLessEqual(len(encoded), len(baseline))
                    self.assertEqual(ahps.ahps_decompress_bytes(encoded), data)
                self.assertEqual(selected[0], selected[1])
                if ahps_csv.pack_csv_container(data) is None:
                    self.assertEqual(selected[0], baseline)

    def test_real_column_candidate_can_win(self):
        # Consecutive copies are farther apart than preset 0's 256 KiB
        # dictionary in row order, and adjacent after transposition.
        rng = random.Random(20261105)
        first = bytes(rng.choices(b"abcdefghijklmnopqrstuvwxyz ", k=140000))
        second = bytes(rng.choices(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ ", k=140000))
        data = b"first,second\n" + (first + b"," + second + b"\n") * 4
        plan = [ahps.Segment(0, len(data), [])]
        with mock.patch.object(ahps, "plan_segments", return_value=plan), \
                mock.patch.object(ahps, "rival_plans", return_value=[]):
            baseline, _ = ahps.ahps_compress_bytes(data, lzma_preset=0)
            selected = []
            for threshold in (sys.maxsize, 0):
                with mock.patch.object(ahps, "CSV_PARALLEL_MIN_LEN", threshold), \
                        mock.patch.object(ahps.os, "cpu_count", return_value=2):
                    start = time.perf_counter()
                    encoded, report = ahps.ahps_compress_bytes(data, lzma_preset=0, csv_mode=True)
                    elapsed = time.perf_counter() - start
                selected.append(encoded)
                self.assertLess(len(encoded), len(baseline))
                self.assertEqual(lzma.decompress(encoded)[:5], b"AHPS\x06")
                self.assertEqual(report.output_bytes, len(encoded))
                self.assertLessEqual(report.elapsed_sec, elapsed)
                self.assertEqual(ahps.ahps_decompress_bytes(encoded), data)
        self.assertEqual(selected[0], selected[1])

    def test_small_unsupported_and_single_cpu_inputs_avoid_worker(self):
        cases = ((b"a,b\nx,y\n", 1 << 20, 2),
                 (b"plain non-CSV bytes", 0, 2),
                 (b"a,b\nx,y\n", 0, 1),
                 (b"a,b\nx,y\n", 0, None))
        for data, threshold, cpus in cases:
            with self.subTest(data=data, threshold=threshold, cpus=cpus), \
                    mock.patch.object(ahps, "CSV_PARALLEL_MIN_LEN", threshold), \
                    mock.patch.object(ahps.os, "cpu_count", return_value=cpus), \
                    mock.patch.object(ahps, "ThreadPoolExecutor") as pool:
                encoded, _ = ahps.ahps_compress_bytes(data, lzma_preset=0, csv_mode=True)
            pool.assert_not_called()
            self.assertEqual(ahps.ahps_decompress_bytes(encoded), data)

    def test_equal_size_csv_candidate_keeps_baseline_in_both_paths(self):
        data = b"a,b\nx,y\n"
        baseline, baseline_report = ahps.ahps_compress_bytes(data, lzma_preset=0)
        original = ahps.xz_compress

        def tie_csv(raw, preset):
            if raw[:5] == ahps_csv.MAGIC:
                return bytes(len(baseline))
            return original(raw, preset)

        for threshold in (sys.maxsize, 0):
            with self.subTest(threshold=threshold), \
                    mock.patch.object(ahps, "CSV_PARALLEL_MIN_LEN", threshold), \
                    mock.patch.object(ahps.os, "cpu_count", return_value=2), \
                    mock.patch.object(ahps, "xz_compress", side_effect=tie_csv):
                encoded, report = ahps.ahps_compress_bytes(data, lzma_preset=0, csv_mode=True)
            self.assertEqual(encoded, baseline)
            self.assertEqual(report.programs, baseline_report.programs)

    def test_csv_worker_failure_propagates_and_joins_worker(self):
        original = ahps.xz_compress
        workers = []

        def fail_csv(raw, preset):
            if raw[:5] == ahps_csv.MAGIC:
                workers.append(threading.current_thread())
                raise RuntimeError("CSV worker failed")
            return original(raw, preset)

        with mock.patch.object(ahps, "CSV_PARALLEL_MIN_LEN", 0), \
                mock.patch.object(ahps.os, "cpu_count", return_value=2), \
                mock.patch.object(ahps, "xz_compress", side_effect=fail_csv):
            with self.assertRaisesRegex(RuntimeError, "CSV worker failed"):
                ahps.ahps_compress_bytes(b"a,b\nx,y\n", lzma_preset=0, csv_mode=True)
        self.assertEqual(len(workers), 1)
        self.assertIsNot(workers[0], threading.current_thread())
        self.assertFalse(workers[0].is_alive())

    def test_cli_benchmark_passes_csv_option(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            source.write_bytes(b"a,b\nx,y\n")
            with mock.patch.object(sys, "argv", ["ahps.py", "bench", str(source), "--csv"]), \
                    mock.patch.object(ahps, "run_benchmark_on_buffer") as benchmark:
                ahps.main()
            self.assertTrue(benchmark.call_args.kwargs["csv_mode"])

    def test_cli_compress_and_decompress_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            encoded = Path(folder) / "compressed.ahps"
            restored = Path(folder) / "restored.csv"
            data = b'a,b\r\n"quoted,newline\nvalue",""\r\n'
            source.write_bytes(data)
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(sys, "argv", ["ahps.py", "compress", str(source), str(encoded), "--csv", "-p", "0"]):
                    ahps.main()
                with mock.patch.object(sys, "argv", ["ahps.py", "decompress", str(encoded), str(restored)]):
                    ahps.main()
            self.assertEqual(restored.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
