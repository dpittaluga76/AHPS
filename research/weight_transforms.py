"""Exact, length-preserving byte transforms for model-weight experiments.

``raw`` copies the input. ``bitplanes`` processes independent 64 KiB blocks.
Within each block, it writes all bit-0 values, then bit-1 values, through bit 7.
Bits are ordered by their original byte positions within each plane, and the
first bit in every packed output byte occupies its least significant position.
``bitplanes_full`` applies the same packing to the entire aligned input.

``fp8_fields`` also processes independent 64 KiB blocks. Its three streams are
bit 7 of every byte, bits 3 through 6 of each byte in row order, and bits 0
through 2 of each byte in row order. Each stream uses little-endian bit packing.
``fp8_fields_full`` uses the entire aligned input as one block. These transforms
operate on byte patterns and do not interpret or modify floating-point values.

``delta_bitplanes`` first stores the initial byte of each 64 KiB block verbatim
and replaces every subsequent byte with its difference from the previous byte,
modulo 256. It then applies the same bit-plane packing as ``bitplanes``.

All processed blocks contain a multiple of eight bytes. Any final group of
fewer than eight bytes is copied verbatim by every method. Input is limited to
32 MiB to bound NumPy's unpacked intermediate arrays.
"""

import numpy as np


BLOCK_SIZE = 64 * 1024
MAX_INPUT_SIZE = 32 * 1024 * 1024
METHODS = (
    "raw",
    "bitplanes",
    "bitplanes_full",
    "fp8_fields",
    "fp8_fields_full",
    "delta_bitplanes",
)


def _bitplanes(block: np.ndarray, inverse: bool) -> np.ndarray:
    bits = np.unpackbits(block, bitorder="little")
    if inverse:
        ordered = bits.reshape(8, len(block)).T
    else:
        ordered = bits.reshape(len(block), 8).T
    return np.packbits(ordered.reshape(-1), bitorder="little")


def _fp8_fields(block: np.ndarray, inverse: bool) -> np.ndarray:
    size = len(block)
    if inverse:
        sign_end = size // 8
        exponent_end = sign_end + size // 2
        bits = np.empty((size, 8), dtype=np.uint8)
        bits[:, 7] = np.unpackbits(block[:sign_end], bitorder="little")
        bits[:, 3:7] = np.unpackbits(
            block[sign_end:exponent_end], bitorder="little"
        ).reshape(size, 4)
        bits[:, :3] = np.unpackbits(
            block[exponent_end:], bitorder="little"
        ).reshape(size, 3)
        return np.packbits(bits.reshape(-1), bitorder="little")

    bits = np.unpackbits(block, bitorder="little").reshape(size, 8)
    return np.concatenate((
        np.packbits(bits[:, 7], bitorder="little"),
        np.packbits(bits[:, 3:7].reshape(-1), bitorder="little"),
        np.packbits(bits[:, :3].reshape(-1), bitorder="little"),
    ))


def transform(data: bytes, method: str, inverse: bool = False) -> bytes:
    """Apply or invert a named transform, preserving length and trailing bytes."""
    if not isinstance(data, bytes):
        raise TypeError("Weight transforms require bytes input.")
    if method not in METHODS:
        raise ValueError(f"Unknown weight transform: {method!r}.")
    if len(data) > MAX_INPUT_SIZE:
        raise ValueError("Weight transform input exceeds the 32 MiB limit.")
    aligned_size = len(data) - len(data) % 8
    if method == "raw" or not aligned_size:
        return data

    block_size = aligned_size if method.endswith("_full") else BLOCK_SIZE
    source = np.frombuffer(data, dtype=np.uint8)
    result = bytearray()
    for start in range(0, aligned_size, block_size):
        block = source[start:min(start + block_size, aligned_size)]
        if method.startswith("fp8_fields"):
            transformed = _fp8_fields(block, inverse)
        elif method == "delta_bitplanes":
            if inverse:
                differences = _bitplanes(block, inverse=True)
                transformed = np.cumsum(differences, dtype=np.uint8)
            else:
                differences = np.empty_like(block)
                differences[0] = block[0]
                np.subtract(block[1:], block[:-1], out=differences[1:])
                transformed = _bitplanes(differences, inverse=False)
        else:
            transformed = _bitplanes(block, inverse)
        result.extend(transformed.tobytes())
    result.extend(data[aligned_size:])
    return bytes(result)
