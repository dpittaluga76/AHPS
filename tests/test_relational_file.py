"""Independent file framing, tail preservation and rejection checks for AHRL."""

import lzma
import random
import struct
import unittest
from unittest import mock
import zlib

from research import relational, relational_file


MAGIC = b"AHRF\x00\x00\x00\x01"
HEADER = struct.Struct("<8sBBBBIQII")
FRAME = struct.Struct("<II")
INNER_HEADER = struct.Struct("<8sBBBBQI")
MODEL = struct.Struct("<BBQQ")


def inner_raw(data, width=1, fields=1, **changes):
    values = dict(magic=b"AHRL\x00\x00\x00\x01", width=width, fields=fields,
                  mode=0, reserved=0, size=len(data), crc=zlib.crc32(data))
    values.update(changes)
    body = INNER_HEADER.pack(*(values[key] for key in
                              ("magic", "width", "fields", "mode", "reserved", "size", "crc")))
    return lzma.compress(body + data, format=lzma.FORMAT_XZ,
                         check=lzma.CHECK_CRC64, preset=0)


def outer(original, width=1, fields=1, chunk_bytes=4, frames=None, **changes):
    if frames is None:
        frames = []
        record_bytes = width * fields
        for offset in range(0, len(original), chunk_bytes):
            part = original[offset:offset + chunk_bytes]
            padded = part + bytes(-len(part) % record_bytes)
            archive = inner_raw(padded, width, fields)
            frames.append(FRAME.pack(len(part), len(archive)) + archive)
    values = dict(magic=MAGIC, width=width, fields=fields, reserved_a=0,
                  reserved_b=0, chunk_bytes=chunk_bytes, size=len(original),
                  count=len(frames), crc=zlib.crc32(original))
    values.update(changes)
    return HEADER.pack(*(values[key] for key in
                         ("magic", "width", "fields", "reserved_a", "reserved_b",
                          "chunk_bytes", "size", "count", "crc"))) + b"".join(frames)


def frame(original_count, archive, stored_count=None):
    return FRAME.pack(original_count, len(archive) if stored_count is None else stored_count) + archive


class RelationalFileRoundTripTests(unittest.TestCase):
    def test_every_width_and_field_boundary_preserves_all_original_bytes(self):
        rng = random.Random(1441)
        for width in (1, 2, 4, 8):
            for fields in (1, 3, 16):
                stride = width * fields
                chunk_bytes = 2 * stride
                lengths = sorted({0, 1, stride - 1, stride, chunk_bytes - 1,
                                  chunk_bytes, chunk_bytes + 1, 3 * stride + 1})
                for length in lengths:
                    source = rng.randbytes(length)
                    for method in ("raw", "auto"):
                        with self.subTest(width=width, fields=fields, length=length, method=method):
                            archive = relational_file.encode(source, method=method, width=width,
                                                             fields=fields, chunk_bytes=chunk_bytes,
                                                             preset=0)
                            self.assertEqual(relational_file.decode(archive), source)

    def test_raw_encoder_matches_independent_framing_and_only_pads_final_record(self):
        source = bytes(range(29))
        expected = outer(source, width=2, fields=3, chunk_bytes=12)
        actual = relational_file.encode(source, method="raw", width=2, fields=3,
                                        chunk_bytes=12, preset=0)
        self.assertEqual(actual, expected)
        self.assertEqual(relational_file.decode(expected), source)

    def test_empty_file_is_header_only_with_no_frame(self):
        expected = outer(b"", width=4, fields=3, chunk_bytes=24)
        for method in ("raw", "auto"):
            archive = relational_file.encode(b"", method=method, width=4, fields=3,
                                             chunk_bytes=24, preset=0)
            self.assertEqual(archive, expected)
            self.assertEqual(len(archive), HEADER.size)
            self.assertEqual(relational_file.decode(archive, max_output_bytes=0), b"")

    def test_crafted_causal_model_and_raw_tail_restore_without_search(self):
        first = bytes((255, 2, 0, 255))
        body = (INNER_HEADER.pack(b"AHRL\x00\x00\x00\x01", 1, 2, 2, 0, 4, zlib.crc32(first))
                + MODEL.pack(1, 0, 0, 0) + MODEL.pack(2, 0, 3, 5)
                + bytes((255, 1, 0, 250)))
        archive = lzma.compress(body, check=lzma.CHECK_CRC64, preset=0)
        original = first + b"\x07"
        wrapped = outer(original, width=1, fields=2, chunk_bytes=4,
                        frames=[frame(4, archive), frame(1, inner_raw(b"\x07\x00", 1, 2))])
        with mock.patch.object(relational, "encode", side_effect=AssertionError("encoder called")), \
                mock.patch.object(relational, "_adaptive_models", side_effect=AssertionError("search called")):
            self.assertEqual(relational_file.decode(wrapped), original)

    def test_auto_never_exceeds_matched_raw_complete_size(self):
        rng = random.Random(991)
        sources = (bytes(range(53)), rng.randbytes(91), bytes(157),
                   b"".join(struct.pack("<HH", x, 3 * x + 7) for x in range(40)))
        for source in sources:
            raw = relational_file.encode(source, method="raw", width=2, fields=2,
                                          chunk_bytes=64, preset=0)
            auto = relational_file.encode(source, method="auto", width=2, fields=2,
                                           chunk_bytes=64, preset=0)
            with self.subTest(length=len(source)):
                self.assertLessEqual(len(auto), len(raw))
                self.assertEqual(relational_file.decode(auto), source)
                self.assertEqual(auto, relational_file.encode(
                    source, method="auto", width=2, fields=2, chunk_bytes=64, preset=0))

    def test_inspection_reports_validated_frames_and_padding(self):
        original = bytes(range(13))
        archive = outer(original, width=2, fields=2, chunk_bytes=8)
        info = relational_file.inspect_archive(archive)
        self.assertEqual((info["width"], info["fields"], info["chunk_bytes"],
                          info["source_bytes"], info["frame_count"]), (2, 2, 8, 13, 2))
        self.assertEqual(info["archive_bytes"], len(archive))
        self.assertEqual([entry["original_bytes"] for entry in info["frames"]], [8, 5])
        self.assertEqual([entry["padded_bytes"] for entry in info["frames"]], [8, 8])
        self.assertEqual([entry["padding_bytes"] for entry in info["frames"]], [0, 3])
        self.assertEqual([entry["metadata"]["storage_mode"] for entry in info["frames"]], [0, 0])
        with self.assertRaises(ValueError):
            relational_file.inspect_archive(archive + b"\x00")


class RelationalFileRejectionTests(unittest.TestCase):
    def test_invalid_encode_options_even_when_empty(self):
        invalid = (dict(width=3), dict(width=True), dict(fields=0), dict(fields=17),
                   dict(fields=1.0), dict(chunk_bytes=0), dict(chunk_bytes=-1),
                   dict(chunk_bytes=5), dict(chunk_bytes=8 * 1024**2 + 4),
                   dict(chunk_bytes=True), dict(method="delta"), dict(method="unknown"),
                   dict(preset=True), dict(preset=-1), dict(preset=10))
        for changes in invalid:
            args = dict(method="raw", width=2, fields=2, chunk_bytes=8, preset=0)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                relational_file.encode(b"", **args)

    def test_excessive_frame_count_is_rejected_before_any_encode(self):
        with mock.patch.object(relational, "encode", side_effect=AssertionError("encode started")):
            with self.assertRaises(ValueError):
                relational_file.encode(bytes(4097), method="raw", width=1, fields=1,
                                        chunk_bytes=1, preset=0)

    def test_source_and_archive_caps_apply_before_frame_work(self):
        # Exercise real limit paths with small fixtures instead of allocating
        # 129/161 MiB solely to prove the initial size guard while the PC is used.
        self.assertEqual(relational_file.MAX_OUTPUT_BYTES, 128 * 1024**2)
        self.assertEqual(relational_file.MAX_ARCHIVE_BYTES, 160 * 1024**2)
        with mock.patch.object(relational_file, "MAX_OUTPUT_BYTES", 8), \
                mock.patch.object(relational, "encode", side_effect=AssertionError("encoder called")):
            with self.assertRaises(ValueError):
                relational_file.encode(bytes(9), method="raw", width=1, fields=1,
                                        chunk_bytes=4, preset=0)
        archive = outer(b"abcd")
        with mock.patch.object(relational_file, "MAX_ARCHIVE_BYTES", len(archive) - 1), \
                mock.patch.object(relational, "_read_container", side_effect=AssertionError("inner read")):
            with self.assertRaises(ValueError):
                relational_file.decode(archive)
            with self.assertRaises(ValueError):
                relational_file.inspect_archive(archive)

    def test_bad_outer_headers_are_rejected_before_reading_inner_container(self):
        valid = outer(b"abcd")
        values = list(HEADER.unpack_from(valid))
        changes = ((0, b"AHPS\x05xxx"), (1, 3), (2, 0), (2, 17), (3, 1), (4, 1),
                   (5, 0), (5, 8 * 1024**2 + 1), (6, 2**63), (6, 128 * 1024**2 + 1),
                   (7, 0), (7, 2), (7, 4097))
        for index, value in changes:
            changed = values.copy()
            changed[index] = value
            malformed = HEADER.pack(*changed) + valid[HEADER.size:]
            with self.subTest(index=index, value=value), \
                    mock.patch.object(relational, "_read_container", side_effect=AssertionError("inner read")):
                with self.assertRaises(ValueError):
                    relational_file.decode(malformed)
        malformed = bytearray(outer(b"abcd", width=2, fields=2, chunk_bytes=4))
        struct.pack_into("<I", malformed, 12, 6)  # chunk size not a whole record
        with self.assertRaises(ValueError):
            relational_file.decode(malformed)

    def test_output_limit_inclusive_and_invalid_limit_types(self):
        archive = outer(b"abcdefgh")
        self.assertEqual(relational_file.decode(archive, max_output_bytes=8), b"abcdefgh")
        with mock.patch.object(relational, "_read_container", side_effect=AssertionError("inner read")):
            with self.assertRaises(ValueError):
                relational_file.decode(archive, max_output_bytes=7)
        for limit in (-1, True, 0.5):
            with self.subTest(limit=limit), self.assertRaises((ValueError, TypeError)):
                relational_file.decode(archive, max_output_bytes=limit)

    def test_missing_extra_truncated_frames_and_trailing_bytes(self):
        valid = outer(b"abcde")
        invalid = (b"", valid[:HEADER.size - 1], valid[:HEADER.size],
                   valid[:HEADER.size + FRAME.size - 1], valid[:-1],
                   valid + b"x", valid + valid[HEADER.size:])
        for archive in invalid:
            with self.subTest(length=len(archive)), self.assertRaises(ValueError):
                relational_file.decode(archive)

    def test_frame_lengths_must_be_canonical(self):
        inner = inner_raw(b"abcd")
        for original_count, stored_count in ((0, len(inner)), (3, len(inner)),
                                              (5, len(inner)), (4, 0),
                                              (4, len(inner) - 1), (4, len(inner) + 1),
                                              (4, 9 * 1024**2 + 1)):
            malformed = outer(b"abcd", frames=[frame(original_count, inner, stored_count)])
            with self.subTest(original_count=original_count, stored_count=stored_count), \
                    self.assertRaises(ValueError):
                relational_file.decode(malformed)
        malformed = outer(b"abcde", frames=[frame(4, inner), frame(2, inner_raw(b"e\0"))])
        with self.assertRaises(ValueError):
            relational_file.decode(malformed)

    def test_valid_inner_crc_does_not_allow_nonzero_padding(self):
        inner = inner_raw(b"abc\x01", width=2, fields=2)
        malformed = outer(b"abc", width=2, fields=2, chunk_bytes=4,
                          frames=[frame(3, inner)])
        with self.assertRaises(ValueError):
            relational_file.decode(malformed)

    def test_inner_layout_and_padded_length_must_match_wrapper(self):
        cases = (
            outer(b"ab", width=1, fields=2, chunk_bytes=2,
                  frames=[frame(2, inner_raw(b"ab", width=2, fields=1))]),
            outer(b"abc", width=2, fields=1, chunk_bytes=4,
                  frames=[frame(3, inner_raw(b"ab", width=2, fields=1))]),
            outer(b"abc", width=2, fields=1, chunk_bytes=4,
                  frames=[frame(3, inner_raw(b"abc\0\0\0", width=2, fields=1))]),
        )
        for archive in cases:
            with self.subTest(length=len(archive)), self.assertRaises(ValueError):
                relational_file.decode(archive)

    def test_outer_and_inner_checksums_are_both_enforced(self):
        cases = (
            outer(b"abcd", crc=zlib.crc32(b"abcd") ^ 1),
            outer(b"abcd", frames=[frame(4, inner_raw(b"abcd", crc=0))]),
        )
        for archive in cases:
            with self.assertRaises(ValueError):
                relational_file.decode(archive)
            with self.assertRaises(ValueError):
                relational_file.inspect_archive(archive)

    def test_inner_trailing_concatenated_and_corrupt_xz_are_rejected(self):
        inner = inner_raw(b"abcd")
        corrupted = bytearray(inner)
        corrupted[len(corrupted) // 2] ^= 128
        for archive in (inner[:-1], inner + b"x", inner + inner, bytes(corrupted)):
            malformed = outer(b"abcd", frames=[frame(4, archive)])
            with self.subTest(length=len(archive)), self.assertRaises(ValueError):
                relational_file.decode(malformed)

    def test_actual_inner_expansion_is_bounded_by_expected_frame_size(self):
        # A small outer frame contains a compressed MiB while claiming one byte.
        inner = inner_raw(bytes(1024**2), size=1)
        archive = outer(b"\0", frames=[frame(1, inner)])
        decoder_class = lzma.LZMADecompressor
        returned, requests = [], []

        class ObservedDecoder:
            def __init__(self, *args, **kwargs):
                self.inner = decoder_class(*args, **kwargs)

            def decompress(self, data, max_length=-1):
                requests.append(max_length)
                output = self.inner.decompress(data, max_length=max_length)
                returned.append(len(output))
                return output

            def __getattr__(self, key):
                return getattr(self.inner, key)

        with mock.patch.object(relational.lzma, "LZMADecompressor", ObservedDecoder):
            with self.assertRaises(ValueError):
                relational_file.decode(archive)
        cap = 1 + INNER_HEADER.size + 16 * MODEL.size + 1
        self.assertTrue(requests)
        self.assertTrue(all(0 < request <= cap for request in requests))
        self.assertLessEqual(sum(returned), cap)


if __name__ == "__main__":
    unittest.main()
