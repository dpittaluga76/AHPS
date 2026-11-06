#!/usr/bin/env python3
"""Decoder resource and startup contract tests; all payloads are small.

Run with: python -m unittest test_decode_limits
The archive fixtures follow docs/format.md independently of the encoder.
"""

import contextlib
import dataclasses
import lzma
import struct
import unittest
import zlib
from unittest import mock

import ahps


VERSIONS = (b"AHPS\x03", b"AHPS\x04", b"AHPS\x05")
DATA = b"small decoder limit fixture"


def raw_container(magic: bytes, data: bytes) -> bytes:
    """A valid RAW archive, including the distinct empty v3 representation."""
    if magic == b"AHPS\x03":
        count = int(bool(data))
        header = magic + struct.pack("<III", count, max(1, len(data)), 0)
        return header + bytes(count) + data
    table = struct.pack("<IB", len(data), 0) if data else b""
    header = magic + struct.pack("<IIQI", int(bool(data)), len(table), len(data), zlib.crc32(data))
    return header + table + data


def wrap(container: bytes, dictionary: int = 4096) -> bytes:
    return lzma.compress(
        container,
        format=lzma.FORMAT_XZ,
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": dictionary}],
    )


def limits(**overrides):
    # Decoder memory includes more than its dictionary; 2 MiB accommodates
    # these 4 KiB dictionaries while leaving every expansion cap tiny.
    values = {
        "max_dict_bytes": 2 * 1024 * 1024,
        "max_container_bytes": 4096,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return ahps.DecodeLimits(**values)


@contextlib.contextmanager
def record_decoded_bytes():
    """Observe real expanded bytes without assuming a decoder call sequence."""
    original_decoder = lzma.LZMADecompressor
    returned_lengths = []

    class RecordingDecoder:
        def __init__(self, *args, **kwargs):
            self.decoder = original_decoder(*args, **kwargs)

        def decompress(self, *args, **kwargs):
            result = self.decoder.decompress(*args, **kwargs)
            returned_lengths.append(len(result))
            return result

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    with mock.patch.object(ahps.lzma, "LZMADecompressor", RecordingDecoder):
        yield returned_lengths


class DecodeLimitContractTests(unittest.TestCase):
    def test_defaults_and_frozen_configuration(self):
        configured = ahps.DecodeLimits()
        self.assertEqual(configured.max_dict_bytes, 256 * 1024 * 1024)
        self.assertEqual(configured.max_container_bytes, 4 * 1024**3 + 1024**2)
        self.assertEqual(configured.max_output_bytes, 4 * 1024**3)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            configured.max_output_bytes = 1
        self.assertTrue(issubclass(ahps.DecodeLimitError, ValueError))

    def test_all_versions_decode_at_exact_container_and_output_caps(self):
        for magic in VERSIONS:
            raw = raw_container(magic, DATA)
            configured = limits(max_container_bytes=len(raw), max_output_bytes=len(DATA))
            for force_python in (False, True):
                with self.subTest(version=magic, force_python=force_python):
                    self.assertEqual(
                        ahps.ahps_decompress_bytes(wrap(raw), force_python, limits=configured), DATA
                    )

    def test_one_byte_over_container_cap_is_a_limit_error(self):
        for magic in VERSIONS:
            raw = raw_container(magic, DATA)
            for force_python in (False, True):
                with self.subTest(version=magic, force_python=force_python):
                    with self.assertRaises(ahps.DecodeLimitError):
                        ahps.ahps_decompress_bytes(
                            wrap(raw), force_python, limits=limits(max_container_bytes=len(raw) - 1)
                        )

    def test_one_byte_over_output_cap_is_a_limit_error(self):
        for magic in VERSIONS:
            for force_python in (False, True):
                with self.subTest(version=magic, force_python=force_python):
                    with self.assertRaises(ahps.DecodeLimitError):
                        ahps.ahps_decompress_bytes(
                            wrap(raw_container(magic, DATA)),
                            force_python,
                            limits=limits(max_output_bytes=len(DATA) - 1),
                        )

    def test_zero_output_cap_allows_empty_archives(self):
        for magic in VERSIONS:
            raw = raw_container(magic, b"")
            for force_python in (False, True):
                with self.subTest(version=magic, force_python=force_python):
                    self.assertEqual(
                        ahps.ahps_decompress_bytes(
                            wrap(raw),
                            force_python,
                            limits=limits(max_container_bytes=len(raw), max_output_bytes=0),
                        ),
                        b"",
                    )

    def test_zero_output_cap_rejects_one_byte(self):
        for magic in VERSIONS:
            with self.subTest(version=magic):
                with self.assertRaises(ahps.DecodeLimitError):
                    ahps.ahps_decompress_bytes(
                        wrap(raw_container(magic, b"x")), limits=limits(max_output_bytes=0)
                    )

    def test_dictionary_requirement_is_a_limit_error_not_corruption(self):
        archive = wrap(raw_container(b"AHPS\x05", DATA), dictionary=1024 * 1024)
        with self.assertRaises(ahps.DecodeLimitError):
            ahps.ahps_decompress_bytes(archive, limits=limits(max_dict_bytes=16 * 1024))
        self.assertEqual(ahps.ahps_decompress_bytes(archive, limits=limits()), DATA)

    def test_dictionary_budget_does_not_replace_expansion_budget(self):
        raw = raw_container(b"AHPS\x05", bytes(8192))
        archive = wrap(raw)
        with self.assertRaises(ahps.DecodeLimitError):
            ahps.ahps_decompress_bytes(
                archive, limits=limits(max_container_bytes=1024, max_output_bytes=8192)
            )
        self.assertEqual(
            ahps.ahps_decompress_bytes(
                archive, limits=limits(max_container_bytes=len(raw), max_output_bytes=8192)
            ),
            bytes(8192),
        )

    def test_partial_header_cannot_bypass_container_cap(self):
        # A recognized version, but not enough bytes to read its length fields.
        for magic in VERSIONS:
            partial = magic + bytes(5)
            with self.subTest(version=magic):
                with self.assertRaises(ahps.DecodeLimitError):
                    ahps.ahps_decompress_bytes(
                        wrap(partial), limits=limits(max_container_bytes=len(partial) - 1)
                    )

    def test_declared_output_limit_stops_expansion_at_header(self):
        # The complete container is valid, but its body must never be inflated
        # once the 25-byte header declares an output beyond the configured cap.
        data = bytes(128 * 1024)
        for magic in (b"AHPS\x04", b"AHPS\x05"):
            raw = raw_container(magic, data)
            archive = wrap(raw)
            with self.subTest(version=magic):
                with record_decoded_bytes() as returned_lengths:
                    with self.assertRaises(ahps.DecodeLimitError):
                        ahps.ahps_decompress_bytes(
                            archive,
                            limits=limits(max_container_bytes=len(raw), max_output_bytes=16),
                        )
                expanded = sum(returned_lengths)
                self.assertGreater(expanded, 0)
                self.assertLessEqual(expanded, 25, "expanded the body before checking its declared size")

    def test_container_cap_bounds_actual_expansion_to_one_extra_byte(self):
        data = bytes(128 * 1024)
        for magic in VERSIONS:
            archive = wrap(raw_container(magic, data))
            for cap in (0, 8, 64):
                with self.subTest(version=magic, container_cap=cap):
                    with record_decoded_bytes() as returned_lengths:
                        with self.assertRaises(ahps.DecodeLimitError):
                            ahps.ahps_decompress_bytes(
                                archive,
                                limits=limits(max_container_bytes=cap, max_output_bytes=len(data)),
                            )
                    expanded = sum(returned_lengths)
                    self.assertLessEqual(
                        expanded, cap + 1, "expanded beyond the budget and one excess-detection byte"
                    )

    def test_declared_output_limit_is_checked_before_any_lift(self):
        # The huge declared length is deliberately not backed by a huge payload.
        # This must be rejected from the header, never passed to a native lift.
        table = struct.pack("<IB", 1, 1) + b"\x02\x01\x00\x00\x00"
        for magic in (b"AHPS\x04", b"AHPS\x05"):
            raw = magic + struct.pack("<IIQI", 1, len(table), 1 << 62, 0) + table + b"x"
            for force_python in (False, True):
                with self.subTest(version=magic, force_python=force_python):
                    with (
                        mock.patch.object(ahps.SIMD_BRIDGE, "lift", side_effect=AssertionError("native lift reached")),
                        mock.patch.object(ahps.SIMD_BRIDGE, "lift_into", side_effect=AssertionError("native lift reached")),
                        mock.patch.object(ahps, "py_inverse_lift", side_effect=AssertionError("Python lift reached")),
                    ):
                        with self.assertRaises(ahps.DecodeLimitError):
                            ahps.ahps_decompress_bytes(
                                wrap(raw), force_python, limits=limits(max_output_bytes=16)
                            )

    def test_v3_derived_output_limit_precedes_native_or_python_decode(self):
        # v3 has no TotalLen field: its output length is the residual length.
        raw = raw_container(b"AHPS\x03", DATA)
        for force_python in (False, True):
            with self.subTest(force_python=force_python):
                with (
                    mock.patch.object(ahps.SIMD_BRIDGE, "unlift_all", side_effect=AssertionError("native decode reached")),
                    mock.patch.object(ahps, "py_inverse_lift", side_effect=AssertionError("Python decode reached")),
                ):
                    with self.assertRaises(ahps.DecodeLimitError):
                        ahps.ahps_decompress_bytes(
                            wrap(raw), force_python, limits=limits(max_output_bytes=len(DATA) - 1)
                        )

    def test_invalid_limit_values_fail_before_reading_input(self):
        bad_values = (-1, True, 1.5, "16", None)
        for field in ("max_dict_bytes", "max_container_bytes", "max_output_bytes"):
            for value in bad_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises((TypeError, ValueError)):
                        configured = limits(**{field: value})
                        ahps.ahps_decompress_bytes(b"not an XZ stream", limits=configured)

    def test_zero_dictionary_budget_is_invalid(self):
        with self.assertRaises((TypeError, ValueError)):
            configured = limits(max_dict_bytes=0)
            ahps.ahps_decompress_bytes(b"not an XZ stream", limits=configured)

    def test_limits_argument_must_be_decode_limits(self):
        for invalid in (None, {}, 1, "default"):
            with self.subTest(limits=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    ahps.ahps_decompress_bytes(b"not an XZ stream", limits=invalid)


class StrictOuterStreamTests(unittest.TestCase):
    def assert_corrupt(self, archive):
        for force_python in (False, True):
            with self.subTest(force_python=force_python):
                with self.assertRaises((ValueError, lzma.LZMAError)) as caught:
                    ahps.ahps_decompress_bytes(archive, force_python, limits=limits())
                self.assertNotIsInstance(caught.exception, ahps.DecodeLimitError)

    def test_truncated_streams_are_corrupt(self):
        archive = wrap(raw_container(b"AHPS\x05", DATA))
        for length in (0, 1, 12, len(archive) // 2, len(archive) - 1):
            with self.subTest(length=length):
                self.assert_corrupt(archive[:length])

    def test_trailing_bytes_are_rejected_including_xz_padding(self):
        for magic in VERSIONS:
            archive = wrap(raw_container(magic, DATA))
            for suffix in (b"x", b"\x00", bytes(4), b"unrelated trailing bytes"):
                with self.subTest(version=magic, suffix=suffix):
                    self.assert_corrupt(archive + suffix)

    def test_concatenated_streams_are_rejected(self):
        for magic in VERSIONS:
            archive = wrap(raw_container(magic, DATA))
            # An empty second XZ stream must not be silently accepted either.
            for second in (archive, wrap(b"")):
                with self.subTest(version=magic, second_length=len(second)):
                    self.assert_corrupt(archive + second)


class NativeStartupTests(unittest.TestCase):
    def test_incompatible_abi_is_never_initialized(self):
        for version in (205, 207):
            library = mock.Mock()
            library.ahps_abi_version.return_value = version
            with (
                self.subTest(version=version),
                mock.patch.object(ahps, "cpu_supports_native", return_value=True),
                mock.patch.object(ahps.pathlib.Path, "exists", return_value=True),
                mock.patch.object(ahps.ctypes, "CDLL", return_value=library),
            ):
                bridge = ahps.NativeSIMDBridge()
                self.assertIsNone(bridge.lib)
                library.ahps_init_tables.assert_not_called()

    def test_new_writer_uses_v5_and_current_native_identity(self):
        archive, _ = ahps.ahps_compress_bytes(DATA, lzma_preset=0)
        self.assertEqual(lzma.decompress(archive)[:5], b"AHPS\x05")
        self.assertEqual(ahps.EXPECTED_ABI_VERSION, 206)
        if ahps.SIMD_BRIDGE.lib is not None:
            self.assertEqual(ahps.SIMD_BRIDGE.lib.ahps_abi_version(), 206)

    def test_unsupported_cpu_skips_dll_and_still_decodes(self):
        with (
            mock.patch.object(ahps, "cpu_supports_native", return_value=False) as supports_native,
            mock.patch.object(ahps.pathlib.Path, "exists", return_value=True),
            mock.patch.object(ahps.ctypes, "CDLL") as load_library,
        ):
            bridge = ahps.NativeSIMDBridge()
            supports_native.assert_called()
            load_library.assert_not_called()
            self.assertIsNone(bridge.lib)
            with mock.patch.object(ahps, "SIMD_BRIDGE", bridge):
                self.assertEqual(
                    ahps.ahps_decompress_bytes(
                        wrap(raw_container(b"AHPS\x05", DATA)), force_python=False, limits=limits()
                    ),
                    DATA,
                )
            load_library.assert_not_called()


if __name__ == "__main__":
    unittest.main()
