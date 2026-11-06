"""Independent arithmetic/token wire and isolated AHRC-format checks."""

import binascii
import ctypes as ct
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import unittest
from unittest import mock

from research import independent_backend as codec


HEADER = struct.Struct("<8sBBBBQQQQII")
MAGIC = b"AHRC\0\0\0\1"
WINDOW = 8 * 1024**2
MASK = (1 << 32) - 1
HALF, QUARTER = 1 << 31, 1 << 30
HAVE_BACKEND = os.name == "nt" and codec.BACKEND_PATH.is_file()
HAVE_TRANSFORMS = (HAVE_BACKEND and codec.TRANSFORM_PATH.is_file()
                   and bool(ct.windll.kernel32.IsProcessorFeaturePresent(40)))
requires_backend = unittest.skipUnless(HAVE_BACKEND, "requires the optional Windows AHRC research DLL")
requires_transforms = unittest.skipUnless(
    HAVE_TRANSFORMS, "requires the frozen Windows AHRC and AVX2 transform research DLLs"
)


def setUpModule():
    if HAVE_BACKEND:
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ct.c_void_p
        kernel.SetPriorityClass.argtypes = [ct.c_void_p, ct.c_uint32]
        kernel.GetPriorityClass.argtypes = [ct.c_void_p]
        process = kernel.GetCurrentProcess()
        if not kernel.SetPriorityClass(process, 0x4000) or kernel.GetPriorityClass(process) != 0x4000:
            raise RuntimeError("independent backend tests require below-normal priority")


class ArithmeticReference:
    """Protocol implementation using Python integers and keyed probability state."""

    def __init__(self, payload=None):
        self.low, self.high, self.pending = 0, MASK, 0
        self.models, self.bits, self.position = {}, [], 0
        if payload is not None:
            count = struct.unpack_from("<Q", payload)[0]
            if count < 32 or len(payload) != 8 + (count + 7) // 8:
                raise ValueError("invalid bit extent")
            self.bits = [(byte >> shift) & 1 for byte in payload[8:] for shift in range(7, -1, -1)]
            if any(self.bits[count:]) or any(self.bits[count - 30:count]):
                raise ValueError("nonzero padding")
            self.bits = self.bits[:count]
            self.code = 0
            for _ in range(32):
                self.code = (self.code << 1) | self.read()

    def read(self):
        if self.position == len(self.bits):
            raise ValueError("truncated arithmetic bits")
        bit = self.bits[self.position]
        self.position += 1
        return bit

    def emit(self, bit):
        self.bits.append(bit)
        self.bits.extend([1 - bit] * self.pending)
        self.pending = 0

    def bit(self, key, value=None):
        probability = self.models.get(key, 2048)
        split = self.low + ((self.high - self.low + 1) * probability // 4096) - 1
        decoding = value is None
        if decoding:
            value = int(self.code > split)
        if value:
            self.low = split + 1
        else:
            self.high = split
        self.models[key] = probability - (probability >> 5) if value else probability + ((4096 - probability) >> 5)
        while True:
            if self.high < HALF:
                if not decoding:
                    self.emit(0)
            elif self.low >= HALF:
                self.low -= HALF
                self.high -= HALF
                if decoding:
                    self.code -= HALF
                else:
                    self.emit(1)
            elif self.low >= QUARTER and self.high < 3 * QUARTER:
                self.low -= QUARTER
                self.high -= QUARTER
                if decoding:
                    self.code -= QUARTER
                else:
                    self.pending += 1
            else:
                break
            self.low *= 2
            self.high = 2 * self.high + 1
            if decoding:
                self.code = 2 * self.code + self.read()
        return value

    def positive(self, family, value=None):
        if value is None:
            depth = 0
            while self.bit((family, "prefix", depth)):
                depth += 1
                if depth == 24:
                    raise ValueError("positive integer too wide")
            value = 1 << depth
            for position in range(depth - 1, -1, -1):
                value |= self.bit((family, "suffix", depth, position)) << position
            return value
        depth = value.bit_length() - 1
        for index in range(depth + 1):
            self.bit((family, "prefix", index), int(index < depth))
        for position in range(depth - 1, -1, -1):
            self.bit((family, "suffix", depth, position), (value >> position) & 1)

    def finish(self):
        self.pending += 1
        self.emit(int(self.low >= QUARTER))
        self.bits.extend([0] * 30)
        result = bytearray((len(self.bits) + 7) // 8)
        for index, bit in enumerate(self.bits):
            result[index // 8] |= bit << (7 - index % 8)
        return struct.pack("<Q", len(self.bits)) + result


def append_match(output, length, distance):
    # Repeating the available period is equivalent to forward overlapping copy.
    period = bytes(output[-distance:])
    if not period:
        period = b"\0"  # Lets rejection fixtures describe an impossible match.
    output.extend((period * ((length + len(period) - 1) // len(period)))[:length])


def token_payload(tokens):
    arithmetic, output = ArithmeticReference(), bytearray()
    previous_match, last_distance = 0, 1
    for token in tokens:
        match = isinstance(token, tuple)
        position = len(output)
        arithmetic.bit(("flag", (position & 3) * 2 + previous_match), int(match))
        if match:
            length, distance = token
            repeat = distance == last_distance
            arithmetic.bit(("repeat", position & 3), int(repeat))
            if not repeat:
                arithmetic.positive("distance", distance)
                last_distance = distance
            arithmetic.positive("length", length - 3)
            append_match(output, length, distance)
        else:
            previous, node = output[-1] if output else 0, 1
            for position in range(7, -1, -1):
                bit = (token >> position) & 1
                arithmetic.bit(("literal", previous, node), bit)
                node = node * 2 + bit
            output.append(token)
        previous_match = int(match)
    return arithmetic.finish(), bytes(output)


def reference_decode(payload, expected):
    arithmetic, output = ArithmeticReference(payload), bytearray()
    previous_match, last_distance = 0, 1
    while len(output) < expected:
        position = len(output)
        match = arithmetic.bit(("flag", (position & 3) * 2 + previous_match))
        if match:
            repeat = arithmetic.bit(("repeat", position & 3))
            distance = last_distance if repeat else arithmetic.positive("distance")
            length = arithmetic.positive("length") + 3
            if not 1 <= distance <= min(WINDOW, len(output)) or not 4 <= length <= min(65535, expected - len(output)):
                raise ValueError("invalid match")
            last_distance = distance
            append_match(output, length, distance)
        else:
            previous, node, value = output[-1] if output else 0, 1, 0
            for _ in range(8):
                bit = arithmetic.bit(("literal", previous, node))
                value = (value << 1) | bit
                node = node * 2 + bit
            output.append(value)
        previous_match = match
    if arithmetic.position != len(arithmetic.bits):
        raise ValueError("unconsumed arithmetic bits")
    return bytes(output)


def raw_encode(data, capacity=None):
    capacity = 2 * len(data) + 4096 if capacity is None else capacity
    source = (ct.c_uint8 * len(data)).from_buffer_copy(data)
    guard = bytes([0xA5]) * 16
    output = (ct.c_uint8 * (capacity + 32)).from_buffer_copy(guard + bytes(capacity) + guard)
    written = ct.c_size_t(999)
    status = codec.backend_library().ahrc_compress(ct.cast(source, ct.c_char_p), len(data), ct.byref(output, 16), capacity, ct.byref(written))
    if bytes(output[:16]) != guard or bytes(output[-16:]) != guard or bytes(source) != data:
        raise AssertionError("compressor crossed a capacity canary or mutated input")
    return status, bytes(output[16:16 + written.value]), written.value


def raw_decode(payload, expected):
    source = (ct.c_uint8 * len(payload)).from_buffer_copy(payload)
    guard = bytes([0x5A]) * 16
    output = (ct.c_uint8 * (expected + 32)).from_buffer_copy(guard + bytes(expected) + guard)
    status = codec.backend_library().ahrc_decompress(ct.cast(source, ct.c_char_p), len(payload), ct.byref(output, 16), expected)
    if bytes(output[:16]) != guard or bytes(output[-16:]) != guard or bytes(source) != payload:
        raise AssertionError("decoder crossed a capacity canary or mutated input")
    return status, bytes(output[16:16 + expected])


def outer(source, residual=None, mode=0, representation=0, table_start=0, table_length=0, **changes):
    residual = source if residual is None else residual
    fields = dict(magic=MAGIC, mode=mode, representation=representation, reserved_a=0,
                  reserved_b=0, size=len(source), payload_length=len(residual), start=table_start,
                  length=table_length, source_crc=binascii.crc32(source), residual_crc=binascii.crc32(residual))
    fields.update(changes)
    return HEADER.pack(*(fields[k] for k in ("magic", "mode", "representation", "reserved_a",
        "reserved_b", "size", "payload_length", "start", "length", "source_crc", "residual_crc"))) + residual


@requires_backend
class BackendArithmeticTests(unittest.TestCase):
    def test_independent_hardcoded_empty_and_one_zero_streams(self):
        fixtures = [(b"", bytes.fromhex("2000000000000000 40000000")),
                    (b"\0", bytes.fromhex("2900000000000000 002000000000"))]
        self.assertEqual(codec.backend_library().ahrc_version(), 1)
        for source, expected in fixtures:
            with self.subTest(source=source):
                self.assertEqual(token_payload(list(source))[0], expected)
                self.assertEqual(raw_encode(source)[1], expected)
                self.assertEqual(raw_decode(expected, len(source)), (0, source))
                self.assertEqual(reference_decode(expected, len(source)), source)

    def test_literal_only_native_wire_matches_independent_models(self):
        for source in (b"A", bytes(range(32)), bytes(range(255, 200, -1))):
            expected, _ = token_payload(list(source))
            status, payload, count = raw_encode(source)
            with self.subTest(size=len(source)):
                self.assertEqual(status, 0)
                self.assertEqual((payload, count), (expected, len(expected)))

    def test_repeat_distance_and_explicit_distance_match_known_greedy_tokens(self):
        for tokens in ([65, (63, 1)], [65, 66, 67, 68, (60, 4)]):
            expected, source = token_payload(tokens)
            with self.subTest(tokens=tokens):
                self.assertEqual(raw_encode(source)[1], expected)
                self.assertEqual(raw_decode(expected, len(source)), (0, source))
                self.assertEqual(reference_decode(expected, len(source)), source)

    def test_crafted_context_transitions_and_overlapping_matches_cross_decode(self):
        tokens = [65, 66, (11, 2), 67, (5, 3), (7, 3), 255, 0, (9, 1), 17]
        payload, source = token_payload(tokens)
        self.assertEqual(raw_decode(payload, len(source)), (0, source))
        self.assertEqual(reference_decode(payload, len(source)), source)
        native = raw_encode(source)[1]
        self.assertEqual(reference_decode(native, len(source)), source)

    def test_native_streams_decode_with_independent_reference(self):
        rng = random.Random(6301)
        for source in (rng.randbytes(511), bytes(range(64)) * 41,
                       b"one two three\0" * 100, bytes(131100)):
            status, payload, _ = raw_encode(source)
            with self.subTest(size=len(source)):
                self.assertEqual(status, 0)
                self.assertEqual(reference_decode(payload, len(source)), source)
                self.assertEqual(raw_decode(payload, len(source)), (0, source))

    def test_capacity_failures_clear_written_and_preserve_canaries(self):
        source = b"abcde" * 30
        expected = raw_encode(source)[1]
        for capacity in (0, 1, 7, 8, 11, len(expected) - 1):
            with self.subTest(capacity=capacity):
                status, payload, written = raw_encode(source, capacity)
                self.assertEqual((status, written, payload), (2, 0, b""))
        self.assertEqual(raw_encode(source, len(expected)), (0, expected, len(expected)))

    def test_invalid_lengths_nulls_and_overlapping_buffers_are_rejected(self):
        library = codec.backend_library()
        buffer = (ct.c_uint8 * 64)()
        source = ct.cast(buffer, ct.c_char_p)
        written = ct.c_size_t(17)
        self.assertEqual(library.ahrc_compress(source, 128 * 1024**2 + 1, buffer, 64, ct.byref(written)), 1)
        self.assertEqual(written.value, 0)
        self.assertEqual(library.ahrc_decompress(source, 12, buffer, 128 * 1024**2 + 1), 1)
        self.assertEqual(library.ahrc_compress(None, 1, buffer, 64, ct.byref(written)), 1)
        self.assertEqual(library.ahrc_compress(source, 12, buffer, 64, ct.byref(written)), 1)
        self.assertEqual(library.ahrc_decompress(source, 12, buffer, 12), 1)

    def test_bit_extent_padding_truncation_and_trailing_bytes_are_strict(self):
        payload, source = token_payload([65, (7, 1)])
        count = struct.unpack_from("<Q", payload)[0]
        pad = bytearray(payload)
        pad[-1] |= 1
        tail = bytearray(payload)
        bit = count - 2
        tail[8 + bit // 8] |= 1 << (7 - bit % 8)
        invalid = [b"", payload[:7], payload[:-1], payload + b"\0", bytes(pad), bytes(tail),
                   struct.pack("<Q", 31) + payload[8:],
                   struct.pack("<Q", 2**64 - 1) + payload[8:],
                   struct.pack("<Q", count + 8) + payload[8:] + b"\0"]
        for candidate in invalid:
            with self.subTest(size=len(candidate)):
                self.assertNotEqual(raw_decode(candidate, len(source))[0], 0)

    def test_match_history_length_and_output_bounds_are_enforced(self):
        for tokens, expected in (([(4, 1)], 4), ([65, (4, 2)], 5),
                                 ([65, (7, 1)], 7), ([65, (65536, 1)], 65537)):
            payload, _ = token_payload(tokens)
            with self.subTest(tokens=tokens):
                self.assertNotEqual(raw_decode(payload, expected)[0], 0)

    def test_exact_window_edge_and_one_beyond_using_small_token_streams(self):
        tokens = [65]
        remaining = WINDOW - 1
        while remaining:
            length = min(65535, remaining)
            if length < 4:
                tokens.extend([65] * length)
            else:
                tokens.append((length, 1))
            remaining -= length
        payload, _ = token_payload(tokens + [(4, WINDOW)])
        status, restored = raw_decode(payload, WINDOW + 4)
        self.assertEqual(status, 0)
        self.assertEqual(restored, b"A" * (WINDOW + 4))
        invalid, _ = token_payload(tokens + [65, (4, WINDOW + 1)])
        self.assertNotEqual(raw_decode(invalid, WINDOW + 5)[0], 0)


class IndependentRepresentationTests(unittest.TestCase):
    @requires_backend
    def test_word_native_helper_preserves_first_record_and_modular_fields(self):
        source = struct.pack("<IIIIII", 255, 256, 0xFFFFFFFF, 256, 255, 0)
        expected = source[:12] + struct.pack("<III", 1, 0xFFFFFFFF, 1)
        library = codec.backend_library()
        value = (ct.c_uint8 * 24).from_buffer_copy(source)
        out = (ct.c_uint8 * 24)()
        self.assertEqual(library.ahrc_word_delta(ct.cast(value, ct.c_char_p), 24, out, 0), 0)
        self.assertEqual(bytes(out), expected)
        self.assertEqual(library.ahrc_word_delta(ct.cast(out, ct.c_char_p), 24, out, 1), 0)
        self.assertEqual(bytes(out), source)
        self.assertEqual(library.ahrc_word_delta(ct.cast(value, ct.c_char_p), 24, value, 0), 0)
        self.assertEqual(bytes(value), expected)
        for size in (0, 1, 13):
            self.assertEqual(library.ahrc_word_delta(ct.cast(value, ct.c_char_p), size, out, 0), 1)
        self.assertEqual(library.ahrc_word_delta(ct.cast(value, ct.c_char_p), 12, ct.byref(value, 1), 0), 1)

    @requires_transforms
    def test_exe_split_uses_tail_targets_and_nonzero_suffix_origin(self):
        table = struct.pack("<III", 1, 2, 3)
        prefix = b"\xe8\x01\0\0\0\x90"
        suffix = b"\xe9\x02\0\0\0\x90"
        source = prefix + table + suffix
        expected_prefix = b"\xe8\x90" + struct.pack(">I", 6)
        expected_suffix = b"\xe9\x90" + struct.pack(">I", 18 + 5 + 2)
        expected = expected_prefix + table + expected_suffix
        for representation in ("table_byte", "table_word"):
            with self.subTest(representation=representation):
                residual = codec.transform(source, representation, table_start=6, table_length=12)
                self.assertEqual(residual, expected)
                self.assertEqual(codec.transform(residual, representation, table_start=6, table_length=12, inverse=True), source)

    @requires_transforms
    def test_byte_word_arithmetic_and_lane_order_are_independent(self):
        source = struct.pack("<IIIIII", 255, 256, 0xFFFFFFFF, 256, 255, 0)
        expected_word = source[:12] + struct.pack("<III", 1, 0xFFFFFFFF, 1)
        expected_byte = source[:12] + bytes.fromhex("01010000 ffff0000 01010101")
        for rep, expected in (("table_byte", expected_byte), ("table_word", expected_word)):
            expected = b"".join(expected[lane::12] for lane in range(12))
            self.assertEqual(codec.transform(source, rep, 0, 24), expected)
            self.assertEqual(codec.transform(expected, rep, 0, 24, inverse=True), source)

    @requires_transforms
    def test_all_variants_roundtrip_with_bounded_own_format_fallback(self):
        source = b"\xe8\x01\0\0\0\x90" + random.Random(713).randbytes(120) + b"tail"
        for rep in ("raw", "exe", "table_byte", "table_word"):
            kwargs = {"table_start": 6, "table_length": 120} if rep.startswith("table_") else {}
            archive, metadata = codec.encode_variant(source, rep, **kwargs)
            with self.subTest(representation=rep):
                self.assertEqual(archive[:8], MAGIC)
                self.assertEqual(metadata["representation"], rep)
                self.assertLessEqual(len(archive), len(source) + 52)
                self.assertEqual(codec.decode(archive), source)

    def test_raw_stored_header_and_empty_are_independent_wire_fixtures(self):
        for source in (b"", b"x", bytes(range(8))):
            archive, metadata = codec.encode_variant(source, "raw")
            self.assertEqual(archive, outer(source))
            self.assertEqual(metadata["backend_mode"], "stored")
            self.assertEqual(codec.decode(outer(source)), source)
        self.assertEqual(len(codec.encode(b"")[0]), 52)

    @requires_backend
    def test_coded_raw_payload_cross_decodes_with_reference(self):
        source = b"abcdefgh" * 200
        archive, metadata = codec.encode_variant(source, "raw")
        self.assertEqual(metadata["backend_mode"], "coded")
        self.assertEqual(reference_decode(archive[52:], len(source)), source)
        payload, expected = token_payload([65, (99, 1)])
        archive = outer(expected, payload, mode=1, residual_crc=binascii.crc32(expected))
        self.assertEqual(codec.decode(archive), expected)

    @requires_transforms
    def test_auto_stable_selection_and_explicit_stored_representation(self):
        candidates = {"table_word": b"word", "exe": b"exe!", "raw": b"raw!", "table_byte": b"byte"}
        self.assertEqual(codec.select_archive(candidates), (b"raw!", "raw"))
        candidates["table_word"] = b"w"
        self.assertEqual(codec.select_archive(candidates), (b"w", "table_word"))
        source = bytes(range(24))
        archive, metadata = codec.encode(source, table_start=0, table_length=24)
        self.assertEqual(set(metadata["candidate_bytes"]), {"raw", "exe", "table_byte", "table_word"})
        self.assertEqual(len(archive), min(metadata["candidate_bytes"].values()))
        self.assertEqual(codec.decode(archive), source)
        stored, meta = codec.encode_variant(source, "table_word", table_start=0, table_length=24)
        self.assertEqual(meta["representation"], "table_word")
        self.assertEqual(stored[9], 3)

    def test_payload_ties_and_capacity_exhaustion_store_but_other_failures_propagate(self):
        source = bytes(range(24))
        for status in (0, 2, 1, 3):
            def compressor(data, size, output, capacity, written):
                ct.cast(written, ct.POINTER(ct.c_size_t))[0] = size if status == 0 else 0
                return status

            fake = mock.Mock()
            fake.ahrc_compress.side_effect = compressor
            with self.subTest(status=status), mock.patch.object(codec, "backend_library", return_value=fake):
                if status in (0, 2):
                    archive, metadata = codec.encode_variant(source, "raw")
                    self.assertEqual(archive, outer(source))
                    self.assertEqual(metadata["backend_mode"], "stored")
                else:
                    with self.assertRaises(MemoryError if status == 3 else ValueError):
                        codec.encode_variant(source, "raw")

    def test_both_crcs_and_structural_inspection_flag(self):
        source = bytes(range(24))
        archive = outer(source)
        self.assertFalse(codec.inspect_archive(archive)["checksums_verified"])
        self.assertTrue(codec.inspect_archive(archive, verify_crc=True)["checksums_verified"])
        for changes in ({"source_crc": binascii.crc32(source) ^ 1},
                        {"residual_crc": binascii.crc32(source) ^ 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                codec.decode(outer(source, **changes))

    def test_invalid_outer_descriptors_and_trailing_archives_are_rejected(self):
        source = bytes(24)
        cases = [outer(source, magic=b"AHRC\0\0\0\2"), outer(source, mode=2),
                 outer(source, representation=4), outer(source, reserved_a=1),
                 outer(source, reserved_b=1), outer(source, size=128 * 1024**2 + 1),
                 outer(source, payload_length=23), outer(source, table_start=1),
                 outer(source, representation=2), outer(source, representation=3, table_length=13),
                 outer(source, representation=3, table_start=13, table_length=12),
                 outer(source)[:-1], outer(source) + b"x", outer(source) * 2,
                 outer(b"", representation=1)]
        for archive in cases:
            with self.subTest(header=archive[:12]), self.assertRaises(ValueError):
                codec.decode(archive)
        with mock.patch.object(codec, "backend_library", side_effect=AssertionError("native called")), \
                self.assertRaises(ValueError):
            codec.decode(outer(source), max_output_bytes=23)

    def test_invalid_representation_and_table_arguments(self):
        source = bytes(24)
        for rep, kwargs in (("unknown", {}), ("raw", {"table_start": 0, "table_length": 12}),
                            ("exe", {"table_start": 0}), ("table_word", {}),
                            ("table_byte", {"table_start": -1, "table_length": 12}),
                            ("table_word", {"table_start": 0, "table_length": 13})):
            with self.subTest(representation=rep, kwargs=kwargs), self.assertRaises(ValueError):
                codec.encode_variant(source, rep, **kwargs)

    @requires_transforms
    def test_fresh_process_works_while_old_python_codecs_and_planner_imports_are_denied(self):
        root = str(Path(__file__).resolve().parents[1])
        script = """
import sys
blocked = ('lzma', '_lzma', 'ahps', 'zlib', 'bz2', '_bz2', 'zstandard',
           'research.relational', 'research.relational_file', 'research.relational_hybrid',
           'research.compare_same_boundary_tables', 'research.compare_relational')
class Deny:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('prohibited dependency: ' + fullname)
sys.meta_path.insert(0, Deny())
sys.path.insert(0, ROOT)
from research import independent_backend as backend
source = bytes(range(24)) * 9
for rep in ('raw', 'exe', 'table_byte', 'table_word'):
    kwargs = {'table_start': 0, 'table_length': len(source)} if rep.startswith('table_') else {}
    archive, metadata = backend.encode_variant(source, rep, **kwargs)
    assert archive[:8] == b'AHRC\\0\\0\\0\\1'
    assert backend.decode(archive) == source
archive, metadata = backend.encode(source, table_start=0, table_length=len(source))
assert backend.decode(archive) == source
assert not any(name in sys.modules for name in blocked)
print('independent encode/decode passed')
""".replace("ROOT", repr(root))
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script],
                                capture_output=True, text=True, timeout=30,
                                creationflags=0x4000 if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("independent encode/decode passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
