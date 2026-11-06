"""Bounded lossless weight-chunk experiment, separate from the AHPS format.

Run with the experiment's NumPy/zstandard environment. Each archive records
its transform, codec, original length, and SHA-256. Inputs are at most 32 MiB.
"""

import argparse
import bz2
import hashlib
import lzma
from pathlib import Path
import struct
import zlib

import brotli
import zstandard

from research.weight_transforms import METHODS, transform


MAGIC = b"AHWT\x01"
HEADER = struct.Struct("<5sBBI32s")
MAX_BYTES = 32 * 1024 * 1024
CODECS = ("xz3", "xz9", "zstd3", "zstd9", "zstd19", "zstd22", "bz2", "brotli", "deflate")


def compress_payload(data, codec, brotli_window=22):
    if codec.startswith("xz"):
        preset = int(codec[2:])
        if preset == 9:
            preset |= lzma.PRESET_EXTREME
        return lzma.compress(data, preset=preset)
    if codec.startswith("zstd"):
        return zstandard.ZstdCompressor(level=int(codec[4:]), write_checksum=True).compress(data)
    if codec == "bz2":
        return bz2.compress(data, compresslevel=9)
    if codec == "brotli":
        return brotli.compress(data, quality=11, lgwin=brotli_window)
    if codec == "deflate":
        return zlib.compress(data, level=9)
    raise ValueError("Unknown codec")


def decompress_payload(data, codec, expected):
    if codec.startswith("xz"):
        decoder = lzma.LZMADecompressor(memlimit=512 * 1024 * 1024)
        output = decoder.decompress(data, max_length=expected + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid XZ payload")
    elif codec.startswith("zstd"):
        if zstandard.frame_content_size(data) != expected:
            raise ValueError("Invalid Zstandard content length")
        context = zstandard.ZstdDecompressor(max_window_size=MAX_BYTES // 1024)
        if expected == 0:
            # The one-shot API can return early for empty frames without
            # validating their trailer. Consume the stream explicitly.
            decoder = context.decompressobj()
            output = decoder.decompress(data)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid empty Zstandard frame")
        else:
            output = context.decompress(data, max_output_size=expected, allow_extra_data=False)
    elif codec == "bz2":
        decoder = bz2.BZ2Decompressor()
        output = decoder.decompress(data, max_length=expected + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid bzip2 payload")
    elif codec == "brotli":
        decoder = brotli.Decompressor()
        parts = []
        count = 0
        for offset in range(0, len(data), 1024):
            piece = decoder.process(data[offset:offset + 1024])
            count += len(piece)
            if count > expected:
                raise ValueError("Brotli output exceeds declared size")
            parts.append(piece)
        if not decoder.is_finished():
            raise ValueError("Truncated Brotli payload")
        output = b"".join(parts)
    elif codec == "deflate":
        decoder = zlib.decompressobj()
        output = decoder.decompress(data, expected + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid DEFLATE payload")
    else:
        raise ValueError("Unknown codec")
    if len(output) != expected:
        raise ValueError("Decoded length does not match the archive")
    return output


def encode(data, method="raw", codec="xz9", *, brotli_window=22):
    if len(data) > MAX_BYTES:
        raise ValueError("Input exceeds the 32 MiB experiment limit")
    if brotli_window not in (22, 24) or (codec != "brotli" and brotli_window != 22):
        raise ValueError("Brotli window must be 22 or 24 and applies only to Brotli")
    header = HEADER.pack(MAGIC, METHODS.index(method), CODECS.index(codec), len(data),
                         hashlib.sha256(data).digest())
    return header + compress_payload(transform(data, method), codec, brotli_window)


def decode(archive):
    if not HEADER.size <= len(archive) <= MAX_BYTES + 1024 * 1024:
        raise ValueError("Invalid experimental archive size")
    magic, method, codec, size, digest = HEADER.unpack_from(archive)
    if magic != MAGIC or method >= len(METHODS) or codec >= len(CODECS) or size > MAX_BYTES:
        raise ValueError("Invalid experimental archive header")
    payload = decompress_payload(archive[HEADER.size:], CODECS[codec], size)
    restored = transform(payload, METHODS[method], inverse=True)
    if hashlib.sha256(restored).digest() != digest:
        raise ValueError("Restored SHA-256 does not match")
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("compress", "decompress"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--transform", choices=METHODS, default="raw")
    parser.add_argument("--codec", choices=CODECS, default="xz9")
    parser.add_argument("--brotli-window", type=int, choices=(22, 24), default=22)
    args = parser.parse_args()
    data = args.input.read_bytes()
    result = (encode(data, args.transform, args.codec, brotli_window=args.brotli_window)
              if args.operation == "compress" else decode(data))
    # Existing output files are never overwritten by this research command.
    with args.output.open("xb") as output:
        output.write(result)
    print(f"Wrote {len(result):,} bytes to {args.output}")


if __name__ == "__main__":
    main()
