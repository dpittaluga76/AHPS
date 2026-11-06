#!/usr/bin/env python3
"""ahps_decompress_file must accept and reject exactly what ahps_decompress_bytes does.

Synthetic data and the preserved v3 fixtures only; no corpus is read.
"""

import ctypes
import io
import lzma
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import ahps
from test_search_method import code_bytes


GOLDEN = Path(__file__).resolve().parent / "tests" / "fixtures" / "v3"
ENGINES = (True, False) if ahps.SIMD_BRIDGE.lib is not None else (True,)
SMALL_LIMITS = ahps.DecodeLimits(16 << 20, 1 << 20, 1 << 20)


def container(data, table, count, residual=None, magic=ahps.MAGIC_HEADER, crc=None):
    residual = data if residual is None else residual
    crc = zlib.crc32(data) if crc is None else crc
    return magic + struct.pack("<IIQI", count, len(table), len(data), crc) + table + residual


def archive_of(raw):
    return lzma.compress(raw, format=lzma.FORMAT_XZ, preset=0)


def raw_table(*lengths):
    return b"".join(struct.pack("<IB", length, 0) for length in lengths)


def mixed_archive():
    """A RAW, an EXE_SPLIT and a chained segment, built without the planner."""
    head, code, tail = bytes(range(256)) * 4, code_bytes(64), bytes((i * 7) & 0xFF for i in range(5000))
    split = (ahps.OP_EXE_SPLIT, bytes(4))
    delta, plane = (ahps.OP_DELTA1_LANE, b"\x04\0\0\0"), (ahps.OP_SPLIT, b"\x04\0\0\0")
    lifted_code = ahps.py_forward_lift(split[0], split[1], code)
    lifted_tail = ahps.py_forward_lift(plane[0], plane[1], ahps.py_forward_lift(delta[0], delta[1], tail))
    table = (struct.pack("<IB", len(head), 0)
             + struct.pack("<IB", len(code), 1) + bytes([split[0]]) + split[1]
             + struct.pack("<IB", len(tail), 2) + bytes([delta[0]]) + delta[1] + bytes([plane[0]]) + plane[1])
    data = head + code + tail
    return archive_of(container(data, table, 3, head + lifted_code + lifted_tail)), data


class FileCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "input.ahps"
        self.target = self.folder / "restored.bin"

    def decode(self, archive, force_python=False, limits=ahps.DEFAULT_LIMITS):
        self.source.write_bytes(archive)
        return ahps.ahps_decompress_file(self.source, self.target, force_python, limits)

    def assert_same_rejection(self, archive, limits=ahps.DEFAULT_LIMITS, kind=ValueError):
        """Both decoders refuse it, and the streaming one leaves an existing output as it was."""
        self.target.write_bytes(b"previous output")
        for force_python in ENGINES:
            with self.subTest(force_python=force_python):
                with self.assertRaises((kind, lzma.LZMAError)):
                    ahps.ahps_decompress_bytes(archive, force_python, limits)
                with self.assertRaises((kind, lzma.LZMAError)):
                    self.decode(archive, force_python, limits)
                self.assertEqual(self.target.read_bytes(), b"previous output")
                self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["input.ahps", "restored.bin"])


class ParityTests(FileCase):
    def test_every_engine_pairing_restores_the_same_bytes(self):
        data = code_bytes() + bytes(range(256)) * 8
        for encode_python in ENGINES:
            archive, _ = ahps.ahps_compress_bytes(data, 256, encode_python, 1)
            for decode_python in ENGINES:
                with self.subTest(encode_python=encode_python, decode_python=decode_python):
                    self.assertEqual(self.decode(archive, decode_python), len(data))
                    self.assertEqual(self.target.read_bytes(), data)

    def test_raw_transformed_and_chained_segments_in_one_archive(self):
        archive, data = mixed_archive()
        for force_python in ENGINES:
            with self.subTest(force_python=force_python):
                self.assertEqual(self.decode(archive, force_python), len(data))
                self.assertEqual(self.target.read_bytes(), data)
                self.assertEqual(ahps.ahps_decompress_bytes(archive, force_python), data)

    def test_chunked_plans_round_trip(self):
        data = code_bytes(400)
        with mock.patch.multiple(ahps, EXTREME_LIMIT=256, PLAN_CHUNK_LEN=1024):
            archive, _ = ahps.ahps_compress_bytes(data, 128, True, 1)
        self.assertEqual(self.decode(archive), len(data))
        self.assertEqual(self.target.read_bytes(), data)

    def test_empty_v4_and_v5_archives(self):
        for magic in (ahps.MAGIC_HEADER, ahps.MAGIC_HEADER_V4):
            with self.subTest(magic=magic):
                self.assertEqual(self.decode(archive_of(container(b"", b"", 0, magic=magic))), 0)
                self.assertEqual(self.target.read_bytes(), b"")

    def test_every_preserved_v3_fixture_matches_the_buffered_decoder(self):
        fixtures = sorted(GOLDEN.glob("*.ahps"))
        self.assertTrue(fixtures)
        for fixture in fixtures:
            archive = fixture.read_bytes()
            expected = ahps.ahps_decompress_bytes(archive)
            for force_python in ENGINES:
                with self.subTest(fixture=fixture.name, force_python=force_python):
                    self.assertEqual(self.decode(archive, force_python), len(expected))
                    self.assertEqual(self.target.read_bytes(), expected)

    def test_v4_opcode_nine_uses_the_later_meaning(self):
        data = code_bytes(32)
        params = bytes(4)
        residual = ahps.py_forward_lift(ahps.OP_EXE_SPLIT, params, data)
        table = struct.pack("<IB", len(data), 1) + bytes([ahps.OP_EXE_SPLIT]) + params
        archive = archive_of(container(data, table, 1, residual, ahps.MAGIC_HEADER_V4))
        self.assertEqual(self.decode(archive), len(data))
        self.assertEqual(self.target.read_bytes(), data)


class RejectionTests(FileCase):
    def test_truncated_xz_stream(self):
        archive, _ = mixed_archive()
        for cut in (0, 1, 13, len(archive) // 2, len(archive) - 1):
            with self.subTest(cut=cut):
                self.assert_same_rejection(archive[:cut])

    def test_data_after_the_xz_stream(self):
        archive, _ = mixed_archive()
        self.assert_same_rejection(archive + b"\0")
        self.assert_same_rejection(archive + b"trailing" * 20000)

    def test_v3_trailing_data_and_truncation(self):
        archive = (GOLDEN / "tiny_py.ahps").read_bytes()
        self.assert_same_rejection(archive + b"x")
        self.assert_same_rejection(archive[:-1])

    def test_wrong_crc(self):
        data = bytes(range(256))
        for magic in (ahps.MAGIC_HEADER, ahps.MAGIC_HEADER_V4):
            raw = container(data, raw_table(len(data)), 1, magic=magic, crc=zlib.crc32(data) ^ 1)
            self.assert_same_rejection(archive_of(raw))

    def test_bad_magic_and_short_headers(self):
        self.assert_same_rejection(archive_of(b"BAD!!" + bytes(40)))
        self.assert_same_rejection(archive_of(ahps.MAGIC_HEADER + bytes(10)))
        self.assert_same_rejection(archive_of(b""))

    def test_section_lengths_must_match_exactly(self):
        data = bytes(range(64))
        short = bytearray(container(data, raw_table(len(data)), 1))
        struct.pack_into("<Q", short, 13, len(data) + 1)
        self.assert_same_rejection(archive_of(bytes(short)))
        self.assert_same_rejection(archive_of(container(data, raw_table(len(data)), 1) + b"extra"))

    def test_undeclared_excess_stops_at_the_first_piece(self):
        raw = container(b"", b"", 0) + bytes(8 << 20)
        with open(self.source, "wb") as f:
            f.write(archive_of(raw))
        with open(self.source, "rb") as source:
            stream = ahps.ContainerStream(source, ahps.DEFAULT_LIMITS)
            with self.assertRaises(ValueError):
                ahps.restore_stream(stream, io.BytesIO(), False)
        self.assertLessEqual(stream.decoded, ahps.V4_HEADER_LEN + ahps.DECODE_STEP)

    def test_segment_table_errors(self):
        data = bytes(range(64))
        self.assert_same_rejection(archive_of(container(data, b"\0", 1)))
        self.assert_same_rejection(archive_of(container(data, raw_table(0, 64), 2)))
        self.assert_same_rejection(archive_of(container(data, struct.pack("<IB", 64, 1) + b"\xfe" + bytes(4), 1)))
        self.assert_same_rejection(archive_of(container(data, raw_table(63), 1)))

    def test_declared_sizes_cannot_force_a_large_allocation(self):
        """A table consistent with its header, but claiming a 1 GiB segment over four real bytes."""
        claimed = 1 << 30
        table = struct.pack("<IB", claimed, 1) + bytes([ahps.OP_EXE_SPLIT]) + bytes(4)
        raw = ahps.MAGIC_HEADER + struct.pack("<IIQI", 1, len(table), claimed, 0) + table + b"tiny"
        with mock.patch.object(ahps, "assemble", wraps=ahps.assemble) as built, \
                mock.patch.object(ctypes, "create_string_buffer", wraps=ctypes.create_string_buffer) as scratch:
            self.assert_same_rejection(archive_of(raw))
        self.assertTrue(built.called)
        for call in built.call_args_list:
            self.assertLess(call.args[1], 1 << 20)
        scratch.assert_not_called()

    def test_cli_failure_leaves_no_output(self):
        self.source.write_bytes(b"not an archive")
        args = mock.Mock(input=str(self.source), output=str(self.target), pure_python=False,
                         max_dict_mb=256, max_container_mb=4097, max_output_mb=4096)
        with self.assertRaises((ValueError, lzma.LZMAError)):
            ahps.cmd_decompress(args)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["input.ahps"])


class LimitTests(FileCase):
    def test_limits_match_the_buffered_decoder(self):
        data = bytes(5000)
        archive = archive_of(container(data, raw_table(len(data)), 1))
        for limits in (ahps.DecodeLimits(1 << 20, 1 << 20, 4999), ahps.DecodeLimits(1 << 20, 4000, 1 << 20),
                       ahps.DecodeLimits(1 << 20, 24, 1 << 20)):
            with self.subTest(limits=limits):
                self.assert_same_rejection(archive, limits, ahps.DecodeLimitError)
        limits = ahps.DecodeLimits(1 << 20, len(data) + 25 + 5, len(data))
        self.assertEqual(self.decode(archive, limits=limits), len(data))

    def test_dictionary_limit(self):
        archive = lzma.compress(ahps.MAGIC_HEADER + bytes(40), format=lzma.FORMAT_XZ, preset=9)
        self.assert_same_rejection(archive, ahps.DecodeLimits(1 << 16, 1 << 20, 1 << 20), ahps.DecodeLimitError)

    def test_v3_limits(self):
        archive = (GOLDEN / "lcg_py.ahps").read_bytes()
        self.assert_same_rejection(archive, ahps.DecodeLimits(256 << 20, 1 << 20, 1), ahps.DecodeLimitError)

    def test_rejects_anything_but_decode_limits(self):
        with self.assertRaises(TypeError):
            ahps.ahps_decompress_file(self.source, self.target, limits={})


class StreamingTests(FileCase):
    def test_archive_is_read_in_bounded_steps(self):
        archive, data = mixed_archive()
        sizes = []

        class Spy(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)

        target = io.BytesIO()
        self.assertEqual(ahps.restore_stream(ahps.ContainerStream(Spy(archive), ahps.DEFAULT_LIMITS), target, False), len(data))
        self.assertEqual(target.getvalue(), data)
        self.assertLessEqual(max(sizes), ahps.STREAM_READ_STEP)
        self.assertEqual(ahps.STREAM_READ_STEP, 64 * 1024)

    def test_raw_segments_are_written_in_bounded_steps(self):
        data = bytes(5000)
        archive = archive_of(container(data, raw_table(len(data)), 1))
        writes = []

        class Spy(io.BytesIO):
            def write(self, chunk):
                writes.append(len(chunk))
                return super().write(chunk)

        with mock.patch.object(ahps, "STREAM_RAW_STEP", 1024):
            ahps.restore_stream(ahps.ContainerStream(io.BytesIO(archive), ahps.DEFAULT_LIMITS), Spy(), False)
        self.assertEqual(writes, [1024] * 4 + [904])
        self.assertEqual(ahps.STREAM_RAW_STEP, 1024 * 1024)

    def test_input_and_output_may_be_the_same_file(self):
        archive, data = mixed_archive()
        self.source.write_bytes(archive)
        ahps.ahps_decompress_file(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), data)

    def test_missing_input_leaves_nothing(self):
        with self.assertRaises(FileNotFoundError):
            ahps.ahps_decompress_file(self.folder / "absent", self.target)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_name_collision_never_deletes_the_other_file(self):
        archive, _ = mixed_archive()
        self.source.write_bytes(archive)
        fixed = mock.Mock(hex="collision")
        other = self.folder / f"{self.target.name}.collision.tmp"
        other.write_bytes(b"not ours")
        with mock.patch.object(ahps.uuid, "uuid4", return_value=fixed):
            with self.assertRaises(FileExistsError):
                ahps.ahps_decompress_file(self.source, self.target)
        self.assertEqual(other.read_bytes(), b"not ours")
        self.assertFalse(self.target.exists())

    def test_interruption_removes_the_temporary_file(self):
        archive, _ = mixed_archive()
        self.source.write_bytes(archive)
        with mock.patch.object(ahps, "restore_stream", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ahps.ahps_decompress_file(self.source, self.target)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["input.ahps"])

    def test_cli_uses_the_streaming_decoder(self):
        archive, data = mixed_archive()
        self.source.write_bytes(archive)
        args = mock.Mock(input=str(self.source), output=str(self.target), pure_python=False,
                         max_dict_mb=256, max_container_mb=4097, max_output_mb=4096)
        with mock.patch.object(ahps, "ahps_decompress_bytes", side_effect=AssertionError("buffered path ran")):
            ahps.cmd_decompress(args)
        self.assertEqual(self.target.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
