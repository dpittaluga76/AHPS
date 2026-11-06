"""Byte-exact CSV column transposition for the AHPS v6 container."""

from array import array
import re
import struct
import sys
import zlib
from typing import BinaryIO, Optional, Union


MAGIC = b"AHPS\x06"
HEADER = struct.Struct("<5sIIQI")
MAX_FIELDS = 1_000_000
MAX_COLUMNS = 1024
HEADER_SCAN_LIMIT = 64 * 1024
UTF8_BOM = b"\xef\xbb\xbf"
FIELD_END = re.compile(rb'[,"\r\n]')
HEADER_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def pack_csv_container(data: bytes) -> Optional[bytes]:
    """Return a column-major container, or None for unsupported CSV input.

    Every span retains its quotes and following comma or record terminator.
    Only a consistent comma-separated table with at least two rows and columns
    is eligible. The first logical row must fit in the bounded header probe.
    """
    size = len(data)
    if not size:
        return None
    spans = []
    lengths = array("I")
    position = len(UTF8_BOM) if data.startswith(UTF8_BOM) else 0
    field_start = 0
    row_fields = 0
    columns = 0
    rows = 0
    limit = min(size, HEADER_SCAN_LIMIT)

    while True:
        if len(lengths) >= MAX_FIELDS or row_fields >= MAX_COLUMNS:
            return None
        if position < size and data[position] == 34:
            position += 1
            while True:
                quote = data.find(b'"', position, limit)
                if quote < 0:
                    return None
                position = quote + 1
                if position < limit and data[position] == 34:
                    position += 1
                    continue
                break
        else:
            match = FIELD_END.search(data, position, limit)
            position = match.start() if match else limit
            if position < size and data[position] == 34:
                return None

        if position == limit and limit < size:
            return None
        delimiter = data[position:position + 1]
        if delimiter == b",":
            position += 1
        elif delimiter == b"\r":
            position += 1
            if data[position:position + 1] == b"\n":
                position += 1
        elif delimiter == b"\n":
            position += 1
        elif delimiter:
            return None

        length = position - field_start
        if length > 0xFFFFFFFF:
            return None
        spans.append((field_start, position))
        lengths.append(length)
        field_start = position
        row_fields += 1
        if delimiter == b",":
            continue

        if not columns:
            columns = row_fields
            if columns < 2 or HEADER_CONTROL.search(data, 0, position):
                return None
            limit = size
        elif row_fields != columns:
            return None
        rows += 1
        row_fields = 0
        if position == size:
            break

    if rows < 2:
        return None
    if sys.byteorder != "little":
        lengths.byteswap()
    table = struct.pack("<I", columns) + lengths.tobytes()
    container = bytearray(HEADER.pack(MAGIC, rows, len(table), size,
                                     zlib.crc32(data) & 0xFFFFFFFF))
    container.extend(table)
    source = memoryview(data)
    for column in range(columns):
        for start, end in spans[column::columns]:
            container.extend(source[start:end])
    return bytes(container)


def restore_csv_container(raw: Union[bytes, bytearray], out: BinaryIO) -> int:
    """Validate and restore a v6 container to the caller's provisional output."""
    if len(raw) < HEADER.size + 4:
        raise ValueError("Corrupt AHPS CSV archive: truncated header.")
    magic, rows, table_len, original_len, expected_crc = HEADER.unpack_from(raw)
    if magic != MAGIC:
        raise ValueError("Corrupt AHPS CSV archive: invalid magic.")
    if HEADER.size + table_len + original_len != len(raw):
        raise ValueError("Corrupt AHPS CSV archive: inconsistent container length.")
    columns = struct.unpack_from("<I", raw, HEADER.size)[0]
    fields = rows * columns
    if rows < 2 or not 2 <= columns <= MAX_COLUMNS or fields > MAX_FIELDS:
        raise ValueError("Corrupt AHPS CSV archive: invalid table dimensions.")
    if table_len != 4 + fields * 4:
        raise ValueError("Corrupt AHPS CSV archive: invalid length table.")

    payload_start = HEADER.size + table_len
    lengths = array("I")
    lengths.frombytes(memoryview(raw)[HEADER.size + 4:payload_start])
    if sys.byteorder != "little":
        lengths.byteswap()
    if sum(lengths) != original_len:
        raise ValueError("Corrupt AHPS CSV archive: field lengths do not match output.")

    positions = []
    position = payload_start
    for column in range(columns):
        positions.append(position)
        position += sum(lengths[column::columns])

    source = memoryview(raw)
    crc = 0
    for index, length in enumerate(lengths):
        column = index % columns
        start = positions[column]
        span = source[start:start + length]
        out.write(span)
        crc = zlib.crc32(span, crc)
        positions[column] += length
    if crc & 0xFFFFFFFF != expected_crc:
        raise ValueError("Corrupt AHPS CSV archive: restored data fails its CRC-32 check.")
    return original_len
