"""Independent wire vectors and bounded-decoding checks for the research codec."""

import lzma
import random
import struct
import unittest
from unittest import mock
import zlib

from research import relational


MAGIC = b"AHRL\x00\x00\x00\x01"
HEADER = struct.Struct("<8sBBBBQI")
MODEL = struct.Struct("<BBQQ")
METHODS = ("raw", "shuffle", "delta", "independent", "cross", "adaptive", "auto")


def words(records, width):
    return b"".join(value.to_bytes(width, "little") for row in records for value in row)


def shuffle(data, stride):
    return b"".join(data[position::stride] for position in range(stride))


def container(original, width, fields, mode=0, models=(), payload=None, **changes):
    values = dict(magic=MAGIC, width=width, fields=fields, mode=mode, reserved=0,
                  size=len(original), crc=zlib.crc32(original))
    values.update(changes)
    header = HEADER.pack(*(values[key] for key in
                           ("magic", "width", "fields", "mode", "reserved", "size", "crc")))
    return header + b"".join(MODEL.pack(*model) for model in models) + (
        original if payload is None else payload)


def wrap(body, check=lzma.CHECK_CRC64):
    return lzma.compress(body, format=lzma.FORMAT_XZ, check=check, preset=0)


def wire_models(archive):
    body = lzma.decompress(archive)
    _, _, fields, mode, _, _, _ = HEADER.unpack_from(body)
    if mode != 2:
        return []
    return [MODEL.unpack_from(body, HEADER.size + field * MODEL.size)
            for field in range(fields)]


class RelationalRoundTripTests(unittest.TestCase):
    def test_all_methods_restore_word_boundaries_and_arbitrary_patterns(self):
        rng = random.Random(28171)
        for width in (1, 2, 4, 8):
            mask = (1 << (8 * width)) - 1
            for fields in (1, 3):
                rows = [[(0, mask, 1, mask - 1)[(i + j) % 4]
                         for j in range(fields)] for i in range(4)]
                rows += [[rng.getrandbits(8 * width) for _ in range(fields)]
                         for _ in range(9)]
                data = words(rows, width)
                for method in METHODS:
                    with self.subTest(width=width, fields=fields, method=method):
                        archive = relational.encode(data, width, fields, method, preset=0)
                        self.assertEqual(relational.decode(archive), data)

    def test_empty_single_and_maximum_field_count(self):
        for data, width, fields in ((b"", 8, 16), (b"\xff", 1, 1),
                                    (bytes(range(32)), 2, 16)):
            for method in METHODS:
                with self.subTest(size=len(data), width=width, fields=fields, method=method):
                    archive = relational.encode(data, width, fields, method, preset=0)
                    self.assertEqual(relational.decode(archive, max_output_bytes=len(data)), data)

    def test_bit_patterns_for_float_storage_are_preserved(self):
        # Signed zeros, infinities, signaling/quiet NaNs and noncanonical payloads.
        data = words(((0, 0x80000000, 0x7F800000),
                      (0xFF800000, 0x7FC00001, 0x7F800001),
                      (0xFFFFFFFF, 0x00000001, 0x00800000)), 4)
        for method in METHODS:
            with self.subTest(method=method):
                self.assertEqual(relational.decode(
                    relational.encode(data, 4, 3, method, preset=0)), data)

    def test_shuffle_encoder_has_known_byte_lane_order(self):
        data = bytes.fromhex("01020304 11121314 21222324")
        archive = relational.encode(data, 2, 2, "shuffle", preset=0)
        expected = container(data, 2, 2, mode=1,
                             payload=bytes.fromhex("011121 021222 031323 041424"))
        self.assertEqual(lzma.decompress(archive), expected)

    def test_delta_encoder_has_known_wraparound_residuals(self):
        data = words(((65535, 0), (0, 65535), (2, 1)), 2)
        residual = words(((65535, 0), (1, 65535), (2, 2)), 2)
        expected = container(data, 2, 2, mode=2,
                             models=((1, 0, 0, 0), (1, 0, 0, 0)),
                             payload=shuffle(residual, 4))
        self.assertEqual(lzma.decompress(relational.encode(data, 2, 2, "delta", preset=0)),
                         expected)

    def test_cross_encoder_uses_current_preceding_field_and_previous_first(self):
        data = words(((255, 0, 255), (0, 255, 1), (2, 1, 0)), 1)
        residual = bytes((255, 1, 255, 1, 255, 2, 2, 255, 255))
        expected = container(data, 1, 3, mode=2,
                             models=((1, 0, 0, 0), (2, 0, 1, 0), (2, 1, 1, 0)),
                             payload=shuffle(residual, 3))
        self.assertEqual(lzma.decompress(relational.encode(data, 1, 3, "cross", preset=0)),
                         expected)

    def test_adaptive_finds_a_slope_outside_fixed_list_and_is_deterministic(self):
        rng = random.Random(463)
        rows = []
        for _ in range(128):
            x = rng.getrandbits(16)
            rows.append((x, (5 * x + 17) & 65535))
        data = words(rows, 2)
        first = relational.encode(data, 2, 2, "adaptive", preset=0)
        second = relational.encode(data, 2, 2, "adaptive", preset=0)
        self.assertEqual(first, second)
        self.assertEqual(wire_models(first)[1], (2, 0, 5, 17))
        self.assertEqual(relational.decode(first), data)

    def test_adaptive_scores_following_records_instead_of_fitting_them(self):
        rng = random.Random(744)
        fit = []
        for _ in range(1024):
            x = rng.getrandbits(16)
            fit.append((x, (3 * x + 7) & 65535))
        score = [(rng.getrandbits(16), 42) for _ in range(1024)]
        fit_archive = relational.encode(words(fit, 2), 2, 2, "adaptive", preset=0)
        self.assertEqual(wire_models(fit_archive)[1], (2, 0, 3, 7))
        data = words(fit + score, 2)
        archive = relational.encode(data, 2, 2, "adaptive", preset=0)
        # A fit-only score would retain the affine relation. The following
        # records favor raw constant values, whose byte entropy is zero.
        self.assertEqual(wire_models(archive)[1], (0, 0, 0, 0))
        self.assertEqual(relational.decode(archive), data)

    def test_auto_is_exact_minimum_complete_archive_with_stable_ties(self):
        data = words(((i * 19 & 65535, (3 * i * 19 + 7) & 65535)
                      for i in range(80)), 2)
        choices = [relational.encode(data, 2, 2, method, preset=0)
                   for method in METHODS[:-1]]
        selected = relational.encode(data, 2, 2, "auto", preset=0)
        self.assertEqual(selected, min(choices, key=len))
        self.assertLessEqual(len(selected), len(choices[0]))

    def test_independent_control_never_uses_nonconstant_cross_field_model(self):
        rng = random.Random(912)
        rows = []
        for _ in range(128):
            x = rng.getrandbits(16)
            rows.append((x, (3 * x + 7) & 65535, 42))
        data = words(rows, 2)
        archive = relational.encode(data, 2, 3, "independent", preset=0)
        self.assertEqual(relational.decode(archive), data)
        self.assertTrue(all(kind != 2 or coefficient == 0
                            for kind, _, coefficient, _ in wire_models(archive)))

    def test_invalid_encode_dimensions_lengths_and_method(self):
        invalid = ((b"123", 2, 1, "raw"), (b"1234", 2, 3, "delta"),
                   (b"", 3, 1, "raw"), (b"", 0, 1, "raw"),
                   (b"", 1, 0, "raw"), (b"", 1, 17, "raw"),
                   (b"", 1, 1, "unknown"), (b"", True, 1, "raw"),
                   (b"", 1, 1.0, "raw"))
        for args in invalid:
            with self.subTest(args=args), self.assertRaises((TypeError, ValueError)):
                relational.encode(*args, preset=0)

    def test_encode_rejects_more_than_documented_source_cap(self):
        with self.assertRaises(ValueError):
            relational.encode(bytes(8 * 1024**2 + 1), 1, 1, "raw", preset=0)


class RelationalIndependentWireTests(unittest.TestCase):
    def test_crafted_modular_affine_vector(self):
        original = words(((255, 2), (0, 255), (2, 1)), 1)
        body = container(original, 1, 2, mode=2,
                         models=((1, 0, 0, 0), (2, 0, 3, 5)),
                         payload=bytes((255, 1, 2, 0, 250, 246)))
        self.assertEqual(relational.decode(wrap(body)), original)

    def test_even_coefficients_need_no_modular_inverse_to_decode(self):
        for width in (1, 2, 4, 8):
            mask = (1 << (8 * width)) - 1
            residuals = ((mask, 0, 1), (1, mask, 2), (mask, 7, mask))
            models = ((0, 0, 0, 0), (1, 0, 0, 0), (2, 0, 2, mask))
            rows, previous = [], 0
            for raw, delta, error in residuals:
                previous = (previous + delta) & mask
                rows.append((raw, previous, (2 * raw + mask + error) & mask))
            original = words(rows, width)
            body = container(original, width, 3, mode=2, models=models,
                             payload=shuffle(words(residuals, width), 3 * width))
            with self.subTest(width=width):
                self.assertEqual(relational.decode(wrap(body)), original)

    def test_decode_has_no_dependency_on_encoder_search(self):
        original = bytes((7, 21, 9, 27))
        body = container(original, 1, 2, mode=2,
                         models=((0, 0, 0, 0), (2, 0, 3, 0)),
                         payload=bytes((7, 9, 0, 0)))
        with mock.patch.object(relational, "encode", side_effect=AssertionError("encoder used")), \
                mock.patch.object(relational, "_adaptive_models",
                                  side_effect=AssertionError("model search used")):
            self.assertEqual(relational.decode(wrap(body)), original)

    def test_inspection_exposes_validated_layout_and_models(self):
        original = bytes((7, 21, 9, 27))
        body = container(original, 1, 2, mode=2,
                         models=((0, 0, 0, 0), (2, 0, 3, 0)),
                         payload=bytes((7, 9, 0, 0)))
        info = relational.inspect_archive(wrap(body))
        self.assertEqual((info["width"], info["fields"], info["source_bytes"], info["records"]),
                         (1, 2, 4, 2))
        self.assertEqual(info["storage_mode"], 2)
        self.assertEqual(info["models"][1], {"kind": 2, "source": 0, "a": 3, "b": 0})
        with self.assertRaises(ValueError):
            relational.inspect_archive(wrap(body[:-1] + bytes((1,))))


class RelationalRejectionTests(unittest.TestCase):
    def assert_rejected(self, body):
        with self.assertRaises(ValueError):
            relational.decode(wrap(body))

    def test_invalid_headers_and_exact_length_contract(self):
        original = bytes((1, 2, 3, 4))
        bad_headers = ({"magic": b"AHPS\x05xxx"}, {"width": 3}, {"fields": 0},
                       {"fields": 17}, {"mode": 3}, {"reserved": 1},
                       {"size": 3}, {"size": 6}, {"size": 2**63},
                       {"crc": zlib.crc32(original) ^ 1})
        for changes in bad_headers:
            with self.subTest(changes=changes):
                options = {"width": 2, "fields": 1}
                options.update(changes)
                self.assert_rejected(container(original, **options))
        valid = container(original, 2, 1)
        for body in (b"", valid[:HEADER.size - 1], valid[:-1], valid + b"\0"):
            with self.subTest(length=len(body)):
                self.assert_rejected(body)

    def test_invalid_model_kinds_causality_and_unused_parameters(self):
        original = bytes((1, 2))
        invalid = (
            ((3, 0, 0, 0), (0, 0, 0, 0)),
            ((2, 0, 1, 0), (0, 0, 0, 0)),  # first field refers to itself
            ((0, 0, 0, 0), (2, 1, 1, 0)),  # later field refers to itself
            ((0, 0, 0, 0), (2, 2, 1, 0)),  # forward reference
            ((0, 1, 0, 0), (0, 0, 0, 0)),
            ((0, 0, 1, 0), (0, 0, 0, 0)),
            ((0, 0, 0, 1), (0, 0, 0, 0)),
            ((1, 1, 0, 0), (0, 0, 0, 0)),
            ((1, 0, 1, 0), (0, 0, 0, 0)),
            ((1, 0, 0, 1), (0, 0, 0, 0)),
            ((0, 0, 0, 0), (2, 0, 256, 0)),
            ((0, 0, 0, 0), (2, 0, 1, 256)),
        )
        for models in invalid:
            with self.subTest(models=models):
                self.assert_rejected(container(original, 1, 2, mode=2, models=models))

    def test_missing_extra_and_truncated_model_descriptors(self):
        original = bytes((1, 2))
        model = (0, 0, 0, 0)
        for models in ((), (model,), (model, model, model)):
            self.assert_rejected(container(original, 1, 2, mode=2, models=models))
        body = container(original, 1, 2, mode=2, models=(model, model))
        self.assert_rejected(body[:HEADER.size + MODEL.size + 1])

    def test_truncated_corrupted_trailing_and_concatenated_xz(self):
        archive = wrap(container(b"abcd", 1, 1))
        altered = bytearray(archive)
        altered[len(altered) // 2] ^= 0x80
        cases = (b"", archive[:-1], archive[:len(archive) // 2], bytes(altered),
                 archive + b"\0", archive + bytes(4), archive + archive)
        for invalid in cases:
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                relational.decode(invalid)

    def test_non_crc64_stream_and_ahps_magic_are_rejected(self):
        for check in (lzma.CHECK_NONE, lzma.CHECK_CRC32):
            with self.subTest(check=check), self.assertRaises(ValueError):
                relational.decode(wrap(container(b"abcd", 1, 1), check=check))
        with self.assertRaises(ValueError):
            relational.decode(wrap(b"AHPS\x05" + bytes(32)))

    def test_output_limit_is_inclusive_and_invalid_limits_rejected(self):
        archive = wrap(container(b"abcdefgh", 2, 2))
        self.assertEqual(relational.decode(archive, max_output_bytes=8), b"abcdefgh")
        with self.assertRaises(ValueError):
            relational.decode(archive, max_output_bytes=7)
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises((ValueError, TypeError)):
                relational.decode(archive, max_output_bytes=limit)

    def test_actual_expansion_is_bounded_even_when_declaration_lies(self):
        # The compressed stream claims zero output but contains a MiB of zeros.
        # The spy observes actual liblzma output, not the untrusted declaration.
        archive = wrap(container(b"", 1, 1, payload=bytes(1024**2)))
        actual_decoder = lzma.LZMADecompressor
        requests, returned, limits = [], [], []

        class ObservedDecoder:
            def __init__(self, *args, **kwargs):
                limits.append(kwargs.get("memlimit"))
                self.inner = actual_decoder(*args, **kwargs)

            def decompress(self, data, max_length=-1):
                requests.append(max_length)
                result = self.inner.decompress(data, max_length=max_length)
                returned.append(len(result))
                return result

            def __getattr__(self, name):
                return getattr(self.inner, name)

        cap = 8 + HEADER.size + 16 * MODEL.size + 1
        with mock.patch.object(relational.lzma, "LZMADecompressor", ObservedDecoder):
            with self.assertRaises(ValueError):
                relational.decode(archive, max_output_bytes=8)
        self.assertTrue(requests)
        self.assertTrue(all(0 < count <= cap for count in requests), requests)
        self.assertLessEqual(sum(returned), cap)
        self.assertTrue(all(limit == 128 * 1024**2 for limit in limits), limits)


if __name__ == "__main__":
    unittest.main()
