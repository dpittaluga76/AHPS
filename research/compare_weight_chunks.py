"""Serial, bounded comparison for the three extracted Z-Image chunks.

Use `python -m research.compare_weight_chunks screen` first. A full run reads
an explicit list of [transform, codec] pairs from the --methods JSON file.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import ahps
from benchmark_ahps import RunLock, process_peak
from research.job_runner import run_job
from research.weight_compression import encode, decode
from research.weight_transforms import METHODS


ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "corpus_development" / "z-image-turbo"
OUTPUT = ROOT / "experiments" / "weight-chunks-20261105"
SCREEN_CODECS = ("xz9", "zstd19", "bz2", "brotli")


def worker(phase, index, methods):
    path = CHUNKS / f"chunk_{index:02d}.bin"
    data = path.read_bytes()
    source_digest = hashlib.sha256(data).hexdigest()
    manifest = json.loads((CHUNKS / "chunks.json").read_text())
    expected_source = manifest["chunks"][index - 1]
    if len(data) != expected_source["bytes"] or source_digest != expected_source["sha256"]:
        raise ValueError("Chunk differs from its extraction manifest")
    if phase == "screen":
        width = 256 * 1024
        offsets = (0, 5 * 1024 * 1024, 10 * 1024 * 1024, len(data) - width)
        data = b"".join(data[offset:offset + width] for offset in offsets)
    expected = hashlib.sha256(data).hexdigest()
    results_path = OUTPUT / f"{phase}-{index:02d}.jsonl"
    with results_path.open("x", encoding="utf-8") as results:
        for method, codec in methods:
            start = time.perf_counter()
            if method == "ahps":
                compressed, report = ahps.ahps_compress_bytes(data, lzma_preset=9)
            elif method == "ppmd":
                archive_path = OUTPUT / f"chunk_{index:02d}.ppmd.7z"
                if archive_path.exists():
                    raise FileExistsError(archive_path)
                subprocess.run([r"C:\Program Files\7-Zip\7z.exe", "a", "-t7z", "-m0=PPMd",
                                "-mo=6", "-mmem=256m", "-mmt=1", str(archive_path), str(path)],
                               check=True, capture_output=True)
                compressed = archive_path.read_bytes()
            else:
                compressed = encode(data, method, codec)
            encode_seconds = time.perf_counter() - start
            start = time.perf_counter()
            if method == "ahps":
                restored = ahps.ahps_decompress_bytes(compressed)
            elif method == "ppmd":
                restored = subprocess.run([r"C:\Program Files\7-Zip\7z.exe", "e", "-so", str(archive_path)],
                                          check=True, capture_output=True).stdout
            else:
                restored = decode(compressed)
            decode_seconds = time.perf_counter() - start
            if restored != data:
                raise ValueError("Round-trip mismatch")
            record = dict(phase=phase, chunk=index, method=method, codec=codec,
                          input_bytes=len(data), archive_bytes=len(compressed),
                          encode_seconds=encode_seconds, decode_seconds=decode_seconds,
                          source_sha256=source_digest, input_sha256=expected,
                          archive_sha256=hashlib.sha256(compressed).hexdigest(),
                          verified=True, cumulative_peak_working_set=process_peak())
            if method == "ahps":
                record["programs"] = report.programs
            if phase == "full":
                if method != "ppmd":
                    extension = "ahps" if method == "ahps" else "ahwt"
                    archive_path = OUTPUT / f"chunk_{index:02d}.{method}.{codec}.{extension}"
                    with archive_path.open("xb") as output:
                        output.write(compressed)
                record["archive_path"] = str(archive_path.relative_to(ROOT))
            results.write(json.dumps(record) + "\n")
            results.flush()
            print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("screen", "full"))
    parser.add_argument("--methods", type=Path)
    parser.add_argument("--worker", type=int)
    args = parser.parse_args()
    if args.phase == "full" and args.methods is None:
        parser.error("full requires --methods")
    methods = ([(method, codec) for method in METHODS for codec in SCREEN_CODECS]
               if args.phase == "screen" else json.loads(args.methods.read_text()))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args.phase, args.worker, methods)
        return
    deadline = time.monotonic() + 900
    with RunLock(ROOT / ".ahps-benchmark.lock"):
        for index in range(1, 4):
            command = [sys.executable, "-B", "-m", "research.compare_weight_chunks",
                       args.phase, "--worker", str(index)]
            if args.methods:
                command += ["--methods", str(args.methods.resolve())]
            result = run_job(command, cwd=ROOT, deadline=min(deadline, time.monotonic() + 300))
            with (OUTPUT / f"{args.phase}-{index:02d}-job.json").open("x") as output:
                json.dump(result, output, indent=2)
            print(f"Chunk {index} {args.phase}: {result['status']}", flush=True)
            if result["status"] != "ok":
                print(result.get("stderr", ""), flush=True)
                raise SystemExit(1)


if __name__ == "__main__":
    main()
