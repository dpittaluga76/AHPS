"""Bounded, lossless research codec for exact relationships between record fields.

This independent format is specified in docs/relational-compression.md.  It is
not an AHPS archive or a production codec.  Models are selected by the encoder;
decoding uses only the compact, causal descriptors stored in the archive.
"""

from collections import Counter
from itertools import islice
import lzma
import math
import struct
import zlib


MAGIC = b"AHRL\x00\x00\x00\x01"
HEADER = struct.Struct("<8sBBBBQI")
DESCRIPTOR = struct.Struct("<BBQQ")
MAX_OUTPUT_BYTES = 8 * 1024**2
MAX_FIELDS = 16
XZ_MEMLIMIT = 128 * 1024**2
METHODS = ("raw", "shuffle", "delta", "independent", "cross", "adaptive")

RAW = 0
PREVIOUS = 1
AFFINE = 2
_WORD_FORMAT = {1: "B", 2: "H", 4: "I", 8: "Q"}


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _binary(value, name, limit=None):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    size = value.nbytes if isinstance(value, memoryview) else len(value)
    if limit is not None and size > limit:
        raise ValueError(f"{name} exceeds the research size limit")
    return bytes(value)


def _layout(width, fields, size):
    _integer(width, "width")
    _integer(fields, "fields")
    if width not in _WORD_FORMAT or not 1 <= fields <= MAX_FIELDS:
        raise ValueError("unsupported record dimensions")
    if size % (width * fields):
        raise ValueError("source length must be an exact record multiple")
    return struct.Struct("<" + _WORD_FORMAT[width] * fields)


def _shuffle(data, record_size):
    return b"".join(data[index::record_size] for index in range(record_size))


def _unshuffle(data, record_size):
    count = len(data) // record_size
    output = bytearray(len(data))
    for index in range(record_size):
        output[index::record_size] = data[index * count:(index + 1) * count]
    return output


def _prediction(model, current, previous, field):
    kind, source, coefficient, intercept = model
    if kind == RAW:
        return 0
    if kind == PREVIOUS:
        return previous[field]
    return coefficient * current[source] + intercept


def _coefficients(rows, target, source, mask):
    """Stable fixed candidates, then slopes from the first 16 eligible pairs."""
    result = []
    seen = set()
    for coefficient in (0, 1, -1, 2, -2, 3, 4, 8, 16):
        coefficient &= mask
        if coefficient not in seen:
            result.append(coefficient)
            seen.add(coefficient)
    eligible = 0
    for left, right in zip(rows, rows[1:]):
        difference = (right[source] - left[source]) & mask
        if not difference & 1:
            continue
        coefficient = ((right[target] - left[target]) *
                       pow(difference, -1, mask + 1)) & mask
        if coefficient not in seen:
            result.append(coefficient)
            seen.add(coefficient)
        eligible += 1
        if eligible == 16:
            break
    return result


def _candidate_models(rows, field, mask, independent):
    yield (RAW, 0, 0, 0)
    yield (PREVIOUS, 0, 0, 0)
    if not rows:
        return
    for source in range(field):
        for coefficient in _coefficients(rows, field, source, mask):
            if independent and coefficient != 0:
                continue
            differences = Counter(
                (row[field] - coefficient * row[source]) & mask for row in rows
            )
            highest = max(differences.values())
            intercept = min(value for value, count in differences.items()
                            if count == highest)
            yield (AFFINE, source, coefficient, intercept)


def _screen_bits(model, rows, previous, field, width, mask):
    if not rows:
        return 0.0
    histograms = [[0] * 256 for _ in range(width)]
    for row in rows:
        residual = (row[field] - _prediction(model, row, previous, field)) & mask
        for lane in range(width):
            histograms[lane][(residual >> (8 * lane)) & 255] += 1
        previous = row
    normalizer = len(rows) * math.log2(len(rows))
    return math.fsum(
        normalizer - math.fsum(count * math.log2(count) for count in histogram if count)
        for histogram in histograms
    )


def _adaptive_models(data, row_format, width, fields, independent=False):
    rows = list(islice(row_format.iter_unpack(data), 2048))
    fit = rows[:1024]
    score = rows[1024:]
    previous = fit[-1] if score else (0,) * fields
    if not score:
        score = fit
    mask = (1 << (8 * width)) - 1
    selected = []
    for field in range(fields):
        best = None
        best_bits = math.inf
        for model in _candidate_models(fit, field, mask, independent):
            bits = _screen_bits(model, score, previous, field, width, mask)
            if bits < best_bits:
                best, best_bits = model, bits
        selected.append(best)
    return selected


def _models_for(data, row_format, width, fields, method):
    if method == "delta":
        return [(PREVIOUS, 0, 0, 0)] * fields
    if method == "cross":
        return [(PREVIOUS, 0, 0, 0)] + [
            (AFFINE, field - 1, 1, 0) for field in range(1, fields)
        ]
    return _adaptive_models(data, row_format, width, fields,
                            independent=method == "independent")


def _residuals(data, row_format, width, fields, models):
    output = bytearray(len(data))
    mask = (1 << (8 * width)) - 1
    previous = (0,) * fields
    for index, row in enumerate(row_format.iter_unpack(data)):
        residuals = [
            (row[field] - _prediction(model, row, previous, field)) & mask
            for field, model in enumerate(models)
        ]
        row_format.pack_into(output, index * row_format.size, *residuals)
        previous = row
    return output


def _encode_fixed(data, width, fields, method, preset, row_format):
    if method == "raw":
        mode, table, payload = 0, b"", data
    elif method == "shuffle":
        mode, table, payload = 1, b"", _shuffle(data, row_format.size)
    else:
        models = _models_for(data, row_format, width, fields, method)
        mode = 2
        table = b"".join(DESCRIPTOR.pack(*model) for model in models)
        payload = _shuffle(_residuals(data, row_format, width, fields, models),
                           row_format.size)
    header = HEADER.pack(MAGIC, width, fields, mode, 0, len(data), zlib.crc32(data))
    return lzma.compress(header + table + payload, format=lzma.FORMAT_XZ,
                         check=lzma.CHECK_CRC64, preset=preset)


def encode(data, width, fields, method, preset=6):
    """Encode complete fixed-width records; ``auto`` compares all framed costs.

    The adaptive byte-histogram screen is only an encoder heuristic.  ``auto``
    evaluates complete XZ archives and includes model/header costs in selection.
    Its deterministic tie order is the order of ``METHODS``.
    """
    data = _binary(data, "data", MAX_OUTPUT_BYTES)
    row_format = _layout(width, fields, len(data))
    if not isinstance(method, str):
        raise TypeError("method must be a string")
    if method not in METHODS and method != "auto":
        raise ValueError("unknown research method")
    _integer(preset, "preset")
    if preset & ~lzma.PRESET_EXTREME not in range(10) or preset < 0:
        raise ValueError("invalid LZMA preset")
    if method != "auto":
        return _encode_fixed(data, width, fields, method, preset, row_format)
    best = None
    for candidate in METHODS:
        archive = _encode_fixed(data, width, fields, candidate, preset, row_format)
        if best is None or len(archive) < len(best):
            best = archive
    return best


def _read_container(archive, max_output_bytes):
    archive = _binary(archive, "archive")
    _integer(max_output_bytes, "max_output_bytes")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must be nonnegative")
    output_limit = min(max_output_bytes, MAX_OUTPUT_BYTES)
    container_limit = output_limit + HEADER.size + MAX_FIELDS * DESCRIPTOR.size
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMLIMIT)
        container = decoder.decompress(archive, max_length=container_limit + 1)
    except (lzma.LZMAError, EOFError) as error:
        raise ValueError("invalid research XZ stream") from error
    if len(container) > container_limit:
        raise ValueError("expanded research container exceeds the size limit")
    if not decoder.eof:
        raise ValueError("truncated or oversized research XZ stream")
    if decoder.unused_data:
        raise ValueError("trailing bytes or concatenated research XZ streams")
    if decoder.check != lzma.CHECK_CRC64:
        raise ValueError("research XZ streams must use CRC64")
    if len(container) < HEADER.size:
        raise ValueError("truncated research header")
    magic, width, fields, mode, reserved, size, checksum = HEADER.unpack_from(container)
    if magic != MAGIC or reserved != 0 or mode not in (0, 1, 2):
        raise ValueError("invalid research header")
    if size > output_limit:
        raise ValueError("declared research output exceeds the size limit")
    row_format = _layout(width, fields, size)
    table_size = fields * DESCRIPTOR.size if mode == 2 else 0
    payload_offset = HEADER.size + table_size
    if len(container) != payload_offset + size:
        raise ValueError("research container length does not match its declaration")
    models = []
    mask = (1 << (8 * width)) - 1
    if mode == 2:
        for field in range(fields):
            model = DESCRIPTOR.unpack_from(container, HEADER.size + field * DESCRIPTOR.size)
            kind, source, coefficient, intercept = model
            if kind in (RAW, PREVIOUS):
                if source or coefficient or intercept:
                    raise ValueError("noncanonical raw/previous descriptor")
            elif kind == AFFINE:
                if source >= field or coefficient > mask or intercept > mask:
                    raise ValueError("invalid causal affine descriptor")
            else:
                raise ValueError("unknown research model kind")
            models.append(model)
    metadata = {
        "width": width,
        "fields": fields,
        "records": size // row_format.size,
        "source_bytes": size,
        "storage_mode": mode,
        "models": [dict(zip(("kind", "source", "a", "b"), model)) for model in models],
        "crc32": checksum,
    }
    return container[payload_offset:], row_format, models, metadata


def _restore(payload, row_format, models, metadata):
    mode = metadata["storage_mode"]
    if mode == 0:
        output = payload
    elif mode == 1:
        output = _unshuffle(payload, row_format.size)
    else:
        residuals = _unshuffle(payload, row_format.size)
        output = bytearray(len(payload))
        fields = metadata["fields"]
        mask = (1 << (8 * metadata["width"])) - 1
        previous = (0,) * fields
        for index, row in enumerate(row_format.iter_unpack(residuals)):
            current = []
            for field, model in enumerate(models):
                value = (row[field] + _prediction(model, current, previous, field)) & mask
                current.append(value)
            row_format.pack_into(output, index * row_format.size, *current)
            previous = current
    if zlib.crc32(output) != metadata["crc32"]:
        raise ValueError("research restored-data checksum mismatch")
    return bytes(output)


def decode(archive, max_output_bytes=MAX_OUTPUT_BYTES):
    """Validate and restore one bounded research archive with no model search."""
    payload, row_format, models, metadata = _read_container(archive, max_output_bytes)
    return _restore(payload, row_format, models, metadata)


def inspect_archive(archive):
    """Return layout and model metadata only after complete archive validation."""
    payload, row_format, models, metadata = _read_container(archive, MAX_OUTPUT_BYTES)
    _restore(payload, row_format, models, metadata)
    return metadata
