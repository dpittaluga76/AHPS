"""Bounded selective word-delta experiment; see its frozen design document.

Both aligned controls use AHRH1 framing. The unchanged public AHPS encoder
supplies the original archive and actual plan once. Outside fragments are
transformed afresh: executable origins are rebased, while other predictor and
scanner history restarts at each new boundary. No existing residual is sliced.
"""
from __future__ import annotations

import dataclasses
import hashlib
import lzma
import struct
import zlib

import ahps


MAGIC = b"AHRH\x01"
HEADER = struct.Struct("<5sIIQI")
FILE_CAP = 128 * 1024**2
TABLE_CAP = 8 * 1024**2
COMPRESSED_CAP = 160 * 1024**2
SEGMENT_CAP = 65536
SEGMENT_TABLE_CAP = 20 * SEGMENT_CAP
XZ_MEMLIMIT = 128 * 1024**2
OP_WORD_DELTA32 = 10
PARAMS12 = b"\x0c\x00\x00\x00"
WORD_PROGRAM = ((OP_WORD_DELTA32, PARAMS12), (ahps.OP_SPLIT, PARAMS12))
BYTE_PROGRAM = ((ahps.OP_DELTA1_LANE, PARAMS12), (ahps.OP_SPLIT, PARAMS12))


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _binary(value, name, limit):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    size = value.nbytes if isinstance(value, memoryview) else len(value)
    if size > limit:
        raise ValueError(f"{name} exceeds the research limit")
    return bytes(value)


def _source_size(value):
    _integer(value, "source_bytes")
    if not 0 <= value <= FILE_CAP:
        raise ValueError("source size exceeds the research limit")
    return value


def _output_limit(value):
    _integer(value, "max_output_bytes")
    if value < 0:
        raise ValueError("max_output_bytes must be nonnegative")
    return min(value, FILE_CAP)


def _require_engine(use_c):
    if not isinstance(use_c, bool):
        raise TypeError("use_c must be boolean")
    if use_c and (ahps.SIMD_BRIDGE.lib is None
                  or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206):
        raise RuntimeError("Native ABI 206 required; no silent fallback")


def _preset(value, size):
    _integer(value, "preset")
    if value < 0 or value & ~lzma.PRESET_EXTREME not in range(10):
        raise ValueError("invalid LZMA preset")
    # Match the public baseline API's interpretation, including preset 9.
    return ahps.resolve_lzma_preset(value, size)


def _validate_plan(segments, source_bytes, allow_word=True):
    """Validate all descriptors before any transform or residual allocation."""
    _source_size(source_bytes)
    if not isinstance(segments, (list, tuple)):
        raise TypeError("segments must be a list or tuple")
    if len(segments) > SEGMENT_CAP:
        raise ValueError("too many segments")
    result, cursor, word_count, table_bytes = [], 0, 0, 0
    for segment in segments:
        if not isinstance(segment, ahps.Segment):
            raise TypeError("plan entries must be AHPS Segments")
        _integer(segment.start, "segment start")
        _integer(segment.length, "segment length")
        if (segment.start != cursor or not 1 <= segment.length <= FILE_CAP
                or cursor + segment.length > source_bytes):
            raise ValueError("segments must cover the source contiguously")
        if not isinstance(segment.program, (list, tuple)):
            raise TypeError("program must be a list or tuple")
        if len(segment.program) > ahps.MAX_PROGRAM_STEPS:
            raise ValueError("too many program steps")
        program = []
        for step in segment.program:
            if not isinstance(step, (list, tuple)) or len(step) != 2:
                raise ValueError("invalid transform descriptor")
            opcode, params = step
            _integer(opcode, "opcode")
            params = _binary(params, "parameters", 4)
            if len(params) != 4:
                raise ValueError("transform parameters must be exactly four bytes")
            if opcode not in ahps.STEP_OPCODES and not (allow_word and opcode == OP_WORD_DELTA32):
                raise ValueError("unknown or forbidden opcode")
            program.append((opcode, params))
        if any(op == OP_WORD_DELTA32 for op, _ in program):
            if (tuple(program) != WORD_PROGRAM or segment.length % 12
                    or segment.length > TABLE_CAP):
                raise ValueError("word delta requires the canonical complete-record program")
            word_count += 1
            if word_count > 1:
                raise ValueError("at most one word-delta segment is allowed")
        table_bytes += 5 + 5 * len(program)
        if table_bytes > SEGMENT_TABLE_CAP:
            raise ValueError("segment table exceeds the research limit")
        result.append(ahps.Segment(cursor, segment.length, program))
        cursor += segment.length
    if cursor != source_bytes:
        raise ValueError("segment coverage does not match source size")
    return result


def serializable_plan(segments):
    """Return actual starts, lengths and every serialized opcode/parameter."""
    if not isinstance(segments, (list, tuple)):
        raise TypeError("segments must be a list or tuple")
    validated = _validate_plan(segments, sum(segment.length for segment in segments))
    return [{"start": segment.start, "length": segment.length,
             "program": [{"opcode": opcode, "params_hex": params.hex()}
                         for opcode, params in segment.program]} for segment in validated]


def _declared_header(head, output_limit):
    if len(head) != HEADER.size:
        raise ValueError("truncated research header")
    magic, count, table_bytes, source_bytes, crc = HEADER.unpack(head)
    if magic not in (ahps.MAGIC_HEADER, MAGIC):
        raise ValueError("only AHPS v5 and AHRH1 are supported")
    if count > SEGMENT_CAP or table_bytes > SEGMENT_TABLE_CAP:
        raise ValueError("declared segment count/table exceeds the research limit")
    if source_bytes > output_limit:
        raise ValueError("declared restored output exceeds the research limit")
    if not 5 * count <= table_bytes <= 20 * count:
        raise ValueError("declared segment table length is inconsistent")
    if (source_bytes == 0) != (count == 0) or count > source_bytes:
        raise ValueError("declared segment count cannot cover the source")
    return magic, count, table_bytes, source_bytes, crc


def _take(decoder, length):
    parts, size = [], 0
    while size < length:
        if decoder.eof:
            raise ValueError("truncated container")
        piece = decoder.decompress(b"", max_length=length - size)
        if not piece:
            raise ValueError("truncated container")
        parts.append(piece)
        size += len(piece)
    return b"".join(parts)


def _parse_table(table, count, source_bytes, allow_word):
    segments, cursor, position = [], 0, 0
    for _ in range(count):
        if position + 5 > len(table):
            raise ValueError("truncated segment descriptor")
        length, steps = struct.unpack_from("<IB", table, position)
        position += 5
        if steps > ahps.MAX_PROGRAM_STEPS or position + 5 * steps > len(table):
            raise ValueError("invalid program length")
        program = []
        for _ in range(steps):
            program.append((table[position], table[position + 1:position + 5]))
            position += 5
        segments.append(ahps.Segment(cursor, length, program))
        cursor += length
    if position != len(table):
        raise ValueError("unused segment table bytes")
    return _validate_plan(segments, source_bytes, allow_word=allow_word)


def _read_container(archive, max_output_bytes):
    output_limit = _output_limit(max_output_bytes)
    archive = _binary(archive, "archive", COMPRESSED_CAP)
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMLIMIT)
        head = decoder.decompress(archive, max_length=HEADER.size)
        magic, count, table_bytes, source_bytes, crc = _declared_header(head, output_limit)
        if decoder.check != lzma.CHECK_CRC64:
            raise ValueError("research streams require CRC64")
        table = _take(decoder, table_bytes)
        segments = _parse_table(table, count, source_bytes, allow_word=magic == MAGIC)
        # All declarations and descriptors have been checked before requesting
        # any residual output; one extra byte detects a lying source length.
        residual = _take(decoder, source_bytes)
        if not decoder.eof:
            if decoder.decompress(b"", max_length=1):
                raise ValueError("actual container expansion exceeds its declaration")
            if not decoder.eof:
                raise ValueError("truncated XZ stream")
        if decoder.unused_data:
            raise ValueError("trailing bytes or concatenated XZ streams")
    except (lzma.LZMAError, EOFError) as exc:
        raise ValueError("invalid research XZ stream") from exc
    metadata = {"format": "AHPS5" if magic == ahps.MAGIC_HEADER else "AHRH1",
                "magic_hex": magic.hex(), "source_bytes": source_bytes,
                "segment_count": count, "segment_table_bytes": table_bytes,
                "container_bytes": HEADER.size + table_bytes + source_bytes,
                "crc32": crc, "crc32_verified": False,
                "word_segments": sum(any(op == OP_WORD_DELTA32 for op, _ in seg.program)
                                     for seg in segments),
                "segments": serializable_plan(segments)}
    return residual, segments, metadata


def _word_delta(data, inverse=False):
    if not data or len(data) % 12 or len(data) > TABLE_CAP:
        raise ValueError("word delta needs bounded complete 12-byte records")
    output = bytearray(len(data))
    previous = (0, 0, 0)
    for index, row in enumerate(struct.iter_unpack("<III", data)):
        if inverse:
            current = tuple((row[field] + previous[field]) & 0xFFFFFFFF for field in range(3))
            struct.pack_into("<III", output, index * 12, *current)
            previous = current
        else:
            current = tuple((row[field] - previous[field]) & 0xFFFFFFFF for field in range(3))
            struct.pack_into("<III", output, index * 12, *current)
            previous = row
    return bytes(output)


def _transform(data, program, use_c, inverse=False):
    steps = reversed(program) if inverse else program
    for opcode, params in steps:
        if opcode == OP_WORD_DELTA32:
            data = _word_delta(data, inverse=inverse)
        else:
            data = ahps.lift_step(opcode, params, data, use_c, inverse=inverse)
    return data


def _restore(residual, segments, metadata, use_c):
    _require_engine(use_c)
    output = bytearray(residual)
    for segment in segments:
        if segment.program:
            start, end = segment.start, segment.start + segment.length
            output[start:end] = _transform(bytes(residual[start:end]), segment.program, use_c, inverse=True)
    if zlib.crc32(output) != metadata["crc32"]:
        raise ValueError("restored complete-file CRC32 mismatch")
    return bytes(output)


def decode(archive, use_c=True, max_output_bytes=FILE_CAP):
    """Restore only recorded transforms after bounded structural validation."""
    residual, segments, metadata = _read_container(archive, max_output_bytes)
    return _restore(residual, segments, metadata, use_c)


def inspect_archive(archive, max_output_bytes=FILE_CAP, verify_crc=False, use_c=True):
    """Inspect bounded structure; optionally restore and validate source CRC32."""
    if not isinstance(verify_crc, bool):
        raise TypeError("verify_crc must be boolean")
    residual, segments, metadata = _read_container(archive, max_output_bytes)
    if verify_crc:
        _restore(residual, segments, metadata, use_c)
        metadata["crc32_verified"] = True
    return metadata


def baseline_archive(data, use_c=True, preset=6):
    """Call the unchanged public AHPS compressor exactly once."""
    data = _binary(data, "data", FILE_CAP)
    _require_engine(use_c)
    _preset(preset, len(data))
    return ahps.ahps_compress_bytes(data, block_size=4096, force_python=not use_c,
                                    lzma_preset=preset, search=ahps.FAST_CHUNKS)


def baseline_plan(archive, max_output_bytes=FILE_CAP):
    """Recover the actual winning AHPS v5 descriptors; never run a planner."""
    _, segments, metadata = _read_container(archive, max_output_bytes)
    if metadata["format"] != "AHPS5":
        raise ValueError("baseline plan must come from an AHPS v5 archive")
    return segments


def _table_range(source_bytes, table_start, table_length):
    _source_size(source_bytes)
    _integer(table_start, "table_start")
    _integer(table_length, "table_length")
    if (table_start < 0 or not 0 < table_length <= TABLE_CAP or table_length % 12
            or table_start + table_length > source_bytes):
        raise ValueError("table must be complete bounded records within the source")
    return table_start, table_start + table_length


def aligned_plan(segments, source_bytes, table_start, table_length, word=False):
    """Clip original descriptors, rebase executable origins, insert one table."""
    if not isinstance(word, bool):
        raise TypeError("word must be boolean")
    begin, end = _table_range(source_bytes, table_start, table_length)
    original = _validate_plan(segments, source_bytes, allow_word=False)
    output = []

    def fragment(segment, start, stop):
        if start >= stop:
            return
        program = []
        for opcode, params in segment.program:
            if opcode in (ahps.OP_EXE32, ahps.OP_EXE_SPLIT):
                params = ((int.from_bytes(params, "big") + start - segment.start)
                          & 0xFFFFFFFF).to_bytes(4, "big")
            program.append((opcode, params))
        output.append(ahps.Segment(start, stop - start, program))

    for segment in original:
        fragment(segment, segment.start, min(segment.start + segment.length, begin))
    output.append(ahps.Segment(begin, table_length, list(WORD_PROGRAM if word else BYTE_PROGRAM)))
    for segment in original:
        fragment(segment, max(segment.start, end), segment.start + segment.length)
    return _validate_plan(output, source_bytes)


def encode_plan(data, segments, use_c=True, preset=6):
    """Recompute a complete plan from source bytes into one solid AHRH1 XZ."""
    data = _binary(data, "data", FILE_CAP)
    segments = _validate_plan(segments, len(data))
    _require_engine(use_c)
    preset = _preset(preset, len(data))
    table = bytearray()
    for segment in segments:
        table.extend(struct.pack("<IB", segment.length, len(segment.program)))
        for opcode, params in segment.program:
            table.append(opcode)
            table.extend(params)
    prefix = HEADER.pack(MAGIC, len(segments), len(table), len(data), zlib.crc32(data)) + table
    container = bytearray(len(prefix) + len(data))
    container[:len(prefix)] = prefix
    for segment in segments:
        start, end = segment.start, segment.start + segment.length
        container[len(prefix) + start:len(prefix) + end] = _transform(data[start:end], segment.program, use_c)
    archive = lzma.compress(container, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=preset)
    if len(archive) > COMPRESSED_CAP:
        raise ValueError("encoded archive exceeds the research limit")
    return archive


def select_archive(baseline, byte, word):
    """Select by complete size, preserving original bytes and ordered ties."""
    choices = [("baseline", _binary(baseline, "baseline", COMPRESSED_CAP))]
    if byte is not None:
        choices.append(("byte", _binary(byte, "byte candidate", COMPRESSED_CAP)))
    if word is not None:
        choices.append(("word", _binary(word, "word candidate", COMPRESSED_CAP)))
    name, archive = min(choices, key=lambda choice: len(choice[1]))
    return archive, name


def encode(data, table_start=None, table_length=None, use_c=True, preset=6):
    """Build baseline once and two aligned candidates; propagate any failure."""
    data = _binary(data, "data", FILE_CAP)
    if (table_start is None) != (table_length is None):
        raise ValueError("table_start and table_length must be supplied together")
    if table_start is not None:
        _table_range(len(data), table_start, table_length)
    original, report = baseline_archive(data, use_c=use_c, preset=preset)
    plan = baseline_plan(original, max_output_bytes=len(data))
    candidates = {"baseline": original}
    plans = {"baseline": serializable_plan(plan)}
    if table_start is not None:
        for name, word in (("byte", False), ("word", True)):
            candidate_plan = aligned_plan(plan, len(data), table_start, table_length, word=word)
            candidates[name] = encode_plan(data, candidate_plan, use_c=use_c, preset=preset)
            plans[name] = serializable_plan(candidate_plan)
    chosen, name = select_archive(original, candidates.get("byte"), candidates.get("word"))
    metadata = {"selected_name": name, "candidate_bytes": {key: len(value) for key, value in candidates.items()},
                "plans": plans, "table": None if table_start is None else {"start": table_start, "length": table_length},
                "source_bytes": len(data), "source_sha256": hashlib.sha256(data).hexdigest(),
                "archive_bytes": len(chosen), "archive_sha256": hashlib.sha256(chosen).hexdigest(),
                "baseline_report": dataclasses.asdict(report)}
    return chosen, metadata
