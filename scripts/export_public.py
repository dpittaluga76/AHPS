#!/usr/bin/env python3
"""Create a new, allowlisted public source tree and optional ZIP, without Git history."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_public import read_public_files, validate_public_tree


def export_public(root: Path, destination: Path, archive: Path | None = None):
    root = root.resolve()
    destination = destination.resolve()
    archive = archive.resolve() if archive is not None else None
    if destination.exists():
        raise ValueError("Export destination already exists; choose a new directory.")
    if ".git" in destination.parts or (archive is not None and ".git" in archive.parts):
        raise ValueError("Git metadata is not a release destination.")
    if archive is not None and archive.exists():
        raise ValueError("Archive destination already exists; choose a new filename.")
    if archive is not None and archive.is_relative_to(destination):
        raise ValueError("Place the ZIP outside the exported source tree.")
    issues = validate_public_tree(root)
    if issues:
        raise ValueError("Public source validation failed:\n" + "\n".join(issues))
    names = read_public_files(root)
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for name in names:
        payload = (root / name).read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
        records.append({"path": name, "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()})
    metadata = {"format": "ahps-public-source-v1", "files": records}
    manifest = destination / "PUBLIC_RELEASE.json"
    with manifest.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, indent=2)
        stream.write("\n")
    issues = validate_public_tree(destination)
    if issues:
        raise ValueError("Export validation failed; inspect the new tree:\n" + "\n".join(issues))
    if archive is not None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for name in sorted([*names, "PUBLIC_RELEASE.json"]):
                info = zipfile.ZipInfo(f"{destination.name}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                output.writestr(info, (destination / name).read_bytes())
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New source directory; existing paths are refused")
    parser.add_argument("--zip", dest="archive", type=Path, help="Optional new ZIP outside the source directory")
    args = parser.parse_args()
    try:
        records = export_public(Path(__file__).resolve().parents[1], args.output, args.archive)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Export refused: {exc}\n")
    print(f"Exported {len(records)} files ({sum(item['bytes'] for item in records):,} bytes) to {args.output}")
    if args.archive is not None:
        print(f"Saved {args.archive}")


if __name__ == "__main__":
    main()
