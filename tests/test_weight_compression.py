"""Synthetic contracts for the optional NumPy weight-compression experiment."""

import hashlib
import importlib.util
import lzma
import struct
import unittest
from unittest import mock
import zlib


HAVE_DEPENDENCIES = all(
    importlib.util.find_spec(name) is not None
    for name in ("numpy", "zstandard", "brotli")
)
if HAVE_DEPENDENCIES:
    from research import weight_compression as wc
    from research import weight_transforms as wt


def scalar_transform(data, method):
    """Implement the documented wire order with scalar byte/bit arithmetic."""
    if method == "raw":
        return data
    aligned = len(data) // 8 * 8
    block_size = aligned if method.endswith("_full") else 65536
    result = bytearray()
    for start in range(0, aligned, max(1, block_size)):
        block = data[start:min(start + block_size, aligned)]
        if method == "delta_bitplanes":
            block = bytes([block[0]] + [
                (block[index] - block[index - 1]) % 256
                for index in range(1, len(block))
            ])
        packed = bytearray(len(block))
        if method.startswith("fp8_fields"):
            output_bit = 0
            for group in ((7,), (3, 4, 5, 6), (0, 1, 2)):
                for value in block:
                    for bit in group:
                        packed[output_bit // 8] |= ((value >> bit) & 1) << (output_bit % 8)
                        output_bit += 1
        else:
            for index, value in enumerate(block):
                for bit in range(8):
                    output_bit = bit * len(block) + index
                    packed[output_bit // 8] |= ((value >> bit) & 1) << (output_bit % 8)
        result.extend(packed)
    result.extend(data[aligned:])
    return bytes(result)


@unittest.skipUnless(HAVE_DEPENDENCIES, "requires numpy, zstandard, and brotli")
class WeightTransformTests(unittest.TestCase):
    def test_scalar_oracle_at_byte_group_and_block_boundaries(self):
        lengths = (0, 1, 7, 8, 9, 15, 16, 17, 65535, 65536, 65537, 65543, 65544, 131071)
        source = bytes((index * 73 + (index // 251) * 29 + 37) & 255
                       for index in range(max(lengths)))
        for length in lengths:
            data = source[:length]
            for method in wt.METHODS:
                with self.subTest(length=length, method=method):
                    expected = scalar_transform(data, method)
                    actual = wt.transform(data, method)
                    self.assertEqual(actual, expected)
                    self.assertEqual(len(actual), len(data))
                    self.assertEqual(wt.transform(expected, method, inverse=True), data)
                    if length % 8:
                        self.assertEqual(actual[-(length % 8):], data[-(length % 8):])

    def test_every_input_bit_has_the_documented_packed_position(self):
        for position in range(8):
            for bit in range(8):
                data = bytearray(8)
                data[position] = 1 << bit
                for method in wt.METHODS:
                    with self.subTest(position=position, bit=bit, method=method):
                        expected = scalar_transform(bytes(data), method)
                        self.assertEqual(wt.transform(bytes(data), method), expected)
                        self.assertEqual(wt.transform(expected, method, inverse=True), bytes(data))

    def test_inverse_accepts_independently_written_wire_fixtures(self):
        data = bytes.fromhex("8001020408102040")
        planes = bytes.fromhex("0204081020408001")
        fields = bytes.fromhex("0100002184880800")
        fixtures = (
            ("raw", data, data),
            ("bitplanes", planes, data),
            ("bitplanes_full", planes, data),
            ("fp8_fields", fields, data),
            ("fp8_fields_full", fields, data),
            ("delta_bitplanes", bytes.fromhex("5566788000000000"),
             bytes.fromhex("0103060a0f151c24")),
        )
        for method, wire, original in fixtures:
            with self.subTest(method=method):
                self.assertEqual(wt.transform(wire + b"end", method, inverse=True), original + b"end")

    def test_delta_history_restarts_at_each_block(self):
        data = bytes([211]) * 65536 + bytes([37]) * 8 + b"tail"
        first_block = bytearray(65536)
        for bit in range(8):
            first_block[bit * 8192] = (211 >> bit) & 1
        second_block = bytes((37 >> bit) & 1 for bit in range(8))
        expected = bytes(first_block) + second_block + b"tail"
        self.assertEqual(wt.transform(data, "delta_bitplanes"), expected)
        self.assertEqual(wt.transform(expected, "delta_bitplanes", inverse=True), data)

    def test_invalid_transform_input_and_declared_cap(self):
        for value in (bytearray(b"abc"), memoryview(b"abc"), "abc"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    wt.transform(value, "raw")
        with self.assertRaises(ValueError):
            wt.transform(b"abc", "unknown")
        with mock.patch.object(wt, "MAX_INPUT_SIZE", 8):
            with self.assertRaises(ValueError):
                wt.transform(bytes(9), "raw")


@unittest.skipUnless(HAVE_DEPENDENCIES, "requires numpy, zstandard, and brotli")
class WeightArchiveTests(unittest.TestCase):
    def assert_rejected(self, encoded):
        errors = (ValueError, OSError, lzma.LZMAError, zlib.error,
                  wc.zstandard.ZstdError, wc.brotli.error)
        with self.assertRaises(errors):
            wc.decode(encoded)

    def test_all_codecs_and_transforms_round_trip_small_and_empty_inputs(self):
        self.assertEqual(len(wc.CODECS), 9)
        samples = (b"", b"\0", bytes(range(256)) * 2 + b"tail123")
        for codec in wc.CODECS:
            for method in wt.METHODS:
                for data in samples:
                    with self.subTest(codec=codec, method=method, size=len(data)):
                        encoded = wc.encode(data, method, codec)
                        self.assertEqual(wc.decode(encoded), data)

    def test_independent_archive_fixture(self):
        data = bytes.fromhex("8001020408102040") + b"end"
        transformed = bytes.fromhex("0204081020408001") + b"end"
        # v1 method ID 1 is block bitplanes and codec ID 8 is DEFLATE.
        head = struct.pack("<5sBBI32s", b"AHWT\x01", 1, 8, len(data), hashlib.sha256(data).digest())
        encoded = head + zlib.compress(transformed, level=9)
        self.assertEqual(wc.decode(encoded), data)

    def test_invalid_header_ids_digest_and_declared_sizes(self):
        encoded = wc.encode(b"synthetic weights", "raw", "deflate")
        for offset, value in ((0, 0), (5, 255), (6, 255), (11, encoded[11] ^ 1)):
            edited = bytearray(encoded)
            edited[offset] = value
            with self.subTest(offset=offset):
                self.assert_rejected(bytes(edited))
        for length in (0, 1, 18, wc.MAX_BYTES + 1, 0xffffffff):
            edited = bytearray(encoded)
            struct.pack_into("<I", edited, 7, length)
            with self.subTest(length=length):
                self.assert_rejected(bytes(edited))
        for length in (0, 4, 10, wc.HEADER.size - 1, wc.HEADER.size):
            with self.subTest(truncated_length=length):
                self.assert_rejected(encoded[:length])

    def test_all_codecs_reject_truncation_trailing_data_and_corruption(self):
        for codec in wc.CODECS:
            for data in (b"", bytes(range(256)) * 4 + b"end"):
                encoded = wc.encode(data, "fp8_fields", codec)
                altered = bytes([data[0] ^ 1]) + data[1:] if data else b"\x01"
                damaged = encoded[:wc.HEADER.size] + wc.compress_payload(
                    wt.transform(altered, "fp8_fields"), codec
                )
                variants = (encoded[:-1], encoded + b"x",
                            encoded + encoded[wc.HEADER.size:], damaged)
                for index, candidate in enumerate(variants):
                    with self.subTest(codec=codec, size=len(data), damage=index):
                        self.assert_rejected(candidate)

    def test_all_codecs_reject_output_larger_than_declared(self):
        for codec in wc.CODECS:
            encoded = bytearray(wc.encode(bytes(range(64)), "raw", codec))
            for length in (0, 1, 63, 65):
                struct.pack_into("<I", encoded, 7, length)
                with self.subTest(codec=codec, length=length):
                    self.assert_rejected(bytes(encoded))

    def test_encode_rejects_unknown_options_and_size_limit(self):
        for window in (22, 24):
            self.assertEqual(wc.decode(wc.encode(b"weights" * 64, codec="brotli", brotli_window=window)),
                             b"weights" * 64)
        with self.assertRaises(ValueError):
            wc.encode(b"data", codec="brotli", brotli_window=25)
        with self.assertRaises(ValueError):
            wc.encode(b"data", codec="xz9", brotli_window=24)
        with self.assertRaises(ValueError):
            wc.encode(b"data", "unknown", "deflate")
        with self.assertRaises(ValueError):
            wc.encode(b"data", "raw", "unknown")
        with mock.patch.object(wc, "MAX_BYTES", 8):
            with self.assertRaises(ValueError):
                wc.encode(bytes(9), "raw", "deflate")


if __name__ == "__main__":
    unittest.main()
