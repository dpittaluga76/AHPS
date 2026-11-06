"""Exact tensor-aware representations for the AHPS v7 container.

The original safetensors header is preserved as literal bytes. F32 tensors with
zero low 16 bits store their upper bytes as two planes. F8_E4M3 tensors store a
128-entry little-endian uint16 magnitude frequency table followed by the rANS
payload supplied by ahps_rans. Every other supported dtype remains literal.
"""

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import zlib
from typing import BinaryIO, Optional, Tuple, Union

import ahps_rans


MAGIC = b"AHPS\x07"
HEADER = struct.Struct("<5sIIQI")
SEGMENT = struct.Struct("<BII")
FREQUENCIES = struct.Struct("<128H")
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024 * 1024
MAX_SEGMENTS = 65536
FREQUENCY_TOTAL = 4096
RAW = 0
F32_LOW16_ZERO = 1
F8_RANS = 2
DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
    "F8_E4M3FN": 1, "F8_E4M3FNUZ": 1, "F8_E5M2FNUZ": 1, "F8_E8M0": 1,
    "U16": 2, "I16": 2, "F16": 2, "BF16": 2,
    "U32": 4, "I32": 4, "F32": 4,
    "U64": 8, "I64": 8, "F64": 8,
}


@dataclass(frozen=True)
class TensorSpan:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    start: int
    end: int


@dataclass(frozen=True)
class TensorLayout:
    source_size: int
    data_start: int
    tensors: Tuple[TensorSpan, ...]


def validate_layout(layout: TensorLayout) -> None:
    """Validate parsed and caller-constructed layouts before inspecting ranges."""
    if not isinstance(layout, TensorLayout):
        raise TypeError("Tensor layout must be a TensorLayout.")
    if (type(layout.source_size) is not int or type(layout.data_start) is not int
            or not 0 <= layout.data_start <= layout.source_size):
        raise ValueError("Invalid tensor layout: source bounds must be ordered nonnegative integers.")
    if not isinstance(layout.tensors, tuple):
        raise TypeError("Tensor layout spans must be a tuple.")
    position = layout.data_start
    names = set()
    for tensor in layout.tensors:
        if not isinstance(tensor, TensorSpan):
            raise TypeError("Tensor layout entries must be TensorSpan objects.")
        if not isinstance(tensor.name, str) or tensor.name in names:
            raise ValueError("Invalid tensor layout: tensor names must be unique strings.")
        names.add(tensor.name)
        if (type(tensor.start) is not int or type(tensor.end) is not int
                or tensor.start != position or not tensor.start <= tensor.end <= layout.source_size):
            raise ValueError("Invalid tensor layout: tensor ranges must cover the payload in order.")
        if not isinstance(tensor.dtype, str) or tensor.dtype not in DTYPE_BYTES:
            raise ValueError("Invalid tensor layout: unsupported dtype width.")
        if (not isinstance(tensor.shape, tuple)
                or any(type(value) is not int or value < 0 for value in tensor.shape)):
            raise ValueError("Invalid tensor layout: tensor dimensions must be nonnegative integers.")
        elements = 1
        for dimension in tensor.shape:
            elements *= dimension
        if elements * DTYPE_BYTES[tensor.dtype] != tensor.end - tensor.start:
            raise ValueError("Invalid tensor layout: shape does not match tensor bytes.")
        position = tensor.end
    if position != layout.source_size:
        raise ValueError("Invalid tensor layout: tensor ranges do not cover the payload.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid safetensors header: duplicate JSON key.")
        result[key] = value
    return result


def parse_layout(source: Union[str, Path]) -> TensorLayout:
    """Validate a safetensors header and ranges without reading tensor payloads."""
    with Path(source).open("rb") as stream:
        source_size = os.fstat(stream.fileno()).st_size
        length_bytes = stream.read(8)
        if len(length_bytes) != 8:
            raise ValueError("Invalid safetensors file: missing header length.")
        header_len = struct.unpack("<Q", length_bytes)[0]
        if not 2 <= header_len <= MAX_HEADER_BYTES or header_len > source_size - 8:
            raise ValueError("Invalid safetensors file: header length exceeds bounds.")
        header_bytes = stream.read(header_len)
        if len(header_bytes) != header_len:
            raise ValueError("Invalid safetensors file: truncated header.")
        if not header_bytes.startswith(b"{"):
            raise ValueError("Invalid safetensors file: header must start with an object.")
    try:
        header = json.loads(header_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid safetensors file: malformed JSON header.") from exc
    if not isinstance(header, dict):
        raise ValueError("Invalid safetensors header: expected an object.")

    data_start = 8 + header_len
    payload_size = source_size - data_start
    tensors = []
    for name, entry in header.items():
        if name == "__metadata__":
            if not isinstance(entry, dict) or any(not isinstance(v, str) for v in entry.values()):
                raise ValueError("Invalid safetensors header: metadata must contain strings.")
            continue
        if not isinstance(entry, dict):
            raise ValueError("Invalid safetensors header: malformed tensor entry.")
        dtype = entry.get("dtype")
        shape = entry.get("shape")
        offsets = entry.get("data_offsets")
        if not isinstance(dtype, str) or dtype not in DTYPE_BYTES:
            raise ValueError("Invalid safetensors header: unsupported dtype width.")
        if not isinstance(shape, list) or any(type(v) is not int or v < 0 for v in shape):
            raise ValueError("Invalid safetensors header: invalid tensor shape.")
        if not isinstance(offsets, list) or len(offsets) != 2 or any(type(v) is not int for v in offsets):
            raise ValueError("Invalid safetensors header: invalid tensor offsets.")
        start, end = offsets
        if not 0 <= start <= end <= payload_size:
            raise ValueError("Invalid safetensors header: tensor offsets exceed the file.")
        elements = 1
        for dimension in shape:
            elements *= dimension
        if elements * DTYPE_BYTES[dtype] != end - start:
            raise ValueError("Invalid safetensors header: shape does not match tensor bytes.")
        tensors.append(TensorSpan(name, dtype, tuple(shape), data_start + start, data_start + end))

    tensors.sort(key=lambda tensor: (tensor.start, tensor.end))
    layout = TensorLayout(source_size, data_start, tuple(tensors))
    validate_layout(layout)
    return layout


def _ranges(layout: TensorLayout, offset: int, length: int):
    """Yield absolute byte ranges, aligning F32 spans to their original tensor."""
    position = offset
    end = offset + length
    for tensor in layout.tensors:
        if tensor.end <= position:
            continue
        if tensor.start >= end:
            break
        if position < tensor.start:
            yield "RAW", position, tensor.start
            position = tensor.start
        stop = min(tensor.end, end)
        if position == stop:
            continue
        if tensor.dtype == "F32":
            aligned_start = min(stop, position + (-(position - tensor.start) % 4))
            aligned_end = max(aligned_start, stop - ((stop - tensor.start) % 4))
            if position < aligned_start:
                yield "RAW", position, aligned_start
            if aligned_start < aligned_end:
                yield "F32", aligned_start, aligned_end
            if aligned_end < stop:
                yield "RAW", aligned_end, stop
        else:
            yield tensor.dtype, position, stop
        position = stop
    if position < end:
        yield "RAW", position, end


def _frequencies(data: bytes):
    signed_counts = Counter(data)
    counts = [signed_counts[symbol] + signed_counts[symbol + 128] for symbol in range(128)]
    observed = [symbol for symbol, count in enumerate(counts) if count]
    size = len(data)
    frequencies = [max(1, count * FREQUENCY_TOTAL // size) if count else 0 for count in counts]
    residuals = [count * FREQUENCY_TOTAL - frequency * size
                 for count, frequency in zip(counts, frequencies)]
    total = sum(frequencies)
    while total < FREQUENCY_TOTAL:
        symbol = max(observed, key=lambda value: residuals[value])
        frequencies[symbol] += 1
        residuals[symbol] -= size
        total += 1
    while total > FREQUENCY_TOTAL:
        eligible = [symbol for symbol in observed if frequencies[symbol] > 1]
        symbol = min(eligible, key=lambda value: residuals[value])
        frequencies[symbol] -= 1
        residuals[symbol] += size
        total -= 1
    return frequencies


def pack_tensor_container(data: bytes, layout: TensorLayout, source_offset: int = 0,
                          force_python: bool = False) -> Optional[bytes]:
    """Pack a source slice using its original tensor boundaries, or return None."""
    validate_layout(layout)
    if len(data) > MAX_CHUNK_BYTES:
        raise ValueError("Tensor input exceeds the 32 MiB chunk limit.")
    if type(source_offset) is not int or source_offset < 0 or source_offset + len(data) > layout.source_size:
        raise ValueError("Tensor input range exceeds the source layout.")
    segments = []
    changed = False
    for dtype, start, end in _ranges(layout, source_offset, len(data)):
        original = data[start - source_offset:end - source_offset]
        mode = RAW
        payload = original
        if dtype == "F32":
            words = len(original) // 4
            if original[0::4].count(0) == words and original[1::4].count(0) == words:
                mode = F32_LOW16_ZERO
                payload = original[2::4] + original[3::4]
        elif dtype == "F8_E4M3" and len(original) > FREQUENCIES.size:
            frequencies = _frequencies(original)
            candidate = FREQUENCIES.pack(*frequencies) + ahps_rans.encode_fp8(
                original, frequencies, force_python=force_python)
            if len(candidate) < len(original):
                mode = F8_RANS
                payload = candidate
        segments.append((mode, len(original), payload))
        changed |= mode != RAW
        if len(segments) > MAX_SEGMENTS:
            return None
    if not changed:
        return None
    table = b"".join(SEGMENT.pack(mode, length, len(payload)) for mode, length, payload in segments)
    return (HEADER.pack(MAGIC, len(segments), len(table), len(data), zlib.crc32(data) & 0xFFFFFFFF)
            + table + b"".join(payload for _, _, payload in segments))


def restore_tensor_container(raw: Union[bytes, bytearray], out: BinaryIO,
                             force_python: bool = False) -> int:
    """Validate a v7 container and restore it to the caller's provisional output."""
    if len(raw) < HEADER.size:
        raise ValueError("Corrupt AHPS tensor archive: truncated header.")
    magic, count, table_len, original_len, expected_crc = HEADER.unpack_from(raw)
    if magic != MAGIC or not 1 <= count <= MAX_SEGMENTS:
        raise ValueError("Corrupt AHPS tensor archive: invalid header.")
    if original_len > MAX_CHUNK_BYTES:
        raise ValueError("Corrupt AHPS tensor archive: output exceeds the chunk limit.")
    if table_len != count * SEGMENT.size or HEADER.size + table_len > len(raw):
        raise ValueError("Corrupt AHPS tensor archive: invalid segment table.")
    segments = []
    output_total = 0
    payload_total = 0
    for index in range(count):
        mode, length, payload_len = SEGMENT.unpack_from(raw, HEADER.size + index * SEGMENT.size)
        if not 1 <= length <= MAX_CHUNK_BYTES:
            raise ValueError("Corrupt AHPS tensor archive: invalid segment length.")
        if mode == RAW:
            valid = payload_len == length
        elif mode == F32_LOW16_ZERO:
            valid = length % 4 == 0 and payload_len == length // 2
        elif mode == F8_RANS:
            valid = FREQUENCIES.size + 4 <= payload_len < length
        else:
            valid = False
        if not valid:
            raise ValueError("Corrupt AHPS tensor archive: invalid segment representation.")
        segments.append((mode, length, payload_len))
        output_total += length
        payload_total += payload_len
    if output_total != original_len or HEADER.size + table_len + payload_total != len(raw):
        raise ValueError("Corrupt AHPS tensor archive: inconsistent section lengths.")

    position = HEADER.size + table_len
    crc = 0
    source = memoryview(raw)
    for mode, length, payload_len in segments:
        payload = source[position:position + payload_len]
        if mode == RAW:
            restored = payload
        elif mode == F32_LOW16_ZERO:
            restored = bytearray(length)
            words = length // 4
            restored[2::4] = payload[:words]
            restored[3::4] = payload[words:]
        else:
            frequencies = FREQUENCIES.unpack_from(payload)
            if sum(frequencies) != FREQUENCY_TOTAL:
                raise ValueError("Corrupt AHPS tensor archive: invalid magnitude frequencies.")
            restored = ahps_rans.decode_fp8(bytes(payload[FREQUENCIES.size:]), length,
                                          frequencies, force_python=force_python)
            if len(restored) != length:
                raise ValueError("Corrupt AHPS tensor archive: decoded length mismatch.")
        out.write(restored)
        crc = zlib.crc32(restored, crc)
        position += payload_len
    if crc & 0xFFFFFFFF != expected_crc:
        raise ValueError("Corrupt AHPS tensor archive: restored data fails its CRC-32 check.")
    return original_len
