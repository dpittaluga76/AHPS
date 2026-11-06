"""Exact FP8 magnitude rANS coding with packed signs and an optional native path.

ABI 1 uses a 4096-total frequency table supplied by the caller. The payload is
packed sign bits (least-significant bit first), a little-endian 32-bit rANS
state, and the reversed renormalization bytes. Decoding restores raw bytes.
"""

import ctypes
import operator
from pathlib import Path
import struct
from typing import Sequence


MAX_INPUT_BYTES = 32 * 1024 * 1024
SCALE_BITS = 12
SCALE_TOTAL = 1 << SCALE_BITS
RANS_L = 1 << 23
EXPECTED_ABI_VERSION = 1


def _load_native():
    directory = Path(__file__).resolve().parent
    for name in ("ahps_rans.dll", "libahps_rans.so", "libahps_rans.dylib"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            library = ctypes.CDLL(str(path))
            library.ahps_rans_abi_version.argtypes = []
            library.ahps_rans_abi_version.restype = ctypes.c_uint32
            if library.ahps_rans_abi_version() != EXPECTED_ABI_VERSION:
                continue
            common = [ctypes.c_char_p, ctypes.c_size_t]
            table = [ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t]
            destination = [ctypes.c_void_p, ctypes.c_size_t]
            library.ahps_rans_encode.argtypes = common + table + destination + [ctypes.POINTER(ctypes.c_size_t)]
            library.ahps_rans_encode.restype = ctypes.c_int
            library.ahps_rans_decode.argtypes = common + [ctypes.c_size_t] + table + destination
            library.ahps_rans_decode.restype = ctypes.c_int
            return library
        except (OSError, AttributeError):
            continue
    return None


_NATIVE = _load_native()


def _validate_frequencies(frequencies):
    values = []
    for frequency in frequencies:
        if isinstance(frequency, bool):
            raise TypeError("rANS frequencies must be integers.")
        value = operator.index(frequency)
        if not 0 <= value <= SCALE_TOTAL:
            raise ValueError("rANS frequencies must be between 0 and 4096.")
        values.append(value)
        if len(values) > 128:
            raise ValueError("rANS requires exactly 128 frequencies.")
    if len(values) != 128 or sum(values) != SCALE_TOTAL:
        raise ValueError("rANS requires 128 frequencies totaling 4096.")
    return values


def _cumulative(frequencies):
    cumulative = []
    total = 0
    for frequency in frequencies:
        cumulative.append(total)
        total += frequency
    return cumulative


def _check_status(status):
    if status == 2:
        raise BufferError("rANS output capacity exceeded.")
    if status != 0:
        raise ValueError("Invalid rANS data or frequency table.")


def encode_fp8(data: bytes, frequencies: Sequence[int], *, force_python: bool = False) -> bytes:
    """Encode at most 32 MiB of exact FP8 bytes with the supplied histogram."""
    if not isinstance(data, bytes):
        raise TypeError("rANS input must be bytes.")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("rANS input exceeds 32 MiB.")
    if type(force_python) is not bool:
        raise TypeError("force_python must be a boolean.")
    frequencies = _validate_frequencies(frequencies)
    sign_bytes = (len(data) + 7) // 8
    if _NATIVE is not None and not force_python:
        table = (ctypes.c_uint16 * 128)(*frequencies)
        capacity = sign_bytes + 2 * len(data) + 4
        output = ctypes.create_string_buffer(capacity)
        written = ctypes.c_size_t()
        status = _NATIVE.ahps_rans_encode(data, len(data), table, 128, output, capacity, ctypes.byref(written))
        _check_status(status)
        if written.value > capacity:
            raise ValueError("Native rANS returned an invalid output length.")
        return ctypes.string_at(output, written.value)

    cumulative = _cumulative(frequencies)
    signs = bytearray(sign_bytes)
    emitted = bytearray()
    state = RANS_L
    for index in range(len(data) - 1, -1, -1):
        value = data[index]
        magnitude = value & 127
        frequency = frequencies[magnitude]
        if not frequency:
            raise ValueError("rANS frequency is zero for an input magnitude.")
        signs[index >> 3] |= (value >> 7) << (index & 7)
        maximum = ((RANS_L >> SCALE_BITS) << 8) * frequency
        while state >= maximum:
            emitted.append(state & 255)
            state >>= 8
        state = ((state // frequency) << SCALE_BITS) + state % frequency + cumulative[magnitude]
    emitted.reverse()
    return bytes(signs) + struct.pack("<I", state) + emitted


def decode_fp8(payload: bytes, size: int, frequencies: Sequence[int], *, force_python: bool = False) -> bytes:
    """Restore exact FP8 bytes, rejecting padding, truncation, and trailing data."""
    if not isinstance(payload, bytes):
        raise TypeError("rANS payload must be bytes.")
    if isinstance(size, bool):
        raise TypeError("rANS output size must be an integer.")
    size = operator.index(size)
    if not 0 <= size <= MAX_INPUT_BYTES:
        raise ValueError("rANS output size must be between 0 and 32 MiB.")
    if type(force_python) is not bool:
        raise TypeError("force_python must be a boolean.")
    frequencies = _validate_frequencies(frequencies)
    sign_bytes = (size + 7) // 8
    if not sign_bytes + 4 <= len(payload) <= sign_bytes + 2 * size + 4:
        raise ValueError("Invalid rANS payload length.")
    if size & 7 and payload[sign_bytes - 1] >> (size & 7):
        raise ValueError("Invalid rANS sign padding.")
    state = struct.unpack_from("<I", payload, sign_bytes)[0]
    if not RANS_L <= state < RANS_L << 8:
        raise ValueError("Invalid initial rANS state.")
    if _NATIVE is not None and not force_python:
        table = (ctypes.c_uint16 * 128)(*frequencies)
        output = ctypes.create_string_buffer(size)
        status = _NATIVE.ahps_rans_decode(payload, len(payload), size, table, 128, output, size)
        _check_status(status)
        return ctypes.string_at(output, size)

    cumulative = _cumulative(frequencies)
    symbols = []
    for symbol, frequency in enumerate(frequencies):
        symbols.extend([symbol] * frequency)
    restored = bytearray(size)
    position = sign_bytes + 4
    for index in range(size):
        slot = state & (SCALE_TOTAL - 1)
        magnitude = symbols[slot]
        sign = (payload[index >> 3] >> (index & 7)) & 1
        restored[index] = magnitude | (sign << 7)
        state = frequencies[magnitude] * (state >> SCALE_BITS) + slot - cumulative[magnitude]
        while state < RANS_L:
            if position == len(payload):
                raise ValueError("Truncated rANS payload.")
            state = (state << 8) | payload[position]
            position += 1
    if state != RANS_L or position != len(payload):
        raise ValueError("Invalid final rANS state or trailing data.")
    return bytes(restored)
