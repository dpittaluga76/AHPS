#!/usr/bin/env python3
"""Serial development benchmarks. Every invocation appends fresh, isolated runs.

Example: python benchmark_ahps.py --files dickens --repeats 3 --output results.jsonl
Only explicit development manifest IDs are accepted. No corpus discovery or resume.
Primary timings include process startup, imports, input/output and process shutdown.
Encode/decode use separate processes; peaks are kernel lifetime working-set counters.
Search is explicit: omitted --search means exhaustive, including at preset 9;
it does not follow the product's automatic preset-9 Candidate A default.
"""

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid


REPO = Path(__file__).resolve().parent
CODECS = ("ahps", "xz", "xz-bcj", "7z-bcj2")
SEALED_NAMES = frozenset(("ooffice", "osdb", "xml"))
SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
SEVEN_ZIP_DICT_BYTES = 64 * 1024 * 1024
FEATURE_NAMES = ("exe32", "exe_split", "delta_split", "per_chunk")
SEARCH_DEFAULTS = {"chunk_trial_preset": None, "exe32_chunk_rival": True}
AHPS_ARTIFACT_NAMES = (
    "ahps.py", "ahps_simd.c", "ahps_simd.dll", "ahps_csv.py",
    "ahps_safetensors.py", "ahps_rans.py", "ahps_rans.c",
)
RANS_NATIVE_NAMES = ("ahps_rans.dll", "libahps_rans.so", "libahps_rans.dylib")


def parse_features(value):
    """Strict feature input: omitted keys stay enabled; values must be JSON bools."""
    try:
        supplied = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Features must be a JSON object") from exc
    if not isinstance(supplied, dict):
        raise ValueError("Features must be a JSON object")
    unknown = set(supplied) - set(FEATURE_NAMES)
    if unknown:
        raise ValueError("Unknown feature keys: " + ", ".join(sorted(unknown)))
    if any(type(enabled) is not bool for enabled in supplied.values()):
        raise ValueError("Feature values must be JSON booleans")
    return {name: supplied.get(name, True) for name in FEATURE_NAMES}


def canonical_features(effective):
    return json.dumps(effective, sort_keys=True, separators=(",", ":"))


def parse_search(value):
    """Normalize the separately selectable search method without changing features."""
    try:
        supplied = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Search must be a JSON object") from exc
    if not isinstance(supplied, dict) or set(supplied) - set(SEARCH_DEFAULTS):
        raise ValueError("Search must contain only chunk_trial_preset and exe32_chunk_rival")
    effective = {**SEARCH_DEFAULTS, **supplied}
    cap = effective["chunk_trial_preset"]
    if cap is not None and (type(cap) is not int or not 0 <= cap <= 9):
        raise ValueError("chunk_trial_preset must be null or an integer from 0 through 9")
    if type(effective["exe32_chunk_rival"]) is not bool:
        raise ValueError("exe32_chunk_rival must be a JSON boolean")
    return effective


def canonical_search(effective):
    return json.dumps(effective, sort_keys=True, separators=(",", ":"))


def selected_repetitions(args):
    """Select predeclared global slots; never renumber prior runs across batches."""
    if args.repeats < 1:
        raise ValueError("Repeats must be positive")
    comparison = args.comparison_id
    if comparison is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", comparison):
        raise ValueError("comparison-id must be a nonempty identifier of at most 128 characters")
    if args.order_start < 1:
        raise ValueError("order-start must be positive")
    if (args.repeat_indices is not None or args.order_start != 1) and comparison is None:
        raise ValueError("Explicit repetition/order scheduling requires comparison-id")
    indices = args.repeat_indices if args.repeat_indices is not None else list(range(1, args.repeats + 1))
    if len(indices) != len(set(indices)) or any(index < 1 or index > args.repeats for index in indices):
        raise ValueError("Repetition indices must be unique slots within 1..repeats")
    return indices


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def root_repo():
    # A source export may live below another checkout; never adopt its Git root.
    if not (REPO / ".git").exists():
        return REPO.resolve()
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO,
                            check=True, capture_output=True, text=True)
    common = Path(result.stdout.strip())
    return (common if common.is_absolute() else REPO / common).resolve().parent


def sealed_name(value):
    # Check both IDs and path components, including names with an extension.
    return any(part.casefold().split(".", 1)[0].strip(" .") in SEALED_NAMES
               for part in re.split(r"[\\/]", str(value)))


def read_manifest(manifest_path):
    """Only the manifest is read here; corpus path checks use metadata alone."""
    if sealed_name(manifest_path):
        raise ValueError("A sealed path cannot be used as the manifest")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported corpus manifest schema")
    if not isinstance(manifest.get("development"), list) or not isinstance(manifest.get("sealed"), list):
        raise ValueError("Manifest requires development and sealed lists")
    return manifest


def same_file(left, right):
    """Compare path/identity without opening contents, including hardlink aliases."""
    left, right = Path(left).resolve(), Path(right).resolve()
    if os.path.normcase(str(left)) == os.path.normcase(str(right)):
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


def protected_manifest_paths(manifest_path, corpus_root):
    """All declared data paths and the manifest, regardless of selected IDs."""
    manifest = read_manifest(manifest_path)
    root = Path(corpus_root).resolve()
    paths = [Path(manifest_path).resolve()]
    for entry in manifest["development"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or relative.drive:
            raise ValueError("Development paths must be relative to the corpus root")
        paths.append((root / relative).resolve())
    for entry in manifest["sealed"]:
        path = entry if isinstance(entry, str) else entry.get("path")
        if path is not None:
            # Absolute sealed paths are allowed; pathlib preserves their root.
            paths.append((root / path).resolve())
    return paths


def protected_runtime_paths(sevenz=SEVEN_ZIP, extra=()):
    """Resolve implementation paths without reading their contents."""
    sevenz = Path(sevenz)
    paths = [Path(__file__), *(REPO / name for name in (*AHPS_ARTIFACT_NAMES, *RANS_NATIVE_NAMES)),
             REPO / "ahps_progress.py",
             Path(sys.executable), Path(getattr(sys, "_base_executable", sys.executable)),
             sevenz, sevenz.with_name("7z.dll"), *map(Path, extra)]
    lzma_binary = getattr(sys.modules.get("_lzma"), "__file__", None)
    if lzma_binary is None:
        raise RuntimeError("Cannot determine the loaded _lzma binary for output protection")
    paths.append(Path(lzma_binary))
    return [path.resolve() for path in paths]


def guard_destination(destination, protected, source=None):
    paths = protected if source is None else [*protected, source]
    if sealed_name(destination) or any(same_file(destination, path) for path in paths):
        raise ValueError("Output must not overwrite a source, manifest or sealed file")


def select_inputs(manifest_path, corpus_root, ids):
    """Validate the entire selection before any caller may open corpus data."""
    manifest = read_manifest(manifest_path)
    root = Path(corpus_root).resolve()
    sealed_ids, sealed_paths = set(), set()
    for entry in manifest["sealed"]:
        if isinstance(entry, str):
            sealed_ids.add(entry.casefold())
            sealed_paths.add(os.path.normcase(str((root / entry).resolve())))
        elif isinstance(entry, dict):
            sealed_ids.add(str(entry.get("id", "")).casefold())
            if "path" in entry:
                sealed_paths.add(os.path.normcase(str((root / entry["path"]).resolve())))
        else:
            raise ValueError("Invalid sealed manifest entry")
    entries = {}
    for entry in manifest["development"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("path"), str):
            raise ValueError("Development entries require explicit string id and path")
        if entry["id"] in entries:
            raise ValueError("Duplicate development ID: " + entry["id"])
        entries[entry["id"]] = entry
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Supply one or more distinct explicit development IDs")
    selected = []
    for file_id in ids:
        if sealed_name(file_id) or file_id.casefold() in sealed_ids:
            raise ValueError("Sealed input is forbidden: " + file_id)
        if file_id not in entries:
            raise ValueError("Unknown development ID: " + file_id)
        entry = entries[file_id]
        relative = Path(entry["path"])
        if str(entry.get("split", "development")).casefold() != "development":
            raise ValueError("Non-development/holdout input is forbidden: " + file_id)
        if relative.is_absolute() or relative.drive or any(c in entry["path"] for c in "*?[]:"):
            raise ValueError("Manifest paths must be explicit relative filenames: " + file_id)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Input path leaves corpus root: " + file_id)
        if sealed_name(relative) or sealed_name(path) or any(same_file(path, sealed) for sealed in sealed_paths):
            raise ValueError("Sealed input path is forbidden: " + file_id)
        selected.append((file_id, path))
    return selected


class RunLock:
    """Kernel lock shared by every worktree, automatically released on exit/crash."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        if os.name != "nt":
            raise RuntimeError("This benchmark lock/peak implementation currently requires Windows")
        import msvcrt
        self.stream = self.path.open("a+b")
        if self.stream.seek(0, 2) == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Another benchmark owns " + str(self.path)) from exc
        return self

    def __exit__(self, *_):
        import msvcrt
        self.stream.seek(0)
        msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


def process_peak(handle=None):
    """Exact Windows peak working set, including after exit with a retained handle."""
    if os.name != "nt":
        raise RuntimeError("Reliable peak working-set measurement is implemented only on Windows")
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not query(kernel.GetCurrentProcess() if handle is None else handle, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    if counters.PeakWorkingSetSize <= 0:
        raise RuntimeError("Kernel did not return a reliable nonzero peak working set")
    return counters.PeakWorkingSetSize


def worker(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("encode", "decode"))
    parser.add_argument("codec", choices=("ahps", "xz", "xz-bcj"))
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--preset", type=int, required=True)
    parser.add_argument("--block-size", type=int, required=True)
    parser.add_argument("--features", default="{}")
    parser.add_argument("--search", default="{}")
    parser.add_argument("--decode-api", choices=("bytes", "file"), default="bytes")
    parser.add_argument("--sevenz", type=Path, default=SEVEN_ZIP)
    args = parser.parse_args(argv)
    result = {"pid": os.getpid(), "python_version": sys.version, "executable": sys.executable}
    try:
        effective = parse_features(args.features)
        search = parse_search(args.search)
        if args.codec != "ahps" and not all(effective.values()):
            raise ValueError("Nondefault features require the AHPS codec")
        if args.codec != "ahps" and search != SEARCH_DEFAULTS:
            raise ValueError("Nondefault search requires the AHPS codec")
        selected = select_inputs(args.manifest, args.corpus_root, [args.file_id])[0][1]
        protected = [*protected_manifest_paths(args.manifest, args.corpus_root),
                     *protected_runtime_paths(args.sevenz)]
        source = Path(args.source).resolve()
        destination = Path(args.destination).resolve()
        guard_destination(destination, protected, source)
        if sealed_name(source) or (args.phase == "encode" and source != selected):
            raise ValueError("Worker source is not its authorized development input")
        if args.phase == "decode" and any(same_file(source, path) for path in protected):
            raise ValueError("Worker decode source is a protected manifest/data path")
        guard_implementation_inputs(args.codec, Path(getattr(sys, "_base_executable", sys.executable)),
                                    args.sevenz, protected_manifest_paths(args.manifest, args.corpus_root))
        if args.codec == "ahps":
            from dataclasses import asdict
            sys.path.insert(0, str(REPO))
            import ahps
            if ahps.SIMD_BRIDGE.lib is None:
                raise RuntimeError("AHPS benchmark requires a loaded native kernel; refusing silent Python fallback")
            plan_features = ahps.PlanFeatures(**effective)
            search_method = ahps.SearchMethod(**search)
            result["features"] = effective
            result["search"] = search
            result["ahps"] = {"magic": ahps.MAGIC_HEADER.hex(),
                              "expected_abi": ahps.EXPECTED_ABI_VERSION,
                              "loaded_abi": ahps.SIMD_BRIDGE.lib.ahps_abi_version(),
                              "dll_path": str(Path(ahps.SIMD_BRIDGE.lib._name).resolve())}
        start = time.perf_counter()
        streamed = args.codec == "ahps" and args.phase == "decode" and args.decode_api == "file"
        data = b"" if streamed else source.read_bytes()
        output = None
        if args.codec == "ahps":
            if args.phase == "encode":
                output, report = ahps.ahps_compress_bytes(
                    data, block_size=args.block_size, lzma_preset=args.preset,
                    features=plan_features, search=search_method)
                result["programs"] = report.programs
                result["report_features"] = asdict(report.features)
                if result["report_features"] != effective:
                    raise RuntimeError("AHPS report.features does not match the requested effective features")
                result["report_search"] = asdict(report.search)
                if result["report_search"] != search:
                    raise RuntimeError("AHPS report.search does not match the requested search method")
            elif streamed:
                ahps.ahps_decompress_file(source, destination)
            else:
                output = ahps.ahps_decompress_bytes(data)
        elif args.phase == "encode":
            preset = args.preset | (lzma.PRESET_EXTREME if args.preset == 9 else 0)
            filters = ([{"id": lzma.FILTER_X86}] if args.codec == "xz-bcj" else [])
            filters.append({"id": lzma.FILTER_LZMA2, "preset": preset})
            output = lzma.compress(data, format=lzma.FORMAT_XZ, filters=filters)
        else:
            output = lzma.decompress(data, format=lzma.FORMAT_XZ)
        if output is not None:
            destination.write_bytes(output)
        result["operation_seconds"] = time.perf_counter() - start
        # Hashing is deliberately done by the coordinator after this measurement.
        result["peak_working_set_bytes"] = process_peak()
        result["peak_method"] = "GetProcessMemoryInfo(self), after read/transform/write"
        result["status"] = "ok"
    except Exception as exc:
        result.update(status="error", error=str(exc), traceback=traceback.format_exc())
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "ok" else 1


def launch(command, timeout, stdout_path=None, native_peak=False):
    """Runs exactly one phase. Retain the real child's handle through peak lookup."""
    result = {"argv": list(map(str, command)), "timing_boundary": "process launch through exit, including I/O"}
    out = Path(stdout_path).open("wb") if stdout_path else subprocess.PIPE
    start = time.perf_counter()
    process = None
    try:
        process = subprocess.Popen(result["argv"], cwd=REPO, stdout=out, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            result["error"] = "Process timed out"
        result.update(elapsed_seconds=time.perf_counter() - start, returncode=process.returncode,
                      stderr=stderr.decode("utf-8", errors="replace"))
        if native_peak:
            result["peak_working_set_bytes"] = process_peak(int(process._handle))
            result["peak_method"] = "GetProcessMemoryInfo(actual child handle), after process exit"
        elif stdout is not None:
            payload = json.loads(stdout.decode("utf-8"))
            result["worker"] = payload
            if payload["status"] == "ok":
                result["peak_working_set_bytes"] = payload["peak_working_set_bytes"]
                result["peak_method"] = payload["peak_method"]
            else:
                result["error"] = payload.get("error", "Worker failed")
        if process.returncode != 0:
            result.setdefault("error", "Process exited with code " + str(process.returncode))
    except Exception as exc:
        result.setdefault("error", str(exc))
        result["traceback"] = traceback.format_exc()
    finally:
        if stdout_path:
            out.close()
    return result


def verify_bcj2_listing(text, expected_size):
    entries = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        values = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" in values and "Size" in values and values.get("Folder") != "+":
            entries.append(values)
    if len(entries) != 1 or int(entries[0]["Size"]) != expected_size:
        raise ValueError("7z listing does not contain exactly the expected input file")
    if not re.search(r"\bBCJ2\b", entries[0].get("Method", "")):
        raise ValueError("7z archive did not actually use BCJ2")
    return entries[0]


def implementation_artifact_paths(codec, interpreter, sevenz):
    paths = [Path(__file__), REPO / "ahps_progress.py", interpreter]
    if codec == "7z-bcj2":
        paths += [sevenz, sevenz.with_name("7z.dll")]
    else:
        paths.append(Path(sys.modules["_lzma"].__file__))
        if codec == "ahps":
            paths += [REPO / name for name in AHPS_ARTIFACT_NAMES]
            paths += [REPO / name for name in RANS_NATIVE_NAMES if (REPO / name).is_file()]
    return paths


def guard_implementation_inputs(codec, interpreter, sevenz, corpus_paths):
    for path in implementation_artifact_paths(codec, interpreter, sevenz):
        try:
            guard_destination(path, corpus_paths)
        except ValueError as exc:
            raise ValueError("Implementation artifact aliases protected data: " + str(path)) from exc


def identities(codec, interpreter, sevenz):
    paths = implementation_artifact_paths(codec, interpreter, sevenz)
    info = {"python_version": sys.version}
    if codec == "7z-bcj2":
        version = subprocess.run([str(sevenz), "i"], check=True, capture_output=True, text=True).stdout
        info["7z_version"] = next(line for line in version.splitlines() if line.startswith("7-Zip "))
    info["artifacts"] = [fingerprint(path) for path in paths]
    if (REPO / ".git").exists():
        info["source_kind"] = "git_checkout"
        info["git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True,
                                            capture_output=True, text=True).stdout.strip()
    else:
        info["source_kind"] = "source_snapshot"
        info["git_commit"] = None
    return info


def one_run(args, file_id, source, codec, repeat, batch_id, interpreter):
    row = {"schema_version": 1, "batch_id": batch_id, "run_id": str(uuid.uuid4()),
           "started_utc": datetime.now(timezone.utc).isoformat(), "file_id": file_id,
           "codec": codec, "repeat": repeat, "status": "error", "phases": {},
           "settings": {"preset": 9 if codec == "7z-bcj2" else args.preset,
                        "extreme_at_9": codec != "7z-bcj2", "threads": 1,
                        "requested_repeats": args.repeats, "phase_timeout_seconds": args.timeout,
                        "cache_policy": "no cache flush; decode follows encode"}}
    if codec == "ahps":
        row["settings"]["block_size"] = args.block_size
    if codec == "7z-bcj2":
        row["settings"]["dict_bytes"] = SEVEN_ZIP_DICT_BYTES
    try:
        effective = parse_features(args.features)
        search = parse_search(getattr(args, "search", "{}"))
        if codec == "ahps":
            row["settings"]["features"] = effective
            row["settings"]["search"] = search
            if getattr(args, "decode_api", "bytes") != "bytes":
                row["settings"]["decode_api"] = args.decode_api
        authorized = select_inputs(args.manifest, args.corpus_root, [file_id])[0][1]
        if source.resolve() != authorized:
            raise ValueError("Run source is not its authorized development input")
        guard_implementation_inputs(codec, interpreter, args.sevenz,
                                    protected_manifest_paths(args.manifest, args.corpus_root))
        row["manifest"] = fingerprint(args.manifest)
        row["source"] = fingerprint(source)
        row["implementation"] = identities(codec, interpreter, args.sevenz)
        with tempfile.TemporaryDirectory(prefix="ahps-benchmark-") as temporary:
            archive, restored = Path(temporary) / "archive.7z", Path(temporary) / "restored.bin"
            for phase, src, dst in (("encode", source, archive), ("decode", archive, restored)):
                if codec == "7z-bcj2":
                    if phase == "encode":
                        command = [str(args.sevenz), "a", "-t7z", "-mx=9", "-md=64m", "-mf=BCJ2", "-mmt=1", "-ms=off",
                                   "-bd", "-bso0", "-bsp0", "-sccUTF-8", "-spd", str(archive), str(source)]
                    else:
                        command = [str(args.sevenz), "e", "-so", "-mmt=1", "-bd", "-sccUTF-8", str(archive)]
                    measured = launch(command, args.timeout, dst if phase == "decode" else None, native_peak=True)
                else:
                    command = [str(interpreter), "-I", "-B", str(Path(__file__).resolve()), "_worker", phase,
                               codec, str(src), str(dst), "--manifest", str(args.manifest), "--corpus-root",
                               str(args.corpus_root), "--file-id", file_id, "--preset", str(args.preset),
                               "--block-size", str(args.block_size), "--sevenz", str(args.sevenz)]
                    if codec == "ahps":
                        command += ["--features", canonical_features(effective)]
                        command += ["--search", canonical_search(search),
                                    "--decode-api", getattr(args, "decode_api", "bytes")]
                    measured = launch(command, args.timeout)
                row["phases"][phase] = measured
                if "error" in measured:
                    raise RuntimeError(phase + ": " + measured["error"])
                if phase == "encode" and codec == "ahps":
                    row["report_features"] = measured["worker"]["report_features"]
                    if row["report_features"] != effective:
                        raise RuntimeError("AHPS worker reported different effective features")
                    row["report_search"] = measured["worker"]["report_search"]
                    if row["report_search"] != search:
                        raise RuntimeError("AHPS worker reported a different search method")
                if phase == "encode" and codec == "7z-bcj2":
                    listing_command = [str(args.sevenz), "l", "-slt", "-sccUTF-8", str(archive)]
                    listing = subprocess.run(listing_command, capture_output=True,
                                             text=True, encoding="utf-8", timeout=args.timeout)
                    row["7z_listing"] = {"argv": listing_command, "stdout": listing.stdout, "stderr": listing.stderr}
                    if listing.returncode:
                        raise RuntimeError("7z listing failed with code " + str(listing.returncode))
                    row["7z_entry"] = verify_bcj2_listing(listing.stdout, row["source"]["bytes"])
            row["compressed_bytes"] = archive.stat().st_size
            row["compressed_sha256"] = sha256(archive)
            row["restored_sha256"] = sha256(restored)
            row["roundtrip_verified"] = row["restored_sha256"] == row["source"]["sha256"]
            if not row["roundtrip_verified"]:
                raise RuntimeError("Round-trip SHA-256 mismatch")
            if fingerprint(source) != row["source"]:
                raise RuntimeError("Source changed during the run")
            if fingerprint(args.manifest) != row["manifest"]:
                raise RuntimeError("Manifest changed during the run")
            if identities(codec, interpreter, args.sevenz) != row["implementation"]:
                raise RuntimeError("Implementation artifacts changed during the run")
            for phase, measured in row["phases"].items():
                measured["input_bytes"] = row["source"]["bytes"] if phase == "encode" else row["compressed_bytes"]
                measured["output_bytes"] = row["compressed_bytes"] if phase == "encode" else row["source"]["bytes"]
                measured["uncompressed_mib_per_second"] = (row["source"]["bytes"] / (1024 * 1024)
                                                           / measured["elapsed_seconds"])
                measured["peak_working_set_over_uncompressed"] = (
                    measured["peak_working_set_bytes"] / row["source"]["bytes"] if row["source"]["bytes"] else None)
            row["compressed_over_original"] = (row["compressed_bytes"] / row["source"]["bytes"]
                                               if row["source"]["bytes"] else None)
            row["status"] = "ok"
    except Exception as exc:
        row.update(error=str(exc), traceback=traceback.format_exc())
        if isinstance(exc, subprocess.SubprocessError):
            row["external_stdout"] = str(getattr(exc, "stdout", ""))
            row["external_stderr"] = str(getattr(exc, "stderr", ""))
    return row


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_worker":
        return worker(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", required=True, help="Explicit development manifest IDs")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--codecs", nargs="+", choices=CODECS, default=list(CODECS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--repeat-indices", type=int, nargs="+", help="Global repetition slots to execute; requires --comparison-id")
    parser.add_argument("--comparison-id", help="Stable identity shared by every bounded batch of one comparison")
    parser.add_argument("--order-start", type=int, default=1, help="First global execution position for this invocation")
    parser.add_argument("--output", type=Path, required=True, help="Append-only JSONL; existing rows are never skipped")
    parser.add_argument("--preset", type=int, choices=range(10), default=9, help="AHPS/xz preset; 9 means extreme")
    parser.add_argument("--block-size", type=int, default=4096)
    parser.add_argument("--features", default="{}", help="JSON bools for exe32/exe_split/delta_split/per_chunk; omitted keys enabled")
    parser.add_argument("--search", default="{}", help="Explicit JSON search settings; omitted means exhaustive, not product auto selection")
    parser.add_argument("--decode-api", choices=("bytes", "file"), default="bytes",
                        help="AHPS decoder to measure: ahps_decompress_bytes or ahps_decompress_file")
    parser.add_argument("--sevenz", type=Path, default=SEVEN_ZIP)
    parser.add_argument("--timeout", type=float, default=1800)
    progress_options = parser.add_mutually_exclusive_group()
    progress_options.add_argument("--progress", dest="progress", action="store_true",
                                  help="Show overall benchmark progress on stderr")
    progress_options.add_argument("--no-progress", dest="progress", action="store_false",
                                  help="Disable terminal progress")
    parser.set_defaults(progress=None)
    args = parser.parse_args(argv)
    try:
        effective = parse_features(args.features)
        search = parse_search(args.search)
        if "ahps" not in args.codecs and not all(effective.values()):
            raise ValueError("Nondefault features require AHPS in --codecs")
        if "ahps" not in args.codecs and search != SEARCH_DEFAULTS:
            raise ValueError("Nondefault search requires AHPS in --codecs")
        repetitions = selected_repetitions(args)
        if args.repeats < 1 or args.timeout <= 0 or args.block_size < 1:
            raise ValueError("Repeats, timeout and block size must be positive")
        root = root_repo()
        args.manifest = (args.manifest or root / "corpus_manifest.json").resolve()
        args.corpus_root = (args.corpus_root or root).resolve()
        selected = select_inputs(args.manifest, args.corpus_root, args.files)
        args.output = args.output.resolve()
        args.sevenz = args.sevenz.resolve()
        guard_destination(args.output, [*protected_manifest_paths(args.manifest, args.corpus_root),
                                        *protected_runtime_paths(args.sevenz)])
        guard_destination(REPO / "ahps_progress.py",
                          protected_manifest_paths(args.manifest, args.corpus_root))
        # Isolated workers never import the parent process's terminal renderer.
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from ahps_progress import TerminalProgress

        interpreter = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        failures = 0
        with RunLock(root / ".ahps-benchmark.lock"):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            batch_id = str(uuid.uuid4())
            order = args.order_start
            completed = 0
            total = len(selected) * len(args.codecs) * len(repetitions)
            with TerminalProgress("Benchmark", enabled=args.progress) as progress:
                for file_id, source in selected:
                    for codec in args.codecs:
                        for repeat in repetitions:
                            progress.update(completed / total, f"{file_id} {codec} repetition {repeat}")
                            row = one_run(args, file_id, source, codec, repeat, batch_id, interpreter)
                            if args.comparison_id is not None:
                                row["comparison_id"] = args.comparison_id
                                row["execution_order"] = order
                            order += 1
                            with args.output.open("a", encoding="utf-8") as output:
                                output.write(json.dumps(row) + "\n")
                                output.flush()
                                os.fsync(output.fileno())
                            failures += row["status"] != "ok"
                            progress.write_line(
                                f"{file_id} {codec} repetition {repeat}: {row['status']} {row.get('error', '')}"
                            )
                            completed += 1
                            progress.update(completed / total, f"{completed}/{total} runs complete")
                if failures:
                    progress.mark_failed("Completed with errors")
        return 1 if failures else 0
    except Exception as exc:
        print("Benchmark refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
