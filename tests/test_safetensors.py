"""Exact tensor bytes, self-contained v7 decoding, and validated layouts."""

import contextlib
from dataclasses import replace
import io
import json
import lzma
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

import ahps
import ahps_rans
import ahps_safetensors as tensors


def safetensors_bytes(entries, metadata=None):
    """Build a small independent safetensors source in declared tensor order."""
    header = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    payload = bytearray()
    for name, dtype, shape, data in entries:
        start = len(payload)
        payload.extend(data)
        header[name] = {"dtype": dtype, "shape": shape,
                        "data_offsets": [start, len(payload)]}
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded + payload


def v7_fixture():
    """RAW prefix/tail and four F32 bit patterns with omitted zero low words."""
    floats = bytes.fromhex("0000803f 00000080 0000c07f 000080ff")
    data = b"head" + floats + b"tail"
    table = (struct.pack("<BII", 0, 4, 4)
             + struct.pack("<BII", 1, 16, 8)
             + struct.pack("<BII", 0, 4, 4))
    residual = b"head" + bytes.fromhex("8000c080 3f807fff") + b"tail"
    header = struct.pack("<5sIIQI", b"AHPS\x07", 3, len(table), len(data), zlib.crc32(data))
    return data, header + table + residual


class TensorSourceCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "source.safetensors"

    def layout(self, data):
        self.source.write_bytes(data)
        return tensors.parse_layout(self.source)


class TensorLayoutTests(TensorSourceCase):
    def test_caller_constructed_invalid_layouts_are_rejected_before_ranges(self):
        span = tensors.TensorSpan("w", "F32", (4,), 0, 16)
        layout = tensors.TensorLayout(16, 0, (span,))
        invalid = [
            replace(layout, source_size=-1),
            replace(layout, source_size=16.0),
            replace(layout, data_start=True),
            replace(layout, data_start=-1),
            replace(layout, data_start=17),
            replace(layout, tensors=()),
            replace(layout, tensors=(replace(span, start=True),)),
            replace(layout, tensors=(replace(span, end=16.0),)),
            replace(layout, tensors=(replace(span, end=20),)),
            replace(layout, tensors=(replace(span, shape=(3,)),)),
            replace(layout, tensors=(replace(span, shape=(True,)),)),
            replace(layout, tensors=(replace(span, shape=(-4,)),)),
            replace(layout, tensors=(replace(span, dtype="UNKNOWN"),)),
            replace(layout, tensors=(replace(span, name=3),)),
            tensors.TensorLayout(16, 0, (
                tensors.TensorSpan("bad", "F32", (1,), 8, 4),
                tensors.TensorSpan("tail", "F32", (3,), 4, 16))),
            tensors.TensorLayout(16, 0, (
                tensors.TensorSpan("duplicate", "F32", (1,), 0, 4),
                tensors.TensorSpan("duplicate", "F32", (3,), 4, 16))),
            tensors.TensorLayout(16, 0, (
                tensors.TensorSpan("head", "F32", (1,), 0, 4),
                tensors.TensorSpan("tail", "F32", (2,), 8, 16))),
            tensors.TensorLayout(16, 0, (
                tensors.TensorSpan("head", "F32", (2,), 0, 8),
                tensors.TensorSpan("tail", "F32", (3,), 4, 16))),
        ]
        with mock.patch.object(tensors, "_ranges") as ranges:
            for index, candidate in enumerate(invalid):
                with self.subTest(index=index):
                    with self.assertRaises(ValueError):
                        tensors.pack_tensor_container(bytes(16), candidate, force_python=True)
            ranges.assert_not_called()
        for candidate in (object(), replace(layout, tensors=(object(),))):
            with self.assertRaises(TypeError):
                tensors.pack_tensor_container(bytes(16), candidate, force_python=True)

    def test_caller_constructed_empty_tensors_preserve_coverage(self):
        layout = tensors.TensorLayout(16, 0, (
            tensors.TensorSpan("before", "F32", (0,), 0, 0),
            tensors.TensorSpan("values", "F32", (4,), 0, 16),
            tensors.TensorSpan("after", "F32", (2, 0), 16, 16)))
        tensors.validate_layout(layout)
        data = bytes(16)
        packed = tensors.pack_tensor_container(data, layout, force_python=True)
        restored = io.BytesIO()
        tensors.restore_tensor_container(packed, restored, force_python=True)
        self.assertEqual(restored.getvalue(), data)
        empty = tensors.TensorLayout(0, 0, ())
        tensors.validate_layout(empty)
        self.assertIsNone(tensors.pack_tensor_container(b"", empty, force_python=True))

    def test_empty_fp8_tensor_before_payload_keeps_positive_length_segments(self):
        data = safetensors_bytes([
            ("empty", "F8_E4M3", [0], b""),
            ("value", "F32", [1], bytes.fromhex("0000803f")),
        ])
        raw = tensors.pack_tensor_container(data, self.layout(data))
        restored = io.BytesIO()
        tensors.restore_tensor_container(raw, restored)
        self.assertEqual(restored.getvalue(), data)

    def test_metadata_multiple_empty_and_scalar_tensors(self):
        data = safetensors_bytes([
            ("scalar", "F32", [], bytes.fromhex("0000803f")),
            ("empty", "F32", [0, 3], b""),
            ("bytes", "U8", [3], b"abc"),
        ], metadata={"format": "pt"})
        layout = self.layout(data)
        self.assertEqual(layout.source_size, len(data))
        self.assertEqual(layout.data_start, 8 + struct.unpack_from("<Q", data)[0])
        self.assertEqual([(item.name, item.dtype, item.shape) for item in layout.tensors],
                         [("scalar", "F32", ()), ("empty", "F32", (0, 3)),
                          ("bytes", "U8", (3,))])
        self.assertEqual([(item.start, item.end) for item in layout.tensors],
                         [(layout.data_start, layout.data_start + 4),
                          (layout.data_start + 4, layout.data_start + 4),
                          (layout.data_start + 4, len(data))])

    def test_invalid_layouts_are_rejected(self):
        header = {"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}
        invalid = []
        for key, value in (("shape", [3]), ("shape", [-1]), ("shape", [True]),
                           ("data_offsets", [1, 9]), ("data_offsets", [8, 0]),
                           ("data_offsets", [0, 7]), ("dtype", "UNKNOWN")):
            changed = {"w": {**header["w"], key: value}}
            invalid.append(json.dumps(changed).encode())
        invalid.extend([
            b'{"w":{"dtype":"F32","dtype":"U8","shape":[8],"data_offsets":[0,8]}}',
            b'{"w":{"dtype":"F32","shape":[2],"data_offsets":[0,8]},"w":{"dtype":"F32","shape":[2],"data_offsets":[0,8]}}',
            b'{"a":{"dtype":"U8","shape":[4],"data_offsets":[0,4]},"b":{"dtype":"U8","shape":[4],"data_offsets":[3,7]}}',
            b'{"a":{"dtype":"U8","shape":[3],"data_offsets":[0,3]},"b":{"dtype":"U8","shape":[4],"data_offsets":[4,8]}}',
            b'[]', b'{"__metadata__":{"bad":3}}',
        ])
        for index, raw_header in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.layout(struct.pack("<Q", len(raw_header)) + raw_header + bytes(8))

    def test_truncated_header_and_unclaimed_tensor_bytes_are_rejected(self):
        valid = safetensors_bytes([("w", "U8", [3], b"abc")])
        for data in (b"", bytes(7), struct.pack("<Q", 2**63), valid[:-1], valid + b"x"):
            with self.subTest(length=len(data)):
                with self.assertRaises(ValueError):
                    self.layout(data)


class RansTests(unittest.TestCase):
    def test_independent_single_magnitude_wire_fixture(self):
        data = b"\0\x80"
        frequencies = [4096] + [0] * 127
        expected = b"\x02" + struct.pack("<I", 1 << 23)
        for force_python in (True, False):
            self.assertEqual(ahps_rans.encode_fp8(data, frequencies, force_python=force_python), expected)
            self.assertEqual(ahps_rans.decode_fp8(expected, 2, frequencies,
                                                 force_python=force_python), data)
        with self.assertRaises(ValueError):
            ahps_rans.decode_fp8(b"\x82" + expected[1:], 2, frequencies, force_python=True)

    def test_every_byte_and_partial_sign_groups_round_trip(self):
        frequencies = [32] * 128
        source = bytes(range(256)) * 4
        for size in (0, 1, 7, 8, 9, 255, 256, 1023):
            data = source[:size]
            with self.subTest(size=size):
                reference = ahps_rans.encode_fp8(data, frequencies, force_python=True)
                native = ahps_rans.encode_fp8(data, frequencies, force_python=False)
                self.assertEqual(native, reference)
                for force_python in (True, False):
                    self.assertEqual(ahps_rans.decode_fp8(reference, size, frequencies,
                                                         force_python=force_python), data)

    def test_negative_zero_signs_with_a_single_magnitude(self):
        data = bytes([0, 128, 128, 0, 128, 0, 0, 128, 128]) * 19
        frequencies = [4096] + [0] * 127
        encoded = ahps_rans.encode_fp8(data, frequencies, force_python=True)
        self.assertEqual(ahps_rans.decode_fp8(encoded, len(data), frequencies,
                                             force_python=True), data)

    def test_invalid_frequency_tables_and_absent_symbols(self):
        invalid = ([32] * 127, [32] * 129, [31] + [32] * 127,
                   [-1, 65] + [32] * 126, [32.0] * 128)
        valid_payload = ahps_rans.encode_fp8(b"x", [32] * 128, force_python=True)
        for frequencies in invalid:
            with self.subTest(frequencies=frequencies[:3]):
                with self.assertRaises((TypeError, ValueError)):
                    ahps_rans.encode_fp8(b"x", frequencies, force_python=True)
                with self.assertRaises((TypeError, ValueError)):
                    ahps_rans.decode_fp8(valid_payload, 1, frequencies, force_python=True)
        with self.assertRaises(ValueError):
            ahps_rans.encode_fp8(b"\x01", [4096] + [0] * 127, force_python=True)

    def test_truncated_extra_and_invalid_state_streams_are_rejected(self):
        frequencies = [32] * 128
        data = bytes(range(256)) * 4
        encoded = ahps_rans.encode_fp8(data, frequencies, force_python=True)
        for payload in (b"", encoded[:3], encoded[:-1], encoded + b"\0", bytes(len(encoded))):
            with self.subTest(length=len(payload)):
                with self.assertRaises(ValueError):
                    ahps_rans.decode_fp8(payload, len(data), frequencies, force_python=True)


class TensorContainerTests(TensorSourceCase):
    def test_independent_float_fixture(self):
        data, raw = v7_fixture()
        restored = io.BytesIO()
        self.assertEqual(tensors.restore_tensor_container(raw, restored, force_python=True), len(data))
        self.assertEqual(restored.getvalue(), data)

    def test_float_slices_preserve_partial_elements_and_ieee_bits(self):
        values = bytes.fromhex("0000803f 00000080 0000c07f 000080ff") * 8
        source = safetensors_bytes([("f32", "F32", [32], values), ("tail", "U8", [3], b"end")])
        layout = self.layout(source)
        for start, end in ((0, len(source)), (layout.data_start + 1, len(source) - 4),
                           (layout.data_start + 2, layout.data_start + 119),
                           (layout.data_start + 3, layout.data_start + 120)):
            with self.subTest(start=start, end=end):
                data = source[start:end]
                packed = tensors.pack_tensor_container(data, layout, source_offset=start, force_python=True)
                self.assertIsNotNone(packed)
                output = io.BytesIO()
                self.assertEqual(tensors.restore_tensor_container(packed, output, force_python=True), len(data))
                self.assertEqual(output.getvalue(), data)

    def test_float_low_bits_are_never_discarded(self):
        values = bytes.fromhex("0100803f 00010080 0000c07f 010080ff") * 16
        source = safetensors_bytes([("f32", "F32", [64], values)])
        layout = self.layout(source)
        packed = tensors.pack_tensor_container(source, layout, force_python=True)
        if packed is not None:
            output = io.BytesIO()
            tensors.restore_tensor_container(packed, output, force_python=True)
            self.assertEqual(output.getvalue(), source)

    def test_fp8_biased_signed_values_and_uniform_fallback(self):
        for data, should_transform in ((bytes([0, 128, 1, 129]) * 1024, True),
                                       (bytes(range(256)) * 16, False)):
            with self.subTest(should_transform=should_transform):
                source = safetensors_bytes([("fp8", "F8_E4M3", [len(data)], data)])
                layout = self.layout(source)
                packed = tensors.pack_tensor_container(source, layout, force_python=True)
                if not should_transform:
                    self.assertIsNone(packed)
                    continue
                self.assertIsNotNone(packed)
                output = io.BytesIO()
                tensors.restore_tensor_container(packed, output, force_python=True)
                self.assertEqual(output.getvalue(), source)

    def test_independent_fp8_frequency_table_and_invalid_frequency_sum(self):
        data = b"\0\x80" * 1024
        frequencies = struct.pack("<128H", 4096, *([0] * 127))
        payload = frequencies + b"\xaa" * 256 + struct.pack("<I", 1 << 23)
        table = struct.pack("<BII", 2, len(data), len(payload))
        header = struct.pack("<5sIIQI", b"AHPS\x07", 1, len(table), len(data), zlib.crc32(data))
        raw = header + table + payload
        for force_python in (True, False):
            output = io.BytesIO()
            tensors.restore_tensor_container(raw, output, force_python=force_python)
            self.assertEqual(output.getvalue(), data)
        damaged = bytearray(raw)
        struct.pack_into("<H", damaged, len(header) + len(table), 4095)
        with self.assertRaises(ValueError):
            tensors.restore_tensor_container(damaged, io.BytesIO(), force_python=True)

    def test_both_public_decoders_and_atomic_failure(self):
        data, raw = v7_fixture()
        archive_path = self.folder / "input.ahps"
        target = self.folder / "restored.bin"
        encoded = lzma.compress(raw, preset=0)
        archive_path.write_bytes(encoded)
        for force_python in (True, False):
            self.assertEqual(ahps.ahps_decompress_bytes(encoded, force_python=force_python), data)
            self.assertEqual(ahps.ahps_decompress_file(archive_path, target, force_python=force_python), len(data))
            self.assertEqual(target.read_bytes(), data)

        bad = []
        for offset, value in ((5, 0xffffffff), (9, 1), (26, 0xffffffff), (30, 1)):
            changed = bytearray(raw)
            struct.pack_into("<I", changed, offset, value)
            bad.append(bytes(changed))
        changed = bytearray(raw)
        changed[25] = 255
        bad.append(bytes(changed))
        changed = bytearray(raw)
        changed[21] ^= 1
        bad.extend((bytes(changed), raw[:-1], raw + b"x"))
        archives = [lzma.compress(item, preset=0) for item in bad]
        archives.extend((encoded[:-1], encoded + b"x"))
        for index, candidate in enumerate(archives):
            with self.subTest(index=index):
                archive_path.write_bytes(candidate)
                target.write_bytes(b"previous output")
                with self.assertRaises((ValueError, lzma.LZMAError)):
                    ahps.ahps_decompress_bytes(candidate)
                with self.assertRaises((ValueError, lzma.LZMAError)):
                    ahps.ahps_decompress_file(archive_path, target)
                self.assertEqual(target.read_bytes(), b"previous output")
                self.assertFalse(list(self.folder.glob("*.tmp")))

    def test_output_and_container_limits(self):
        data, raw = v7_fixture()
        encoded = lzma.compress(raw, preset=0)
        for limits in (ahps.DecodeLimits(max_output_bytes=len(data) - 1),
                       ahps.DecodeLimits(max_container_bytes=len(raw) - 1)):
            with self.subTest(limits=limits):
                with self.assertRaises(ValueError):
                    ahps.ahps_decompress_bytes(encoded, limits=limits)
        exact = ahps.DecodeLimits(max_output_bytes=len(data), max_container_bytes=len(raw))
        self.assertEqual(ahps.ahps_decompress_bytes(encoded, limits=exact), data)

    def test_v7_chunk_output_cap_is_enforced_from_header_alone(self):
        header = struct.pack("<5sIIQI", b"AHPS\x07", 1, 9, tensors.MAX_CHUNK_BYTES + 1, 0)
        with self.assertRaises(ahps.DecodeLimitError):
            ahps.check_declared_sizes(header, ahps.DEFAULT_LIMITS)

    def test_real_float_candidate_can_win_and_restore(self):
        rng = random.Random(7)
        values = b"".join(b"\0\0" + rng.getrandbits(16).to_bytes(2, "little") for _ in range(4096))
        source = safetensors_bytes([("f32", "F32", [4096], values)])
        layout = self.layout(source)
        raw_plan = [ahps.Segment(0, len(source), [])]
        with mock.patch.object(ahps, "plan_segments", return_value=raw_plan), \
                mock.patch.object(ahps, "rival_plans", return_value=[]):
            baseline, _ = ahps.ahps_compress_bytes(source, force_python=True, lzma_preset=0)
            selected, report = ahps.ahps_compress_bytes(source, force_python=True, lzma_preset=0,
                                                       safetensors_layout=layout)
        self.assertLess(len(selected), len(baseline))
        self.assertEqual(lzma.decompress(selected)[:5], b"AHPS\x07")
        self.assertEqual(report.output_bytes, len(selected))
        self.assertEqual(ahps.ahps_decompress_bytes(selected, force_python=True), source)

    def test_cli_full_source_and_offset_slice(self):
        values = bytes.fromhex("0000803f 00000080 0000c07f 000080ff") * 8
        source = safetensors_bytes([("f32", "F32", [32], values)])
        layout = self.layout(source)
        chunk = self.folder / "slice.bin"
        offset = layout.data_start + 1
        chunk.write_bytes(source[offset:-1])
        encoded = self.folder / "compressed.ahps"
        restored = self.folder / "restored.bin"
        for input_path, options in ((self.source, ["--safetensors"]),
                                    (chunk, ["--safetensors", str(self.source), "--source-offset", str(offset)])):
            with self.subTest(input=input_path.name), contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(sys, "argv", ["ahps.py", "compress", str(input_path), str(encoded), "-p", "0"] + options):
                    ahps.main()
                with mock.patch.object(sys, "argv", ["ahps.py", "decompress", str(encoded), str(restored)]):
                    ahps.main()
                self.assertEqual(restored.read_bytes(), input_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
