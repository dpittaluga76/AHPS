"""Independent AHRC1 LZ77/arithmetic backend with reversible AHPS transforms.

No old Python planner or compression library is imported here. The old native
DLL supplies only reversible lifts; all repetition matching and entropy coding
are implemented by the separate ahrc_backend DLL. See the frozen protocol.
"""
from __future__ import annotations

import binascii
import ctypes
import hashlib
import os
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "research" / "ahrc_backend.dll"
TRANSFORM_PATH = ROOT / "ahps_simd.dll"
TRANSFORM_SHA256 = "71df88e8d41c9f233838c6563de714427ccc46782407d20836c0bc1e937c6022"
MAGIC = b"AHRC\0\0\0\1"
HEADER = struct.Struct("<8sBBBBQQQQII")
FILE_CAP = 128 * 1024**2
TABLE_CAP = 8 * 1024**2
ARCHIVE_CAP = HEADER.size + FILE_CAP
REPRESENTATIONS = ("raw", "exe", "table_byte", "table_word")
PARAMS12 = b"\x0c\0\0\0"
_backend = None
_transforms = None


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _binary(value, name, cap):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    size = value.nbytes if isinstance(value, memoryview) else len(value)
    if size > cap:
        raise ValueError(f"{name} exceeds the research limit")
    return bytes(value)


def _output_limit(value):
    _integer(value, "max_output_bytes")
    if value < 0:
        raise ValueError("output limit must be nonnegative")
    return min(value, FILE_CAP)


def backend_library():
    global _backend
    if _backend is None:
        if os.name != "nt":
            raise RuntimeError("This research build requires Windows")
        lib = ctypes.CDLL(str(BACKEND_PATH.resolve()))
        lib.ahrc_version.argtypes = []
        lib.ahrc_version.restype = ctypes.c_uint32
        if lib.ahrc_version() != 1:
            raise RuntimeError("Independent backend ABI 1 required")
        lib.ahrc_compress.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        lib.ahrc_compress.restype = ctypes.c_int
        lib.ahrc_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t]
        lib.ahrc_decompress.restype = ctypes.c_int
        lib.ahrc_word_delta.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_int]
        lib.ahrc_word_delta.restype = ctypes.c_int
        _backend = lib
    return _backend


def transform_library():
    global _transforms
    if _transforms is None:
        if os.name != "nt":
            raise RuntimeError("The frozen transform build requires Windows")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.IsProcessorFeaturePresent.argtypes = [ctypes.c_uint32]
        kernel.IsProcessorFeaturePresent.restype = ctypes.c_int
        if not kernel.IsProcessorFeaturePresent(40):
            raise RuntimeError("AVX2 must be confirmed before loading the frozen transform DLL")
        if hashlib.sha256(TRANSFORM_PATH.read_bytes()).hexdigest() != TRANSFORM_SHA256:
            raise RuntimeError("Frozen transform DLL identity changed")
        lib = ctypes.CDLL(str(TRANSFORM_PATH.resolve()))
        lib.ahps_abi_version.argtypes = []
        lib.ahps_abi_version.restype = ctypes.c_int
        if lib.ahps_abi_version() != 206:
            raise RuntimeError("Frozen transform ABI 206 required")
        for function in (lib.ahps_forward_lift, lib.ahps_inverse_lift):
            function.argtypes = [ctypes.c_uint8, ctypes.c_char_p, ctypes.c_char_p,
                                 ctypes.c_void_p, ctypes.c_int]
            function.restype = None
        _transforms = lib
    return _transforms


def native_info():
    backend, transforms = backend_library(), transform_library()
    return {"backend": {"path": str(Path(backend._name).resolve()), "abi": backend.ahrc_version()},
            "transforms": {"path": str(Path(transforms._name).resolve()),
                           "abi": transforms.ahps_abi_version()}}


def _range(size, representation, table_start, table_length):
    if representation not in REPRESENTATIONS:
        raise ValueError("unknown representation")
    _integer(table_start, "table_start")
    _integer(table_length, "table_length")
    if representation in ("raw", "exe"):
        if table_start or table_length:
            raise ValueError("non-table representations require zero table fields")
    elif (table_start < 0 or not 0 < table_length <= TABLE_CAP or table_length % 12
          or table_start + table_length > size):
        raise ValueError("table must be complete bounded records inside the source")


def _lift(data, opcode, params, inverse):
    if not data:
        return b""
    if opcode not in (2, 7, 9) or len(params) != 4:
        raise ValueError("only the declared reversible transforms are supported")
    output = ctypes.create_string_buffer(len(data))
    lib = transform_library()
    function = lib.ahps_inverse_lift if inverse else lib.ahps_forward_lift
    function(opcode, params, data, output, len(data))
    return output.raw


def _word(data, inverse):
    if not data or len(data) > TABLE_CAP or len(data) % 12:
        raise ValueError("word transform requires complete bounded records")
    output = ctypes.create_string_buffer(len(data))
    code = backend_library().ahrc_word_delta(data, len(data), output, int(inverse))
    if code:
        raise ValueError("native word transform rejected its inputs")
    return output.raw


def transform(data, representation, table_start=0, table_length=0, inverse=False):
    """Apply only fixed reversible lifts, with independent outside histories."""
    data = _binary(data, "data", FILE_CAP)
    if not isinstance(inverse, bool):
        raise TypeError("inverse must be boolean")
    _range(len(data), representation, table_start, table_length)
    if representation == "raw":
        return data
    if representation == "exe":
        return _lift(data, 9, bytes(4), inverse)
    end = table_start + table_length
    output = bytearray(len(data))
    output[:table_start] = _lift(data[:table_start], 9, bytes(4), inverse)
    table = data[table_start:end]
    if inverse:
        table = _lift(table, 7, PARAMS12, True)
        table = _word(table, True) if representation == "table_word" else _lift(table, 2, PARAMS12, True)
    else:
        table = _word(table, False) if representation == "table_word" else _lift(table, 2, PARAMS12, False)
        table = _lift(table, 7, PARAMS12, False)
    output[table_start:end] = table
    output[end:] = _lift(data[end:], 9, (end & 0xFFFFFFFF).to_bytes(4, "big"), inverse)
    return bytes(output)


def _compress(residual):
    # A coded stream needs an 8-byte bit count and at least four coding bytes.
    # Never allocate a worst-case expanded buffer merely to discard it later.
    if len(residual) <= 12:
        return residual, 0
    output = ctypes.create_string_buffer(len(residual))
    written = ctypes.c_size_t(0)
    code = backend_library().ahrc_compress(residual, len(residual), output,
                                          len(residual), ctypes.byref(written))
    if code == 2:
        return residual, 0
    if code == 3:
        raise MemoryError("independent backend allocation failed")
    if code:
        raise ValueError("independent backend rejected its input")
    if not 12 <= written.value <= len(residual):
        raise ValueError("independent backend returned an invalid encoded length")
    if written.value == len(residual):
        return residual, 0
    return ctypes.string_at(output, written.value), 1


def _payload_shape(payload):
    if len(payload) < 12:
        raise ValueError("coded payload is truncated")
    bits = struct.unpack_from("<Q", payload)[0]
    if bits < 32 or (bits + 7) // 8 != len(payload) - 8:
        raise ValueError("invalid coded bit count")
    padding = (-bits) & 7
    # Finalization appends thirty zero bits, followed by zero byte padding.
    tail = int.from_bytes(payload[-5:], "big")
    if tail & ((1 << (30 + padding)) - 1):
        raise ValueError("nonzero coded finalization or byte padding")
    return bits


def _parse(archive, max_output_bytes):
    limit = _output_limit(max_output_bytes)
    archive = _binary(archive, "archive", ARCHIVE_CAP)
    if len(archive) < HEADER.size:
        raise ValueError("truncated AHRC header")
    magic, mode, rep, reserved1, reserved2, size, payload_size, start, length, source_crc, residual_crc = HEADER.unpack_from(archive)
    if magic != MAGIC or mode not in (0, 1) or rep >= len(REPRESENTATIONS) or reserved1 or reserved2:
        raise ValueError("invalid AHRC version, mode or reserved fields")
    if size > limit:
        raise ValueError("declared source exceeds output limit")
    if payload_size != len(archive) - HEADER.size:
        raise ValueError("payload length mismatch or trailing data")
    representation = REPRESENTATIONS[rep]
    _range(size, representation, start, length)
    if size == 0 and (mode or rep or payload_size or source_crc or residual_crc):
        raise ValueError("empty archives must use canonical raw stored form")
    if (mode == 0 and payload_size != size) or (mode == 1 and not 12 <= payload_size < size):
        raise ValueError("noncanonical stored/coded payload size")
    payload = archive[HEADER.size:]
    metadata = {"format": "AHRC1", "representation": representation,
                "backend_mode": "stored" if mode == 0 else "coded", "source_bytes": size,
                "payload_bytes": payload_size, "archive_bytes": len(archive),
                "table_start": start, "table_length": length,
                "source_crc32": source_crc, "residual_crc32": residual_crc,
                "checksums_verified": False}
    if mode:
        metadata["bit_count"] = _payload_shape(payload)
    return payload, metadata


def _restore(payload, metadata):
    size = metadata["source_bytes"]
    if metadata["backend_mode"] == "stored":
        residual = payload
    else:
        output = ctypes.create_string_buffer(size)
        code = backend_library().ahrc_decompress(payload, len(payload), output, size)
        if code == 3:
            raise MemoryError("independent decoder allocation failed")
        if code:
            raise ValueError("invalid independent backend stream")
        residual = output.raw
        del output
    if binascii.crc32(residual) != metadata["residual_crc32"]:
        raise ValueError("transformed residual checksum mismatch")
    restored = transform(residual, metadata["representation"], metadata["table_start"],
                         metadata["table_length"], inverse=True)
    if binascii.crc32(restored) != metadata["source_crc32"]:
        raise ValueError("restored source checksum mismatch")
    return restored


def decode(archive, max_output_bytes=FILE_CAP):
    payload, metadata = _parse(archive, max_output_bytes)
    return _restore(payload, metadata)


def inspect_archive(archive, max_output_bytes=FILE_CAP, verify_crc=False):
    if not isinstance(verify_crc, bool):
        raise TypeError("verify_crc must be boolean")
    payload, metadata = _parse(archive, max_output_bytes)
    if verify_crc:
        _restore(payload, metadata)
        metadata["checksums_verified"] = True
    return metadata


def encode_variant(data, representation="raw", table_start=None, table_length=None):
    data = _binary(data, "data", FILE_CAP)
    if representation in ("raw", "exe"):
        if table_start is not None or table_length is not None:
            raise ValueError("non-table variants do not accept table arguments")
        start, length = 0, 0
    else:
        if table_start is None or table_length is None:
            raise ValueError("table variants need both table arguments")
        start, length = table_start, table_length
    _range(len(data), representation, start, length)
    if not data:
        representation = "raw"
    residual = transform(data, representation, start, length)
    payload, mode = _compress(residual)
    source_crc, residual_crc = binascii.crc32(data), binascii.crc32(residual)
    archive = HEADER.pack(MAGIC, mode, REPRESENTATIONS.index(representation), 0, 0,
                          len(data), len(payload), start, length, source_crc, residual_crc) + payload
    metadata = inspect_archive(archive, max_output_bytes=len(data))
    metadata.update(residual_sha256=hashlib.sha256(residual).hexdigest(),
                    archive_sha256=hashlib.sha256(archive).hexdigest())
    return archive, metadata


def select_archive(candidates):
    """Choose among already verified AHRC candidates, never an AHPS fallback."""
    if not isinstance(candidates, dict) or "raw" not in candidates or any(k not in REPRESENTATIONS for k in candidates):
        raise ValueError("selection requires raw and only declared independent representations")
    choices = [(name, _binary(candidates[name], "candidate", ARCHIVE_CAP))
               for name in REPRESENTATIONS if name in candidates]
    name, archive = min(choices, key=lambda item: len(item[1]))
    return archive, name


def encode(data, representation="auto", table_start=None, table_length=None):
    data = _binary(data, "data", FILE_CAP)
    if representation != "auto":
        return encode_variant(data, representation, table_start, table_length)
    if (table_start is None) != (table_length is None):
        raise ValueError("both table arguments must be supplied together")
    methods = REPRESENTATIONS[:2]
    if table_start is not None:
        _range(len(data), "table_word", table_start, table_length)
        methods = REPRESENTATIONS
    candidates, trials = {}, []
    for name in methods:
        arguments = {"table_start": table_start, "table_length": table_length} if name.startswith("table_") else {}
        candidates[name], metadata = encode_variant(data, name, **arguments)
        trials.append(metadata)
    archive, name = select_archive(candidates)
    return archive, {"selected_representation": name,
                     "candidate_bytes": {k: len(v) for k, v in candidates.items()},
                     "candidate_backend_modes": {m: trial["backend_mode"] for m, trial in zip(methods, trials)},
                     "trials": trials}
