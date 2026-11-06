"""Bounded whole-file framing around the unchanged AHRL research codec.

This is a blind fixed-record adapter, not an executable parser.  The format
and its limits are specified in docs/relational-corpus-experiment.md.
"""

import lzma
import struct
import zlib

from . import relational as core


MAGIC = b"AHRF\x00\x00\x00\x01"
HEADER = struct.Struct("<8sBBBBIQII")
FRAME = struct.Struct("<II")
MAX_OUTPUT_BYTES = 128 * 1024**2
MAX_ARCHIVE_BYTES = 160 * 1024**2
MAX_FRAME_BYTES = 9 * 1024**2
MAX_FRAMES = 4096
DEFAULT_CHUNK_BYTES = 8388600
METHODS = ("raw", "auto")


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _binary(value, name, limit):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    size = value.nbytes if isinstance(value, memoryview) else len(value)
    if size > limit:
        raise ValueError(f"{name} exceeds the file-wrapper size limit")
    return bytes(value)


def _dimensions(width, fields, chunk_bytes):
    _integer(chunk_bytes, "chunk_bytes")
    if not 1 <= chunk_bytes <= core.MAX_OUTPUT_BYTES:
        raise ValueError("chunk size exceeds the AHRL record-region limit")
    # The core validates dimensions and divisibility without allocating data.
    return core._layout(width, fields, chunk_bytes).size


def _count(size, chunk_bytes):
    count = (size + chunk_bytes - 1) // chunk_bytes
    if count > MAX_FRAMES:
        raise ValueError("file-wrapper frame count exceeds the limit")
    return count


def encode(data, method="auto", width=4, fields=3,
           chunk_bytes=DEFAULT_CHUNK_BYTES, preset=6):
    """Encode every source byte, padding only its final partial record.

    RAW and AUTO use identical framing and record boundaries.  AUTO delegates
    complete-archive selection to the unmodified core for each chunk.
    """
    data = _binary(data, "data", MAX_OUTPUT_BYTES)
    record_bytes = _dimensions(width, fields, chunk_bytes)
    if not isinstance(method, str):
        raise TypeError("method must be a string")
    if method not in METHODS:
        raise ValueError("file-wrapper method must be raw or auto")
    _integer(preset, "preset")
    if preset < 0 or (preset & ~lzma.PRESET_EXTREME) not in range(10):
        raise ValueError("invalid LZMA preset")
    count = _count(len(data), chunk_bytes)
    output = bytearray(HEADER.pack(MAGIC, width, fields, 0, 0, chunk_bytes,
                                   len(data), count, zlib.crc32(data)))
    for offset in range(0, len(data), chunk_bytes):
        source = data[offset:offset + chunk_bytes]
        original_bytes = len(source)
        padding = (-original_bytes) % record_bytes
        if padding:
            source += bytes(padding)
        archive = core.encode(source, width, fields, method, preset=preset)
        if not 1 <= len(archive) <= MAX_FRAME_BYTES:
            raise ValueError("encoded frame exceeds the file-wrapper limit")
        if len(output) + FRAME.size + len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("encoded archive exceeds the file-wrapper limit")
        output.extend(FRAME.pack(original_bytes, len(archive)))
        output.extend(archive)
    return bytes(output)


def _parse(archive, max_output_bytes):
    archive = _binary(archive, "archive", MAX_ARCHIVE_BYTES)
    _integer(max_output_bytes, "max_output_bytes")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must be nonnegative")
    output_limit = min(max_output_bytes, MAX_OUTPUT_BYTES)
    if len(archive) < HEADER.size:
        raise ValueError("truncated file-wrapper header")
    magic, width, fields, reserved_a, reserved_b, chunk_bytes, size, count, crc = (
        HEADER.unpack_from(archive)
    )
    if magic != MAGIC or reserved_a or reserved_b:
        raise ValueError("invalid file-wrapper header")
    if size > output_limit:
        raise ValueError("declared file-wrapper output exceeds the limit")
    record_bytes = _dimensions(width, fields, chunk_bytes)
    if count != _count(size, chunk_bytes):
        raise ValueError("file-wrapper count does not match size/chunk declaration")
    # Parse all frame boundaries before decompressing or growing output.  Each
    # retained descriptor is small and the number of frames is bounded.
    frames = []
    position = HEADER.size
    remaining = size
    for index in range(count):
        if position + FRAME.size > len(archive):
            raise ValueError("missing or truncated file-wrapper frame header")
        original_bytes, stored_bytes = FRAME.unpack_from(archive, position)
        position += FRAME.size
        expected_bytes = min(chunk_bytes, remaining)
        if original_bytes != expected_bytes:
            raise ValueError("noncanonical file-wrapper frame length")
        if not 1 <= stored_bytes <= MAX_FRAME_BYTES:
            raise ValueError("stored frame length exceeds the limit")
        end = position + stored_bytes
        if end > len(archive):
            raise ValueError("truncated file-wrapper frame payload")
        padding = (-original_bytes) % record_bytes
        padded_bytes = original_bytes + padding
        frames.append((index, position, end, original_bytes, padded_bytes))
        remaining -= original_bytes
        position = end
    if position != len(archive) or remaining:
        raise ValueError("extra frames or trailing file-wrapper bytes")
    info = {
        "width": width,
        "fields": fields,
        "chunk_bytes": chunk_bytes,
        "source_bytes": size,
        "archive_bytes": len(archive),
        "frame_count": count,
        "crc32": crc,
        "frames": [],
    }
    return archive, frames, info


def _process(archive, max_output_bytes, collect_output):
    archive, frames, info = _parse(archive, max_output_bytes)
    output = bytearray() if collect_output else None
    checksum = 0
    restored_total = 0
    for index, start, end, original_bytes, padded_bytes in frames:
        # These owned internal helpers preserve all core XZ, expansion, model,
        # causality and CRC checks.  Decode each frame only once.
        payload, row_format, models, metadata = core._read_container(
            archive[start:end], padded_bytes
        )
        if (metadata["width"] != info["width"]
                or metadata["fields"] != info["fields"]
                or metadata["source_bytes"] != padded_bytes):
            raise ValueError("inner AHRL layout/length differs from file wrapper")
        restored = core._restore(payload, row_format, models, metadata)
        if any(restored[original_bytes:]):
            raise ValueError("file-wrapper final record padding is not zero")
        view = memoryview(restored)[:original_bytes]
        checksum = zlib.crc32(view, checksum)
        restored_total += len(view)
        if output is not None:
            output.extend(view)
        info["frames"].append({
            "index": index,
            "original_bytes": original_bytes,
            "padded_bytes": padded_bytes,
            "padding_bytes": padded_bytes - original_bytes,
            "archive_bytes": end - start,
            "metadata": metadata,
        })
    if restored_total != info["source_bytes"] or checksum != info["crc32"]:
        raise ValueError("file-wrapper restored length or checksum mismatch")
    return bytes(output) if output is not None else None, info


def decode(archive, max_output_bytes=MAX_OUTPUT_BYTES):
    """Validate and restore one file-wrapper archive without model search."""
    output, _ = _process(archive, max_output_bytes, collect_output=True)
    return output


def inspect_archive(archive, max_output_bytes=MAX_OUTPUT_BYTES):
    """Return frame/model metadata after validating every restored byte.

    The original candidate name is not serialized: equivalent candidates may
    produce identical inner archives.  Storage modes and descriptors record
    the exact representation that was actually selected.
    """
    _, info = _process(archive, max_output_bytes, collect_output=False)
    return info
