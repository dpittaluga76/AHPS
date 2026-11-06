#!/usr/bin/env python3
"""Development-only transform comparison, never an AHPS archive benchmark.

Run: benchmark_predicates.py run --files git-cli --experiment-dll PATH --output PATH
Build: benchmark_predicates.py build --gcc PATH --gcc-sha256 HASH --source-sha256 HASH
       --experiment-dir experiments/no-modrm-BUILD_ID

EXE32/EXE_SPLIT residuals use ordinary XZ, preset 9 extreme. Transform parameters
and the matching inverse remain external experiment settings, not archive metadata.
The resulting sizes are not comparable to complete AHPS archive sizes.
"""

import argparse
import ctypes
from datetime import datetime, timezone
import json
import lzma
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

# -I omits the script directory from import paths; explicitly use our sibling.
REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import benchmark_ahps as bench

INT_MAX = (1 << 31) - 1
EXPERIMENT_DLL = "ahps_no_modrm_9206.dll"
ABIS = {"baseline": 206, "no-modrm": 9206}
TRANSFORMS = {"exe32": 8, "exe_split": 9}
FLAGS = ("-O3", "-mavx2", "-mpopcnt", "-shared", "-DAHPS_EXPERIMENT_NO_MODRM",
         "-DAHPS_ABI_VERSION=9206")


def artifact_paths(experiment_dll=None, compiler=None):
    paths = [REPO / name for name in ("ahps.py", "ahps_simd.c", "ahps_simd.dll",
             "libahps_simd.so", "libahps_simd.dylib", "benchmark_ahps.py",
             "benchmark_predicates.py", "test_predicate_harness.py")]
    if experiment_dll is not None:
        paths += [Path(experiment_dll), Path(experiment_dll).with_name("build.json")]
    if compiler is not None:
        paths.append(Path(compiler))
    return bench.protected_runtime_paths(extra=paths)


def protected_paths(manifest, corpus_root, experiment_dll=None, compiler=None):
    return bench.protected_manifest_paths(manifest, corpus_root) + artifact_paths(experiment_dll, compiler)


def validate_experiment_path(path):
    path = Path(path).resolve()
    experiment_root = (REPO / "experiments").resolve()
    if (path.name != EXPERIMENT_DLL or path.parent == experiment_root
            or not path.parent.is_relative_to(experiment_root)
            or (path.parent / "ahps.py").exists()):
        raise ValueError("Experiment DLL must be ahps_no_modrm_9206.dll in its own experiments/ subdirectory")
    if bench.same_file(path, REPO / "ahps_simd.dll"):
        raise ValueError("Experiment DLL must not alias the production DLL")
    return path


def cpu_supports_native():
    # Same Windows AVX2 capability gate as the production bridge. Do not import
    # ahps.py here: its module initialization automatically loads the normal DLL.
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    try:
        query = ctypes.WinDLL("kernel32", use_last_error=True).IsProcessorFeaturePresent
        query.argtypes = [ctypes.c_uint32]
        query.restype = ctypes.c_int
        return bool(query(40))
    except (AttributeError, OSError):
        return False


class Kernel:
    def __init__(self, path, variant):
        path = Path(path).resolve()
        if variant not in ABIS:
            raise ValueError("Unknown predicate variant")
        if variant == "no-modrm":
            validate_experiment_path(path)
        elif path != (REPO / "ahps_simd.dll").resolve():
            raise ValueError("Baseline must use the production script-directory DLL")
        if not cpu_supports_native():
            raise RuntimeError("AVX2 capability is not confirmed; refusing to load native code")
        self.lib = ctypes.CDLL(str(path))
        self.lib.ahps_abi_version.argtypes = []
        self.lib.ahps_abi_version.restype = ctypes.c_int
        self.abi = self.lib.ahps_abi_version()
        if self.abi != ABIS[variant]:
            raise RuntimeError(f"ABI mismatch: loaded {self.abi}, expected {ABIS[variant]}")
        for function in (self.lib.ahps_forward_lift, self.lib.ahps_inverse_lift):
            function.argtypes = [ctypes.c_uint8, ctypes.c_char_p, ctypes.c_char_p,
                                 ctypes.c_void_p, ctypes.c_int]
            function.restype = None

    def lift(self, data, transform, inverse=False):
        if len(data) > INT_MAX:
            raise ValueError("Transform input exceeds INT_MAX")
        if transform not in TRANSFORMS:
            raise ValueError("Unknown transform")
        output = ctypes.create_string_buffer(max(1, len(data)))
        function = self.lib.ahps_inverse_lift if inverse else self.lib.ahps_forward_lift
        function(TRANSFORMS[transform], bytes(4), data, output, len(data))
        return output.raw[:len(data)]


def encode_residual(kernel, data, transform):
    residual = kernel.lift(data, transform)
    return lzma.compress(residual, format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME)


def decode_residual(kernel, encoded, transform, expected_size):
    if not 0 <= expected_size <= INT_MAX:
        raise ValueError("Expected transform length exceeds INT_MAX")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 << 20)
    residual = decoder.decompress(encoded, max_length=expected_size + 1)
    if len(residual) != expected_size or not decoder.eof or decoder.unused_data:
        raise ValueError("Residual XZ stream length/end does not match the authorized input")
    return kernel.lift(residual, transform, inverse=True)


def read_build_metadata(path):
    """Read the text provenance only, so output guards can protect its compiler."""
    path = validate_experiment_path(path)
    metadata = json.loads(path.with_name("build.json").read_text(encoding="utf-8"))
    if (metadata.get("schema_version") != 1 or metadata.get("status") != "ok"
            or metadata.get("abi") != 9206 or metadata.get("flags") != list(FLAGS)):
        raise ValueError("Experimental build metadata has wrong status, ABI or flags")
    compiler = metadata.get("compiler", {})
    if (not isinstance(compiler, dict) or not isinstance(compiler.get("sha256"), str)
            or len(compiler["sha256"]) != 64 or not isinstance(compiler.get("path"), str)
            or not Path(compiler["path"]).is_absolute()):
        raise ValueError("Experimental build metadata lacks its compiler identity")
    return metadata


def validate_build_metadata(path):
    path = validate_experiment_path(path)
    metadata = read_build_metadata(path)
    if metadata.get("library") != bench.fingerprint(path):
        raise ValueError("Experimental DLL differs from its build metadata")
    if metadata.get("source") != bench.fingerprint(REPO / "ahps_simd.c"):
        raise ValueError("Experimental C source differs from its pinned build metadata")
    return metadata


def guard_implementation_inputs(corpus_paths, experiment=None, compiler=None):
    """Reject corpus/hardlink aliases before fingerprinting any implementation."""
    for path in artifact_paths(experiment, compiler):
        bench.guard_destination(path, corpus_paths)


def worker(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("encode", "decode"))
    parser.add_argument("variant", choices=ABIS)
    parser.add_argument("transform", choices=TRANSFORMS)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--dll", required=True, type=Path)
    parser.add_argument("--dll-sha256", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--file-id", required=True)
    args = parser.parse_args(argv)
    result = {"pid": os.getpid(), "python_version": sys.version, "status": "error"}
    try:
        selected = bench.select_inputs(args.manifest, args.corpus_root, [args.file_id])[0][1]
        corpus_paths = bench.protected_manifest_paths(args.manifest, args.corpus_root)
        experiment = args.dll if args.variant == "no-modrm" else None
        guard_implementation_inputs(corpus_paths, experiment)
        compiler = None
        if experiment is not None:
            compiler = Path(read_build_metadata(experiment)["compiler"]["path"])
            bench.guard_destination(compiler, corpus_paths)
        protected = protected_paths(args.manifest, args.corpus_root, experiment, compiler)
        source, destination = args.source.resolve(), args.destination.resolve()
        bench.guard_destination(destination, protected, source)
        if destination.is_relative_to((REPO / "experiments").resolve()):
            raise ValueError("Worker output must not modify experimental implementation artifacts")
        if destination.suffix.lower() == ".ahps":
            raise ValueError("This experiment never emits AHPS archives")
        if args.phase == "encode":
            if not bench.same_file(source, selected):
                raise ValueError("Worker source is not its authorized development input")
        elif (bench.sealed_name(source) or source.is_relative_to((REPO / "experiments").resolve())
              or any(bench.same_file(source, path) for path in protected)):
            raise ValueError("Decode source is a protected data or implementation path")
        # Prevent an explicitly supplied DLL/build.json from aliasing corpus data.
        bench.guard_destination(args.dll, corpus_paths)
        if experiment is not None:
            bench.guard_destination(args.dll.with_name("build.json"), corpus_paths)
            validate_build_metadata(args.dll)
        expected_size = selected.stat().st_size
        if expected_size > INT_MAX:
            raise ValueError("Authorized input exceeds INT_MAX")
        if bench.sha256(args.dll) != args.dll_sha256:
            raise ValueError("DLL hash changed before worker load")
        kernel = Kernel(args.dll, args.variant)
        result.update(loaded_abi=kernel.abi, dll_path=str(args.dll.resolve()), variant=args.variant,
                      transform=args.transform, output_kind="ordinary XZ residual, not an AHPS archive")
        start = time.perf_counter()
        data = source.read_bytes()
        if args.phase == "encode":
            if len(data) != expected_size:
                raise ValueError("Authorized source changed size")
            output = encode_residual(kernel, data, args.transform)
        else:
            output = decode_residual(kernel, data, args.transform, expected_size)
        destination.write_bytes(output)
        result["operation_seconds"] = time.perf_counter() - start
        result["peak_working_set_bytes"] = bench.process_peak()
        result["peak_method"] = "GetProcessMemoryInfo(self), after read/transform/write"
        result["status"] = "ok"
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "ok" else 1


def implementation(experiment, interpreter):
    import _lzma
    return {"artifacts": [bench.fingerprint(path) for path in (
        Path(__file__), REPO / "benchmark_ahps.py", REPO / "ahps.py", REPO / "ahps_simd.c",
        REPO / "ahps_simd.dll", interpreter, Path(_lzma.__file__), experiment,
        experiment.with_name("build.json"))], "experiment_build": validate_build_metadata(experiment)}


def one_run(args, file_id, source, variant, transform, repeat, batch_id, interpreter):
    row = {"schema_version": 1, "kind": "transform-only-predicate-experiment",
           "size_interpretation": "ordinary XZ residual bytes; not comparable to AHPS archive bytes",
           "batch_id": batch_id, "run_id": str(uuid.uuid4()), "file_id": file_id,
           "variant": variant, "expected_abi": ABIS[variant], "transform": transform,
           "repeat": repeat, "started_utc": datetime.now(timezone.utc).isoformat(),
           "status": "error", "phases": {}, "roundtrip_verified": False,
           "settings": {"preset": 9, "extreme": True, "base_offset": 0, "threads": 1,
                        "requested_repeats": args.repeats, "phase_timeout_seconds": args.timeout,
                        "cache_policy": "no cache flush; decode follows encode"}}
    try:
        selected = bench.select_inputs(args.manifest, args.corpus_root, [file_id])[0][1]
        if not bench.same_file(selected, source) or source.stat().st_size > INT_MAX:
            raise ValueError("Unauthorized source or input exceeds INT_MAX")
        corpus_paths = bench.protected_manifest_paths(args.manifest, args.corpus_root)
        guard_implementation_inputs(corpus_paths, args.experiment_dll)
        row["manifest"], row["source"] = bench.fingerprint(args.manifest), bench.fingerprint(source)
        row["implementation"] = implementation(args.experiment_dll, interpreter)
        dll = REPO / "ahps_simd.dll" if variant == "baseline" else args.experiment_dll
        dll_hash = bench.sha256(dll)
        with tempfile.TemporaryDirectory(prefix="ahps-predicate-residual-") as temporary:
            encoded, restored = Path(temporary) / "residual.xz", Path(temporary) / "restored.bin"
            for phase, src, dst in (("encode", source, encoded), ("decode", encoded, restored)):
                command = [str(interpreter), "-I", "-B", str(Path(__file__).resolve()), "_worker",
                           phase, variant, transform, str(src), str(dst), "--dll", str(dll),
                           "--dll-sha256", dll_hash, "--manifest", str(args.manifest),
                           "--corpus-root", str(args.corpus_root), "--file-id", file_id]
                measured = bench.launch(command, args.timeout)
                row["phases"][phase] = measured
                if "error" in measured:
                    raise RuntimeError(phase + ": " + measured["error"])
                if measured.get("worker", {}).get("loaded_abi") != ABIS[variant]:
                    raise RuntimeError("Worker did not confirm the expected ABI")
            row["residual_xz_bytes"] = encoded.stat().st_size
            row["residual_xz_sha256"], row["restored_sha256"] = bench.sha256(encoded), bench.sha256(restored)
            row["roundtrip_verified"] = row["restored_sha256"] == row["source"]["sha256"]
            if not row["roundtrip_verified"]:
                raise RuntimeError("Round-trip SHA-256 mismatch")
            if bench.fingerprint(source) != row["source"] or bench.fingerprint(args.manifest) != row["manifest"]:
                raise RuntimeError("Input or manifest changed during the run")
            if implementation(args.experiment_dll, interpreter) != row["implementation"]:
                raise RuntimeError("Implementation artifacts changed during the run")
            for phase, measured in row["phases"].items():
                measured["uncompressed_mib_per_second"] = row["source"]["bytes"] / (1 << 20) / measured["elapsed_seconds"]
                measured["peak_working_set_over_uncompressed"] = (
                    measured["peak_working_set_bytes"] / row["source"]["bytes"] if row["source"]["bytes"] else None)
            row["status"] = "ok"
    except Exception as exc:
        row.update(error=str(exc), traceback=traceback.format_exc())
    return row


def build(args, root):
    dll = validate_experiment_path(args.experiment_dir / EXPERIMENT_DLL)
    metadata_path = dll.with_name("build.json")
    source = REPO / "ahps_simd.c"
    protected = protected_paths(args.manifest, args.corpus_root, compiler=args.gcc)
    corpus_paths = bench.protected_manifest_paths(args.manifest, args.corpus_root)
    guard_implementation_inputs(corpus_paths, compiler=args.gcc)
    bench.guard_destination(dll, protected)
    bench.guard_destination(metadata_path, protected)
    bench.guard_destination(args.gcc, bench.protected_manifest_paths(args.manifest, args.corpus_root))
    if dll.exists() or metadata_path.exists():
        raise ValueError("Use a fresh experiment directory; existing builds are never overwritten")
    with bench.RunLock(root / ".ahps-benchmark.lock"):
        compiler, source_id = bench.fingerprint(args.gcc), bench.fingerprint(source)
        if compiler["sha256"] != args.gcc_sha256 or source_id["sha256"] != args.source_sha256:
            raise ValueError("Compiler or C source does not match the explicitly pinned SHA-256")
        text = source.read_text(encoding="utf-8")
        if "AHPS_EXPERIMENT_NO_MODRM" not in text:
            raise ValueError("C source has no experimental predicate compile hook")
        dll.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.gcc.resolve()), *FLAGS, "-o", str(dll), str(source)]
        metadata = {"schema_version": 1, "status": "error", "abi": 9206, "flags": list(FLAGS),
                    "compiler": compiler, "source": source_id, "argv": command,
                    "created_utc": datetime.now(timezone.utc).isoformat()}
        try:
            metadata["compiler_version"] = subprocess.run([str(args.gcc.resolve()), "--version"],
                check=True, capture_output=True, text=True, timeout=30).stdout
            process = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=args.timeout)
            metadata.update(returncode=process.returncode, stdout=process.stdout, stderr=process.stderr)
            if process.returncode:
                raise RuntimeError("Experimental compiler failed")
            if bench.fingerprint(args.gcc) != compiler or bench.fingerprint(source) != source_id:
                raise RuntimeError("Compiler or source changed while building")
            Kernel(dll, "no-modrm")
            metadata.update(status="ok", library=bench.fingerprint(dll))
        finally:
            with metadata_path.open("x", encoding="utf-8") as output:
                json.dump(metadata, output, indent=2)
                output.write("\n")
    print(str(metadata_path))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_worker":
        return worker(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "build"):
        item = sub.add_parser(command)
        item.add_argument("--manifest", type=Path)
        item.add_argument("--corpus-root", type=Path)
        item.add_argument("--timeout", type=float, default=1800)
        if command == "run":
            item.add_argument("--files", nargs="+", required=True)
            item.add_argument("--experiment-dll", type=Path, required=True)
            item.add_argument("--output", type=Path, required=True)
            item.add_argument("--repeats", type=int, default=3)
        else:
            item.add_argument("--gcc", type=Path, required=True)
            item.add_argument("--gcc-sha256", required=True)
            item.add_argument("--source-sha256", required=True)
            item.add_argument("--experiment-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise ValueError("Timeout must be positive")
        root = bench.root_repo()
        args.manifest = (args.manifest or root / "corpus_manifest.json").resolve()
        args.corpus_root = (args.corpus_root or root).resolve()
        if args.command == "build":
            return build(args, root)
        if args.repeats < 1:
            raise ValueError("Repeats must be positive")
        selected = bench.select_inputs(args.manifest, args.corpus_root, args.files)
        args.experiment_dll = validate_experiment_path(args.experiment_dll)
        args.output = args.output.resolve()
        protected = protected_paths(args.manifest, args.corpus_root, args.experiment_dll)
        bench.guard_destination(args.output, protected)
        if args.output.suffix.lower() == ".ahps":
            raise ValueError("This experiment never emits AHPS archives")
        corpus_paths = bench.protected_manifest_paths(args.manifest, args.corpus_root)
        guard_implementation_inputs(corpus_paths, args.experiment_dll)
        compiler = Path(read_build_metadata(args.experiment_dll)["compiler"]["path"])
        bench.guard_destination(compiler, corpus_paths)
        bench.guard_destination(args.output, protected_paths(
            args.manifest, args.corpus_root, args.experiment_dll, compiler))
        interpreter = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        failures = 0
        with bench.RunLock(root / ".ahps-benchmark.lock"):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            batch_id = str(uuid.uuid4())
            for file_id, source in selected:
                for variant in ABIS:
                    for transform in TRANSFORMS:
                        for repeat in range(1, args.repeats + 1):
                            row = one_run(args, file_id, source, variant, transform, repeat, batch_id, interpreter)
                            with args.output.open("a", encoding="utf-8") as output:
                                output.write(json.dumps(row) + "\n")
                                output.flush()
                                os.fsync(output.fileno())
                            failures += row["status"] != "ok"
                            print(f"{file_id} {variant}/{transform} {repeat}: {row['status']}", flush=True)
        return 1 if failures else 0
    except Exception as exc:
        print("Predicate experiment refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
