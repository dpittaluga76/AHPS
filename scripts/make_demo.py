#!/usr/bin/env python3
"""Create a small synthetic input for the public example benchmark manifest."""

from pathlib import Path
import struct


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "examples" / "data" / "demo.bin"
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(struct.pack("<QII", 1700000000000 + i * 1000, i, i * 17 + 42)
                    for i in range(4096))
    with destination.open("xb") as stream:
        stream.write(data)
    print(f"Created {destination.relative_to(root)} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
