#!/usr/bin/env python3
"""Bounded, reproducible AHPS fuzzing in disposable subprocesses.

Only the preserved v3 test fixtures are read; no benchmark corpus is read.
Native crashes, timeouts, unexpected exceptions,
accepted deliberately invalid archives, and incorrect output all fail the run.
Examples: python fuzz_ahps.py --cases 500 --engine both
          python fuzz_ahps.py --cases 3000 --campaign malformed --engine native
"""

import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import json
import lzma
import pathlib
import platform
import random
import struct
import subprocess
import sys
import tempfile
import time
import zlib


ROOT = pathlib.Path(__file__).resolve().parent
GOLDEN_ROOT = ROOT / "tests" / "fixtures" / "v3"
GOLDEN_CASES = ("dll", "empty", "lcg", "struct", "tiny")
GOLDEN_NAMES = tuple(f"{case}_{engine}.ahps" for case in GOLDEN_CASES for engine in ("c", "py"))
LIMIT_BYTES = {"max_dict_bytes": 16 << 20, "max_container_bytes": 1 << 20,
               "max_output_bytes": 1 << 20}


def read_fixture(name):
    path = GOLDEN_ROOT / name
    if path.stat().st_size > 16 * 1024:
        raise AssertionError(f"Unexpectedly large golden fixture: {name}")
    return path.read_bytes()


@lru_cache(maxsize=1)
def golden_containers():
    """Expand the fixed, known-small fixtures once per worker, never a holdout."""
    result = []
    for name in GOLDEN_NAMES:
        # The original fixtures use a larger dictionary. Mutants are repacked
        # at preset 0 and decoded with LIMIT_BYTES, including the 16 MiB cap.
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 << 20)
        raw = decoder.decompress(read_fixture(name), max_length=(64 << 10) + 1)
        if len(raw) > 64 << 10 or not decoder.eof or decoder.unused_data or raw[:5] != b"AHPS\x03":
            raise AssertionError(f"Invalid or oversized golden v3 container: {name}")
        result.append((name, raw))
    return tuple(result)


def valid_mutation_base(ahps, rng, index, data):
    source = index % 4
    if source == 3:
        name, raw = golden_containers()[(index // 4) % len(GOLDEN_NAMES)]
        return raw, f"golden:{name}"
    version = source + 3
    if version == 3:
        if not data:
            return b"AHPS\x03" + struct.pack("<III", 0, 1024, 0), "generated:v3"
        op = rng.randrange(7)
        params = rng.randbytes(3)
        residual = ahps.py_forward_lift(op, params + b"\x00", data)
        tape = bytes([op]) + (params if op else b"")
        return (b"AHPS\x03" + struct.pack("<III", 1, max(1, len(data)), 3 if op else 0)
                + tape + residual), "generated:v3"
    if not data:
        return container(b"AHPS" + bytes([version]), data, b"", 0), f"generated:v{version}"
    # These opcodes have the same meaning in historical v4 and current v5.
    op = rng.choice([ahps.OP_DELTA1_LANE, ahps.OP_DELTA2_LANE, ahps.OP_POLY2,
                     ahps.OP_XORSHIFT16, ahps.OP_TRANSPOSE4, ahps.OP_SPLIT, ahps.OP_EXE32])
    params = rng.randbytes(4)
    residual = ahps.py_forward_lift(op, params, data)
    table = struct.pack("<IB", len(data), 1) + bytes([op]) + params
    return container(b"AHPS" + bytes([version]), data, table, 1, residual), f"generated:v{version}"


def mutate_raw(rng, raw):
    """Deterministic raw-byte edits; acceptance is deliberately not prescribed."""
    raw = bytearray(raw)
    operation = rng.choice(("flip", "overwrite", "truncate", "insert", "delete"))
    if operation == "flip":
        for offset in rng.sample(range(len(raw)), rng.randrange(1, min(4, len(raw)) + 1)):
            raw[offset] ^= 1 << rng.randrange(8)
    elif operation == "overwrite":
        start = rng.randrange(len(raw))
        count = rng.randrange(1, min(16, len(raw) - start) + 1)
        raw[start:start + count] = bytes(value ^ rng.randrange(1, 256) for value in raw[start:start + count])
    elif operation == "truncate":
        del raw[rng.randrange(len(raw)):]
    elif operation == "insert":
        start = rng.randrange(len(raw) + 1)
        raw[start:start] = rng.randbytes(rng.randrange(1, 17))
    else:
        start = rng.randrange(len(raw))
        del raw[start:start + rng.randrange(1, min(16, len(raw) - start) + 1)]
    return bytes(raw), operation


def file_outcome(ahps, archive, force_python, limits):
    """Runs the streaming decoder. A rejected archive must leave nothing but the input behind."""
    with tempfile.TemporaryDirectory() as folder:
        folder = pathlib.Path(folder)
        source, target = folder / "input.ahps", folder / "restored.bin"
        source.write_bytes(archive)
        try:
            ahps.ahps_decompress_file(source, target, force_python, limits)
        except (ValueError, lzma.LZMAError):
            if [path.name for path in folder.iterdir()] != ["input.ahps"]:
                raise AssertionError("rejected streaming decode left files behind")
            return "rejected", None
        return "accepted", target.read_bytes()


def check_mutated_archive(ahps, raw, limits):
    archive = lzma.compress(raw, format=lzma.FORMAT_XZ, preset=0)
    outcomes = []
    for force_python in (False, True):
        try:
            restored = ahps.ahps_decompress_bytes(archive, force_python, limits)
        except (ValueError, lzma.LZMAError):
            outcomes.append(("rejected", None))
            continue
        # Independent wire-level check: do not use the production parser to
        # supply the expected CRC or length after a mutant has been accepted.
        if raw[:5] in (b"AHPS\x04", b"AHPS\x05"):
            if len(raw) < 25:
                raise AssertionError("decoder accepted a short v4/v5 header")
            _, table_len, total_len, expected_crc = struct.unpack_from("<IIQI", raw, 5)
            if 25 + table_len + total_len != len(raw) or len(restored) != total_len:
                raise AssertionError("accepted v4/v5 lengths disagree with the wire header")
            if zlib.crc32(restored) != expected_crc:
                raise AssertionError("accepted v4/v5 output fails independent CRC check")
        outcomes.append(("accepted", restored))
    if outcomes[0] != outcomes[1]:
        raise AssertionError("native/Python mutation acceptance or restored bytes differ")
    for force_python in (False, True):
        if file_outcome(ahps, archive, force_python, limits) != outcomes[0]:
            raise AssertionError("streaming and buffered decoders disagree on a mutation")
    return outcomes[0][0]


def payload(rng, n, family):
    if family == 0:
        return rng.randbytes(n)
    if family == 1:
        return bytes([rng.randrange(256)]) * n
    if family == 2:
        return bytes((i * 7 + 19) & 255 for i in range(n))
    if family == 3:
        atoms = [b"\xe8\x00\x00\x00\x00", b"\x89\xe8", b"\xe9\xff\xff\xff\xff",
                 b"\x0f\x85\x12\x00\x00\x00", b"\xff\x15\xf7\xff\xff\xff",
                 b"\x48\x8d\x05\x01\x00\x00\x00"]
        out = bytearray()
        while len(out) < n:
            out.extend(rng.choice(atoms) if rng.random() < .7 else rng.randbytes(rng.randrange(1, 12)))
        return bytes(out[:n])
    record = struct.pack("<QII", rng.randrange(1 << 32), rng.randrange(256), rng.randrange(65536))
    return (record * ((n + 15) // 16))[:n]


def container(magic, data, table, count, residual=None, crc=None):
    residual = data if residual is None else residual
    crc = zlib.crc32(data) if crc is None else crc
    return magic + struct.pack("<IIQI", count, len(table), len(data), crc) + table + residual


def modes(ahps, engine):
    if engine in ("native", "both") and ahps.SIMD_BRIDGE.lib is None:
        raise RuntimeError("Native engine requested but unavailable; refusing a silent Python-only pass")
    return [True] if engine == "python" else [False] if engine == "native" else [False, True]


def check_case(ahps, seed, index, campaign, engines):
    rng = random.Random((seed << 32) ^ index)
    limits = ahps.DecodeLimits(**LIMIT_BYTES)
    if campaign == "transforms":
        # Preserve Ken's direct inverse/bijection campaign. Feed arbitrary
        # residuals to the inverse, not only forward-generated valid samples.
        n = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 31, 64, 257])
        alphabet = bytes([0xE8, 0xE9, 0x0F, 0x80, 0x8F, 0xFF, 0x15, 0x25,
                          0x8B, 0x8D, 0x05, 0x89, 0x83, 0x48, 0x00])
        data = bytes(rng.choice(alphabet) for _ in range(n)) if index % 2 else rng.randbytes(n)
        prm = rng.randbytes(4) if index % 3 else bytes(4)
        op = ahps.OP_EXE_SPLIT
        forward = ahps.lift_step(op, prm, data, False)
        inverse = ahps.lift_step(op, prm, data, False, inverse=True)
        for force_py in engines:
            use_c = not force_py
            if ahps.lift_step(op, prm, data, use_c) != forward:
                raise AssertionError("EXE_SPLIT forward engine mismatch")
            if ahps.lift_step(op, prm, forward, use_c, inverse=True) != data:
                raise AssertionError("EXE_SPLIT forward/inverse mismatch")
            if ahps.lift_step(op, prm, data, use_c, inverse=True) != inverse:
                raise AssertionError("EXE_SPLIT arbitrary inverse engine mismatch")
            if ahps.lift_step(op, prm, inverse, use_c) != data:
                raise AssertionError("EXE_SPLIT inverse/forward mismatch")
        return
    n = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 15, 31, 63, 127, 255, 511, 1024, 2049, 4096])
    data = payload(rng, n, index % 5)
    if campaign == "roundtrip":
        for enc_py in engines:
            encoded, _ = ahps.ahps_compress_bytes(data, rng.choice([64, 257, 1024, 4096]), enc_py, 0)
            for dec_py in engines:
                if ahps.ahps_decompress_bytes(encoded, dec_py, limits) != data:
                    raise AssertionError("encoder/decoder round trip differs")
                if file_outcome(ahps, encoded, dec_py, limits) != ("accepted", data):
                    raise AssertionError("streaming encoder/decoder round trip differs")
        return

    if campaign == "crafted":
        # Build a valid archive independently of the compressor/planner. Correct
        # CRCs mean successful inversion is checked, not merely rejection.
        n = max(1, n)
        data = payload(rng, n, index % 5)
        opcodes = [ahps.OP_POLY2, ahps.OP_DELTA1_LANE, ahps.OP_DELTA2_LANE,
                   ahps.OP_XORSHIFT16, ahps.OP_TRANSPOSE4, ahps.OP_SPLIT,
                   ahps.OP_EXE32, ahps.OP_EXE_SPLIT]
        program = []
        for _ in range(rng.randrange(4)):
            op = rng.choice(opcodes)
            prm = rng.randbytes(4)
            if op in (ahps.OP_DELTA1_LANE, ahps.OP_DELTA2_LANE, ahps.OP_SPLIT):
                prm = bytes([rng.choice([0, 1, 2, 4, 7, 16, 255])]) + prm[1:]
            program.append((op, prm))
        residual = data
        for op, prm in program:
            expected = ahps.lift_step(op, prm, residual, False)
            if False in engines and ahps.lift_step(op, prm, residual, True) != expected:
                raise AssertionError("native/Python forward residual differs")
            residual = expected
        table = struct.pack("<IB", n, len(program)) + b"".join(bytes([op]) + prm for op, prm in program)
        for magic in dict.fromkeys([b"AHPS\x04", ahps.MAGIC_HEADER]):
            archive = lzma.compress(container(magic, data, table, 1, residual), preset=0)
            for dec_py in engines:
                if ahps.ahps_decompress_bytes(archive, dec_py, limits) != data:
                    raise AssertionError("valid crafted archive decoded incorrectly")
                if file_outcome(ahps, archive, dec_py, limits) != ("accepted", data):
                    raise AssertionError("valid crafted archive streamed incorrectly")
        return

    if campaign == "mutations":
        if engines != [False, True]:
            raise ValueError("mutations requires --engine both for native/Python parity")
        raw, source = valid_mutation_base(ahps, rng, index, data)
        raw, operation = mutate_raw(rng, raw)
        outcome = check_mutated_archive(ahps, raw, limits)
        return {"source": source, "mutation": operation, "outcome": outcome}

    # Every selected mutation is invalid by construction. Do not catch arbitrary
    # exceptions: IndexError/struct.error/etc. expose decoder validation gaps.
    n = max(1, n)
    data = payload(rng, n, index % 5)
    version = 3 + index % 3
    mutation = (index // 3) % 10
    magic = b"AHPS" + bytes([version])
    if version == 3:
        raw = bytearray(magic + struct.pack("<III", 1, n, 0) + b"\x00" + data)
        if mutation == 0:
            struct.pack_into("<I", raw, 5, 0xFFFFFFFF)  # Count overruns tape.
        elif mutation == 1:
            struct.pack_into("<I", raw, 9, 0)  # Zero block size.
        elif mutation == 2:
            struct.pack_into("<I", raw, 9, 0xFFFFFFFF)  # Block size exceeds maximum.
        elif mutation == 3:
            struct.pack_into("<I", raw, 13, 0xFFFFFFFF)  # Params overrun tape.
        elif mutation == 4:
            raw[17] = 0xFE  # Only historical opcodes 0..6 are legal.
        elif mutation == 5:
            raw = raw[:17]  # Declares one block but contains no opcode.
        elif mutation == 6:
            raw = magic + struct.pack("<III", 0, n, 0) + b"\x00"  # Nonempty residual for zero blocks.
        elif mutation == 7:
            raw[17] = ahps.OP_POLY2  # Non-RAW opcode requires three parameter bytes.
        elif mutation == 8:
            raw = magic + struct.pack("<III", 1, n, 3) + b"\x00" + bytes(3) + data
        else:
            raw = magic + struct.pack("<III", 0, 1024, 1) + b"\x00"  # Invalid empty param tape.
    else:
        table = struct.pack("<IB", n, 0)
        raw = bytearray(container(magic, data, table, 1))
    if version != 3 and mutation == 0:
        raw[:5] = b"BAD!!"
    elif version != 3 and mutation == 1:
        struct.pack_into("<I", raw, 5, 0xFFFFFFFF)
    elif version != 3 and mutation == 2:
        struct.pack_into("<I", raw, 9, len(table) + 1)
    elif version != 3 and mutation == 3:
        struct.pack_into("<Q", raw, 13, (1 << 63) + n)
    elif version != 3 and mutation == 4:
        struct.pack_into("<I", raw, 21, zlib.crc32(data) ^ 1)
    elif version != 3 and mutation == 5:
        struct.pack_into("<I", raw, 25, 0)
    elif version != 3 and mutation == 6:
        table = struct.pack("<IB", n, 1) + b"\xfe" + bytes(4)
        raw = container(magic, data, table, 1)
    elif version != 3 and mutation == 7:
        raw[29] = 4  # Too many steps, with no step bytes.
    archive = lzma.compress(raw, preset=0)
    if version != 3 and mutation == 8:
        archive = archive[:max(1, len(archive) // 2)]
    elif version != 3 and mutation == 9:
        archive = b"not an xz header" + rng.randbytes(n)
    for dec_py in engines:
        try:
            ahps.ahps_decompress_bytes(archive, dec_py, limits)
        except (ValueError, lzma.LZMAError):
            pass
        else:
            raise AssertionError(f"deliberately invalid v{version} mutation {mutation} accepted")
        if file_outcome(ahps, archive, dec_py, limits)[0] != "rejected":
            raise AssertionError(f"deliberately invalid v{version} mutation {mutation} streamed")
    return {"source": f"v{version}", "mutation": str(mutation), "outcome": "rejected"}


def worker(args):
    import ahps
    engines = modes(ahps, args.engine)
    coverage = Counter()
    for index in range(args.start, args.start + args.count):
        try:
            result = check_case(ahps, args.seed, index, args.campaign, engines)
            if result:
                coverage.update(f"{key}={value}" for key, value in result.items())
        except Exception:
            print(f"REPRO campaign={args.campaign} seed={args.seed} index={index} engine={args.engine}",
                  file=sys.stderr, flush=True)
            raise
    print(json.dumps({"campaign": args.campaign, "start": args.start, "count": args.count,
                      "backend": ahps.SIMD_BRIDGE.lib_path, "coverage": dict(coverage), "ok": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=300)
    parser.add_argument("--campaign", choices=["all", "roundtrip", "crafted", "malformed", "transforms", "mutations"], default="all")
    parser.add_argument("--engine", choices=["both", "native", "python"], default="both")
    parser.add_argument("--seed", type=int, default=42229)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--start", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.campaign in ("all", "mutations") and args.engine != "both":
        parser.error("the mutations campaign requires --engine both (also when using --campaign all)")
    if args.worker:
        worker(args)
        return
    if args.cases < 1 or args.batch_size < 1 or args.timeout <= 0:
        parser.error("cases, batch-size, and timeout must be positive")
    campaigns = ["roundtrip", "crafted", "malformed", "transforms", "mutations"] if args.campaign == "all" else [args.campaign]
    root = pathlib.Path(__file__).resolve().parent
    report = {"seed": args.seed, "cases_per_campaign": args.cases, "engine": args.engine,
              "python": sys.version, "platform": platform.platform(), "results": [], "ok": False,
              "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                for name in ["ahps.py", "ahps_simd.c", "fuzz_ahps.py"]}}
    report["decode_limits"] = LIMIT_BYTES
    if "mutations" in campaigns:
        report["fixture_sha256"] = {
            name: hashlib.sha256(read_fixture(name)).hexdigest()
            for name in (*GOLDEN_NAMES, *(f"{case}.sha256" for case in GOLDEN_CASES))
        }
    for library in ("ahps_simd.dll", "libahps_simd.so", "libahps_simd.dylib"):
        if (root / library).is_file():
            report["source_sha256"][library] = hashlib.sha256((root / library).read_bytes()).hexdigest()
    started = time.perf_counter()
    try:
        for campaign in campaigns:
            coverage = Counter()
            for start in range(0, args.cases, args.batch_size):
                command = [sys.executable, "-B", str(pathlib.Path(__file__).resolve()), "--worker",
                           "--campaign", campaign, "--engine", args.engine, "--seed", str(args.seed),
                           "--start", str(start), "--count", str(min(args.batch_size, args.cases - start))]
                try:
                    child = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                           timeout=args.timeout)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f"worker timeout; reproduce: {subprocess.list2cmdline(command)}") from exc
                if child.returncode:
                    raise RuntimeError(f"worker failed ({child.returncode}); reproduce: "
                                       f"{subprocess.list2cmdline(command)}\n{child.stderr}\n{child.stdout}")
                result = json.loads(child.stdout.strip().splitlines()[-1])
                if result.get("ok") is not True:
                    raise AssertionError("worker did not report success")
                coverage.update(result.get("coverage", {}))
                report["results"].append(result)
            print(f"PASS {campaign}: {args.cases} cases ({args.engine})", flush=True)
            if coverage:
                print(json.dumps({"campaign": campaign, "coverage": dict(coverage)}, sort_keys=True), flush=True)
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["elapsed_sec"] = time.perf_counter() - started
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
