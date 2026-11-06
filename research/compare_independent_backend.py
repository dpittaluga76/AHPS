#!/usr/bin/env python3
"""Immutable complete-file AHRC comparison with production AHPS as reference only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.compare_relational import (
    check_identity, check_runtime, extract_exception_table, identity, now,
    read_json, runtime, sha, shared_lock, write_bytes, write_json,
)
from research.compare_relational_corpus import (
    BELOW_NORMAL, CORPUS, FILENAMES, check_core, inventory, priority_class,
    set_below_normal_priority,
)
from research.compare_relational_hybrid import check_restored, check_timing, marker, read_partial

DIRECTORY = ROOT / "experiments" / "independent-backend-20261103"
REPORT = ROOT / "reports" / "independent-backend-20261103.json"
PRIOR_REPORT = ROOT / "reports" / "relational-hybrid-20261103.json"
BUILD_MANIFEST = ROOT / "research" / "ahrc_backend_build.json"
BACKEND_DLL = ROOT / "research" / "ahrc_backend.dll"
FILE_CAP = 128 * 1024**2
TABLE_CAP = 8 * 1024**2
HEADER_BYTES = 52
VARIANTS = ("raw", "exe", "table_byte", "table_word")
ENCODE_METHODS = ("ahps",) + VARIANTS
METHODS = ENCODE_METHODS + ("selected",)
LIMITS = {"input_worker_seconds": 300, "batch_seconds": 900,
          "job_committed_bytes": 2 * 1024**3}
SETTINGS = {"ahps_preset": 6, "ahps_block_bytes": 4096, "ahps_features": "DEFAULT_FEATURES",
            "ahps_search": {"chunk_trial_preset": 1, "exe32_chunk_rival": False},
            "file_cap_bytes": FILE_CAP, "table_cap_bytes": TABLE_CAP,
            "backend_abi": 1, "transform_abi": 206, "header_bytes": HEADER_BYTES,
            "magic_hex": b"AHRC\0\0\0\1".hex(), "selection_order": list(VARIANTS),
            "window_bytes": 8 * 1024**2, "hash_heads": 1 << 19, "chain_probes": 32,
            "minimum_match": 4, "maximum_match": 65535, "table_stride": 12}
BASE_SOURCES = (
    "ahps.py", "ahps_simd.c", "ahps_simd.dll", "benchmark_ahps.py",
    "research/__init__.py", "research/relational.py", "research/relational_file.py",
    "research/relational_hybrid.py", "research/compare_relational.py",
    "research/compare_relational_corpus.py", "research/compare_relational_hybrid.py",
    "research/ahrc_backend.c", "research/ahrc_backend.dll", "research/build_ahrc_backend.py",
    "research/ahrc_backend_build.json", "research/independent_backend.py",
    "research/compare_independent_backend.py", "research/job_runner.py",
    "docs/independent-backend-experiment.md", "docs/format.md",
    "reports/evaluation-protocol.md", "corpus_manifest.json",
    "reports/relational-hybrid-20261103.json",
)
GUARD_FUNCTIONS = {
    "lzma": ("compress", "decompress", "LZMACompressor", "LZMADecompressor"),
    "zlib": ("compress", "decompress", "compressobj", "decompressobj"),
    "bz2": ("compress", "decompress", "BZ2Compressor", "BZ2Decompressor"),
    "ahps": ("ahps_compress_bytes", "ahps_decompress_bytes", "ahps_decompress_file",
             "plan_segments", "chunked_plan", "best_plan", "rival_plans", "refine_chain",
             "py_score_block", "pack_container", "xz_compress", "cmd_compress"),
}
GUARD_NAMES = [module + "." + function for module, functions in GUARD_FUNCTIONS.items() for function in functions]
GUARD_NAMES += ["ahps.NativeSIMDBridge.score_all", "ahps.NativeSIMDBridge.estimate"]
META_FIELDS = ("representation", "backend_mode", "source_bytes", "payload_bytes", "archive_bytes",
               "table_start", "table_length", "source_crc32", "residual_crc32")


def source_names():
    tests = sorted(path.name for path in ROOT.glob("test_*.py"))
    if "test_independent_backend.py" not in tests:
        raise ValueError("Independent-backend regression tests are required before freezing")
    return list(BASE_SOURCES) + tests


def identity_objects(value):
    """Collect the build manifest's concrete compiler/tool/runtime dependencies."""
    result = {}
    def visit(item):
        if isinstance(item, dict):
            if set(("path", "bytes", "sha256")).issubset(item):
                pin = {key: item[key] for key in ("path", "bytes", "sha256")}
                if pin["path"] in result and result[pin["path"]] != pin:
                    raise ValueError("Conflicting native-build identities")
                result[pin["path"]] = pin
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return [result[key] for key in sorted(result)]


def check_build():
    build = read_json(BUILD_MANIFEST)
    if build["backend_abi"] != 1 or build["priority_class"] != BELOW_NORMAL:
        raise ValueError("Native backend build ABI/priority differs from protocol")
    for key, path in (("source", ROOT / "research" / "ahrc_backend.c"),
                      ("dll", BACKEND_DLL), ("build_script", ROOT / "research" / "build_ahrc_backend.py")):
        if build[key] != identity(path):
            raise ValueError("Native build source/output identity mismatch")
    dependencies = identity_objects(build)
    for pin in dependencies:
        check_identity(pin)
    if not build.get("compiler_version") or not build.get("command") or not dependencies:
        raise ValueError("Incomplete native build evidence")
    inspection = build["dependency_inspection"]
    imports = [name.lower() for name in inspection["imported_dll_names"]]
    if not imports or any(any(codec in name for codec in ("lzma", "zlib", "zstd", "bz2", "bzip", "lz4")) for name in imports):
        raise ValueError("Native dependency inspection is missing or imports an external codec")
    lib = ctypes.CDLL(str(BACKEND_DLL))
    lib.ahrc_version.argtypes = []
    lib.ahrc_version.restype = ctypes.c_uint32
    if lib.ahrc_version() != 1:
        raise ValueError("Actual native backend ABI is not 1")
    return {"manifest": identity(BUILD_MANIFEST), "identities": dependencies,
            "imported_dll_names": inspection["imported_dll_names"], "backend_abi": 1}


def historical_baselines():
    previous = read_json(PRIOR_REPORT)
    if tuple(previous["freeze"]["input_ids"]) != FILENAMES:
        raise ValueError("Historical report does not cover the exact five-file cohort")
    result = {}
    for name in FILENAMES:
        rows = [row for row in previous["records"] if row["input"] == name and row["method"] == "ahps"]
        if len(rows) != 1 or rows[0]["status"] != "ok":
            raise ValueError("A completed historical AHPS baseline is required for every file")
        row = rows[0]
        check_identity(row["archive"])
        result[name] = {"archive": row["archive"], "source_bytes": row["source_bytes"],
                        "source_sha256": row["source_sha256"]}
    return result


def manifest_entries():
    selected = {}
    paths = {"corpus_development/" + name for name in FILENAMES}
    for item in read_json(ROOT / "corpus_manifest.json")["development"]:
        if item.get("path") in paths:
            name = Path(item["path"]).name
            if item.get("split") != "development" or name in selected:
                raise ValueError("Invalid development manifest entry")
            selected[name] = item
    if tuple(sorted(selected)) != FILENAMES:
        raise ValueError("Manifest must identify exactly the five complete development inputs")
    return selected


def prepare(directory):
    controller_priority = set_below_normal_priority()
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "experiments") or directory.exists() or REPORT.exists():
        raise ValueError("Use a new artifact directory; no overwrite or resume")
    with shared_lock():
        check_core()
        inventory()
        build = check_build()
        history = historical_baselines()
        pins = [identity(ROOT / name) for name in source_names()]
        run_runtime = runtime()
        manifest = manifest_entries()
        originals = []
        for name in FILENAMES:
            parent = identity(CORPUS / name)
            expected, old = manifest[name], history[name]
            if ((parent["bytes"], parent["sha256"]) != (expected["bytes"], expected["sha256"])
                    or (parent["bytes"], parent["sha256"]) != (old["source_bytes"], old["source_sha256"])
                    or parent["bytes"] > FILE_CAP):
                raise ValueError("Input differs from manifest/historical baseline or exceeds cap")
            originals.append(parent)
        directory.mkdir(parents=True)
        snapshots = []
        for pin in pins:
            target = directory / "frozen" / Path(pin["path"]).relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_bytes(target, Path(pin["path"]).read_bytes())
            saved = identity(target)
            check_identity(pin)
            if (saved["bytes"], saved["sha256"]) != (pin["bytes"], pin["sha256"]):
                raise ValueError("Source changed while snapshotting")
            snapshots.append({"live": pin, "snapshot": saved})
        (directory / "inputs").mkdir()
        inputs = []
        for name, parent in zip(FILENAMES, originals):
            target = directory / "inputs" / name
            with Path(parent["path"]).open("rb") as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest, length=1024**2)
            snapshot = identity(target)
            check_identity(parent)
            if (snapshot["bytes"], snapshot["sha256"]) != (parent["bytes"], parent["sha256"]):
                raise ValueError("Complete input snapshot differs from parent")
            item = {"id": name, "manifest_id": manifest[name]["id"], "source": parent,
                    "input": snapshot, "scope": "complete file", "table_status": "available"}
            try:
                table, region = extract_exception_table(target, max_bytes=TABLE_CAP)
                if (not 0 < len(table) <= TABLE_CAP or len(table) % 12 or region["bytes"] != len(table)
                        or not 0 <= region["file_offset"] <= snapshot["bytes"] - len(table)):
                    raise ValueError("Invalid complete exception-table bounds")
                item.update(region=region, table_sha256=sha(table))
                del table
            except (ValueError, OSError) as exc:
                item.update(table_status="unavailable", table_reason=f"{type(exc).__name__}: {exc}")
            check_identity(snapshot)
            check_identity(parent)
            inputs.append(item)
        freeze = {"schema": 1, "prepared_utc": now(), "baseline_main_commit": "0dac8d2",
                  "research_checkpoint": "3788ed9", "git_head": subprocess.check_output(
                      ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                  "corpus_directory": str(CORPUS.resolve()), "input_ids": list(FILENAMES), "methods": list(METHODS),
                  "pins": pins, "snapshots": snapshots, "runtime": run_runtime, "inputs": inputs,
                  "settings": SETTINGS, "limits": LIMITS, "build": build,
                  "historical_report": identity(PRIOR_REPORT), "historical_baselines": history,
                  "preparation_priority_class": controller_priority, "worker_priority_class": BELOW_NORMAL,
                  "owner_using_pc": True, "report_path": str(REPORT), "guarded_entrypoints": GUARD_NAMES,
                  "timing_scope": "Own candidate encode API component sum plus selection; excludes AHPS, verification and artifact I/O; not a separately timed convenience API",
                  "memory_scope": "Shared Job peaks across methods, not per-method peaks",
                  "comparison": "Independent AHRC archive versus frozen AHPS; AHPS is never an output fallback"}
        verify_freeze(freeze)
        write_json(directory / "freeze.json", freeze)
        print(json.dumps({"prepared": str(directory), "inputs": [{"id": i["id"], "bytes": i["input"]["bytes"],
              "table_status": i["table_status"], "table_bytes": i.get("region", {}).get("bytes")} for i in inputs]}))


def verify_freeze(freeze, selected=None):
    check_core()
    check_runtime(freeze["runtime"])
    if (tuple(freeze["input_ids"]) != FILENAMES or tuple(i["id"] for i in freeze["inputs"]) != FILENAMES
            or tuple(freeze["methods"]) != METHODS or freeze["settings"] != SETTINGS or freeze["limits"] != LIMITS
            or freeze["worker_priority_class"] != BELOW_NORMAL or freeze["preparation_priority_class"] != BELOW_NORMAL
            or freeze["owner_using_pc"] is not True or freeze["guarded_entrypoints"] != GUARD_NAMES
            or freeze["corpus_directory"] != str(CORPUS.resolve()) or freeze["report_path"] != str(REPORT)):
        raise ValueError("Frozen settings, identities or schedule differ")
    if [pin["path"] for pin in freeze["pins"]] != [str((ROOT / name).resolve()) for name in source_names()]:
        raise ValueError("Frozen source/test inventory differs")
    if len(freeze["pins"]) != len(freeze["snapshots"]):
        raise ValueError("Incomplete source snapshots")
    for pin, pair in zip(freeze["pins"], freeze["snapshots"]):
        check_identity(pin)
        check_identity(pair["snapshot"])
        if pair["live"] != pin or (pin["bytes"], pin["sha256"]) != (pair["snapshot"]["bytes"], pair["snapshot"]["sha256"]):
            raise ValueError("Source snapshot mismatch")
    if freeze["build"] != check_build():
        raise ValueError("Native build/compiler/dependency identities changed")
    check_identity(freeze["historical_report"])
    if freeze["historical_report"] != identity(PRIOR_REPORT) or freeze["historical_baselines"] != historical_baselines():
        raise ValueError("Historical AHPS evidence changed")
    inventory()
    manifest = manifest_entries()
    for item in freeze["inputs"]:
        expected = manifest[item["id"]]
        if (item["table_status"] not in ("available", "unavailable") or item["manifest_id"] != expected["id"]
                or item["source"]["path"] != str((CORPUS / item["id"]).resolve())
                or (item["source"]["bytes"], item["source"]["sha256"]) != (expected["bytes"], expected["sha256"])
                or (item["input"]["bytes"], item["input"]["sha256"]) != (expected["bytes"], expected["sha256"])
                or item["input"]["bytes"] > FILE_CAP):
            raise ValueError("Frozen input no longer agrees with manifest")
        if selected is None or selected == item["id"]:
            check_identity(item["source"])
            check_identity(item["input"])


@contextmanager
def independence_guard():
    """Fail on external codecs or old-planner entry points during own API calls."""
    import importlib
    from contextlib import ExitStack
    state = {"entered": True, "exited": False, "attempted_calls": [], "entrypoints": GUARD_NAMES}
    def forbidden(name):
        def fail(*args, **kwargs):
            state["attempted_calls"].append(name)
            raise RuntimeError("Independent backend invoked a forbidden entry point: " + name)
        return fail
    with ExitStack() as stack:
        for name, functions in GUARD_FUNCTIONS.items():
            module = importlib.import_module(name)
            for function in functions:
                stack.enter_context(patch.object(module, function, forbidden(name + "." + function)))
        import ahps
        for function in ("score_all", "estimate"):
            stack.enter_context(patch.object(ahps.NativeSIMDBridge, function,
                forbidden("ahps.NativeSIMDBridge." + function)))
        try:
            yield state
        finally:
            state["exited"] = True


def guarded_call(record, phase, function, *args, **kwargs):
    with independence_guard() as state:
        record.setdefault("independence_guards", {})[phase] = state
        start = time.perf_counter()
        value = function(*args, **kwargs)
        seconds = time.perf_counter() - start
    if state["attempted_calls"]:
        raise RuntimeError("A candidate swallowed a forbidden backend/planner call")
    return value, seconds


def check_guards(record):
    phases = ("selection", "decode", "inspect") if record["method"] == "selected" else ("encode", "decode", "inspect")
    if set(record["independence_guards"]) != set(phases):
        raise ValueError("Incomplete independent-phase guard evidence")
    for state in record["independence_guards"].values():
        if (state["entered"] is not True or state["exited"] is not True or state["attempted_calls"]
                or state["entrypoints"] != GUARD_NAMES):
            raise ValueError("Independent API called a prohibited codec/planner entry point")


def check_selection(rows, table_available):
    eligible = VARIANTS if table_available else VARIANTS[:2]
    sizes = {name: rows[name]["archive"]["bytes"] for name in eligible}
    expected = min(eligible, key=lambda name: sizes[name])
    selected = rows["selected"]
    if (selected["selected_representation"] != expected or selected["candidate_bytes"] != sizes
            or selected["archive"]["bytes"] != sizes[expected]
            or selected["archive"]["sha256"] != rows[expected]["archive"]["sha256"]
            or selected["metadata"]["representation"] != expected):
        raise ValueError("Selected output differs from the stable independent-candidate minimum")
    return expected


def check_metadata(metadata, item, representation, archive):
    if (metadata["representation"] != representation or metadata["backend_mode"] not in ("stored", "coded")
            or metadata["source_bytes"] != item["input"]["bytes"]
            or metadata["archive_bytes"] != archive["bytes"]
            or metadata.get("archive_sha256", archive["sha256"]) != archive["sha256"]
            or metadata["payload_bytes"] + HEADER_BYTES != archive["bytes"]
            or archive["bytes"] > item["input"]["bytes"] + HEADER_BYTES):
        raise ValueError("Independent archive/header identity or stored-size bound mismatch")
    size = metadata["payload_bytes"]
    if ((metadata["backend_mode"] == "stored" and size != item["input"]["bytes"])
            or (metadata["backend_mode"] == "coded" and size >= item["input"]["bytes"])):
        raise ValueError("Backend stored/coded choice violates strict payload improvement")
    table = (item["region"]["file_offset"], item["region"]["bytes"]) if representation.startswith("table_") else (0, 0)
    if (metadata["table_start"], metadata["table_length"]) != table:
        raise ValueError("Serialized representation uses incorrect table bounds")


def worker(directory, input_id):
    actual_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze, input_id)
    import ahps
    from research import independent_backend as codec
    backend, transforms = codec.backend_library(), codec.transform_library()
    if (ahps.SIMD_BRIDGE.lib is None or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206
            or backend.ahrc_version() != 1 or transforms.ahps_abi_version() != 206):
        raise RuntimeError("Required actual native ABIs are unavailable")
    native, custom_native = identity(transforms._name), identity(backend._name)
    if (native != identity(ROOT / "ahps_simd.dll") or identity(ahps.SIMD_BRIDGE.lib._name) != native
            or custom_native != identity(BACKEND_DLL)):
        raise RuntimeError("Actual native DLL differs from the frozen pin")
    item = next(i for i in freeze["inputs"] if i["id"] == input_id)
    data = Path(item["input"]["path"]).read_bytes()
    check_restored(data, item["input"])
    available = item["table_status"] == "available"
    if available:
        region = item["region"]
        if sha(memoryview(data)[region["file_offset"]:region["file_offset"] + region["bytes"]]) != item["table_sha256"]:
            raise ValueError("Table bytes differ from frozen extraction")
    attempt = directory / "runs" / input_id
    write_json(attempt / "worker-started.json", {"pid": os.getpid(), "started_utc": now(), "runtime": runtime(),
               "native": native, "custom_native": custom_native, "abi": 206, "backend_abi": 1,
               "input": item["input"], "priority_class": actual_priority, "priority_set_before_heavy_work": True})
    archives, rows = {}, {}
    failed = False
    for method in METHODS:
        path = attempt / method
        path.mkdir()
        record = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                  "started_utc": now(), "source_bytes": len(data), "source_sha256": item["input"]["sha256"],
                  "complete_file": True, "table_status": item["table_status"]}
        write_json(path / "started.json", record)
        try:
            if failed:
                record.update(status="unstarted_dependency_error", reason="An earlier required phase failed")
            elif method in VARIANTS[2:] and not available:
                record.update(status="unavailable_table", reason=item["table_reason"])
            else:
                record["priority_before_encode"] = priority_class()
                marker(attempt / "phase.json", method + ":encode")
                if method == "ahps":
                    start = time.perf_counter()
                    archive, ahps_report = ahps.ahps_compress_bytes(data, block_size=4096,
                        force_python=False, lzma_preset=6, search=ahps.FAST_CHUNKS)
                    record["encode_api_seconds"] = time.perf_counter() - start
                    record.update(actual_archive_encode=True, ahps_report=dataclasses.asdict(ahps_report))
                elif method in VARIANTS:
                    kwargs = ({"table_start": region["file_offset"], "table_length": region["bytes"]}
                              if method in VARIANTS[2:] else {})
                    (archive, metadata), seconds = guarded_call(record, "encode", codec.encode_variant, data, method, **kwargs)
                    record.update(actual_archive_encode=True, encode_api_seconds=seconds, metadata=metadata)
                else:
                    marker(attempt / "phase.json", method + ":selection")
                    (archive, selected), seconds = guarded_call(record, "selection", codec.select_archive, archives)
                    record.update(actual_archive_encode=False, selection_api_seconds=seconds,
                                  selected_representation=selected,
                                  candidate_bytes={name: len(value) for name, value in archives.items()})
                    components = {name: rows[name]["encode_api_seconds"] for name in archives}
                    components["selection"] = seconds
                    record["encode_component_seconds"] = components
                    record["encode_component_sum_seconds"] = sum(components.values())
                    record["timing_note"] = "Own trial API component sum plus selection; excludes AHPS, verification, metadata-only serialization and artifact I/O; not a separately measured convenience API"
                write_bytes(path / "archive.bin", archive)
                record["archive"] = identity(path / "archive.bin")
                record["priority_before_decode"] = priority_class()
                marker(attempt / "phase.json", method + ":decode")
                if method == "ahps":
                    record["decoder_api"] = "ahps.ahps_decompress_bytes"
                    start = time.perf_counter()
                    restored = ahps.ahps_decompress_bytes(archive, force_python=False,
                        limits=ahps.DecodeLimits(max_dict_bytes=128 * 1024**2,
                            max_container_bytes=FILE_CAP + 2 * 1024**2, max_output_bytes=len(data)))
                    record["decode_api_seconds"] = time.perf_counter() - start
                else:
                    record["decoder_api"] = "research.independent_backend.decode"
                    restored, seconds = guarded_call(record, "decode", codec.decode, archive, max_output_bytes=len(data))
                    record["decode_api_seconds"] = seconds
                marker(attempt / "phase.json", method + ":verification")
                check_restored(restored, item["input"])
                write_bytes(path / "restored.bin", restored)
                record.update(restored=identity(path / "restored.bin"), roundtrip_verified=True)
                del restored
                if method == "ahps":
                    prior = freeze["historical_baselines"][input_id]["archive"]
                    if (record["archive"]["bytes"], record["archive"]["sha256"]) != (prior["bytes"], prior["sha256"]):
                        raise ValueError("Fresh AHPS differs from the frozen historical archive")
                    record["historical_archive_match"] = True
                else:
                    inspected, seconds = guarded_call(record, "inspect", codec.inspect_archive, archive, verify_crc=False)
                    record["inspect_api_seconds"] = seconds
                    record["inspected_metadata"] = inspected
                    if method == "selected":
                        record["metadata"] = inspected
                    elif (record["metadata"]["archive_sha256"] != record["archive"]["sha256"]
                          or any(record["metadata"][key] != inspected[key] for key in META_FIELDS)):
                        raise ValueError("Encoded metadata disagrees with serialized archive header")
                    representation = record["selected_representation"] if method == "selected" else method
                    check_metadata(record["metadata"], item, representation, record["archive"])
                    check_guards(record)
                    if method == "selected":
                        check_selection({**rows, method: record}, available)
                    else:
                        archives[method] = archive
                record.update(status="ok", priority_after_method=priority_class())
                del archive
        except Exception as exc:
            failed = True
            record.update(status="error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        record["finished_utc"] = now()
        write_json(path / "result.json", record)
        rows[method] = record
        print(json.dumps({"input": input_id, "method": method, "status": record["status"],
                          "archive_bytes": record.get("archive", {}).get("bytes"),
                          "backend_mode": record.get("metadata", {}).get("backend_mode")}), flush=True)
    verify_freeze(freeze, input_id)
    write_json(attempt / "worker-finished.json", {"status": "error" if failed else "ok", "finished_utc": now(),
               "pid": os.getpid(), "identities_after": "verified", "priority_class": priority_class()})
    return 1 if failed else 0


def run(directory):
    controller_priority = set_below_normal_priority()
    from research.job_runner import run_job
    freeze_path = directory / "freeze.json"
    freeze = read_json(freeze_path)
    with shared_lock():
        verify_freeze(freeze)
        runs = directory / "runs"
        runs.mkdir()
        start = time.monotonic()
        deadline = start + LIMITS["batch_seconds"]
        batch = {"started_utc": now(), "freeze": identity(freeze_path), "status": "running",
                 "started_monotonic": start, "deadline_monotonic": deadline,
                 "controller_priority_before": controller_priority, "identities_before": "verified",
                 "schedule": list(FILENAMES), "records": []}
        write_json(runs / "started.json", batch)
        try:
            for name in FILENAMES:
                priority_class()
                attempt = runs / name
                attempt.mkdir()
                if time.monotonic() >= deadline:
                    result = {"input": name, "status": "unstarted_batch_deadline"}
                else:
                    argv = [freeze["runtime"]["artifacts"]["python_exe"]["path"], "-I", "-B",
                            str(Path(__file__).resolve()), "worker", "--directory", str(directory), "--input", name]
                    result = run_job(argv, cwd=ROOT, deadline=min(deadline, time.monotonic() + LIMITS["input_worker_seconds"]),
                        memory_limit_bytes=LIMITS["job_committed_bytes"], phase_file=attempt / "phase.json",
                        stdout_path=attempt / "stdout.log", stderr_path=attempt / "stderr.log")
                    result["input"] = name
                write_json(attempt / "job.json", result)
                batch["records"].append({"input": name, "status": result["status"], "job": identity(attempt / "job.json")})
                print(json.dumps({"input": name, "status": result["status"], "elapsed_seconds": result.get("elapsed_seconds")}), flush=True)
            verify_freeze(freeze)
            batch.update(identities_after="verified", controller_priority_after=priority_class(),
                         status="ok" if all(r["status"] == "ok" for r in batch["records"]) else "incomplete_or_failed")
        except BaseException as exc:
            batch.update(status="interrupted_or_error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            batch.update(finished_utc=now(), elapsed_seconds=time.monotonic() - start)
            write_json(runs / "finished.json", batch)
    return 0 if batch["status"] == "ok" else 1


def summarize(directory):
    import math
    controller_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    with shared_lock():
        verify_freeze(freeze)
        batch = read_partial(directory / "runs" / "finished.json")
        if batch is None or batch.get("status") == "unreadable_result":
            started = read_json(directory / "runs" / "started.json")
            batch = {**started, "status": "incomplete_controller", "partial_finished": batch}
        check_identity(batch["freeze"])
        if (tuple(batch["schedule"]) != FILENAMES or batch["controller_priority_before"] != BELOW_NORMAL
                or not math.isclose(batch["deadline_monotonic"] - batch["started_monotonic"], LIMITS["batch_seconds"], abs_tol=0.01)):
            raise ValueError("Controller schedule, priority or deadline mismatch")
        for entry in batch["records"]:
            check_identity(entry["job"])
        native, custom_native = identity(ROOT / "ahps_simd.dll"), identity(BACKEND_DLL)
        records, jobs, verified_inputs = [], [], set()
        previous_job_end = batch["started_monotonic"]
        for item in freeze["inputs"]:
            attempt = directory / "runs" / item["id"]
            job = read_partial(attempt / "job.json")
            if job is not None:
                jobs.append(job)
            first, last = read_partial(attempt / "worker-started.json"), read_partial(attempt / "worker-finished.json")
            if job and "pid" in job:
                if (not job.get("tree_cleaned") or not job.get("assignment_before_resume") or job.get("breakaway_allowed")
                        or job.get("memory_limit_bytes") != LIMITS["job_committed_bytes"]
                        or job["deadline_monotonic"] > batch["deadline_monotonic"] + 0.01
                        or job["deadline_monotonic"] - job["started_monotonic"] > LIMITS["input_worker_seconds"] + 0.01
                        or job["started_monotonic"] < previous_job_end - 0.05):
                    raise ValueError("Job resource, serial schedule, deadline or cleanup mismatch")
                previous_job_end = job["started_monotonic"] + job["elapsed_seconds"]
            if first and first.get("status") != "unreadable_result":
                if (job is None or first["pid"] != job.get("pid") or first["runtime"] != freeze["runtime"]
                        or first["native"] != native or first["custom_native"] != custom_native
                        or first["abi"] != 206 or first["backend_abi"] != 1 or first["input"] != item["input"]
                        or first["priority_class"] != BELOW_NORMAL or not first.get("priority_set_before_heavy_work")):
                    raise ValueError("Worker native/runtime/source identity or priority mismatch")
            if job and job.get("status") == "ok":
                if (not first or not last or first.get("status") == "unreadable_result" or last.get("status") != "ok"
                        or last["pid"] != job["pid"] or last.get("identities_after") != "verified"
                        or last["priority_class"] != BELOW_NORMAL or job.get("returncode") != 0):
                    raise ValueError("Successful Job lacks verified worker completion")
                verified_inputs.add(item["id"])
            for method in METHODS:
                row = read_partial(attempt / method / "result.json")
                if row is None or row.get("status") == "unreadable_result":
                    records.append({"input": item["id"], "method": method,
                        "status": "no_result" if row is None else "unreadable_result",
                        "job_status": job.get("status") if job else "unstarted",
                        "started": read_partial(attempt / method / "started.json"), "partial_result": row})
                    continue
                if row["input"] != item["id"] or row["method"] != method or job is None or row["pid"] != job.get("pid"):
                    raise ValueError("Result does not belong to scheduled worker")
                for key in ("archive", "restored"):
                    if key in row:
                        check_identity(row[key])
                if row["status"] == "ok":
                    if (not row.get("roundtrip_verified") or not row.get("complete_file")
                            or (row["source_bytes"], row["source_sha256"]) != (item["input"]["bytes"], item["input"]["sha256"])
                            or (row["restored"]["bytes"], row["restored"]["sha256"]) != (item["input"]["bytes"], item["input"]["sha256"])
                            or any(row[key] != BELOW_NORMAL for key in ("priority_before_encode", "priority_before_decode", "priority_after_method"))):
                        raise ValueError("Full-file restoration or method priority mismatch")
                    check_timing(row)
                    if row.get("actual_archive_encode") is not (method != "selected"):
                        raise ValueError("Archive-encode versus selection accounting mismatch")
                    expected_decoder = "ahps.ahps_decompress_bytes" if method == "ahps" else "research.independent_backend.decode"
                    if row["decoder_api"] != expected_decoder:
                        raise ValueError("Unexpected decoder path")
                    if method == "ahps":
                        prior = freeze["historical_baselines"][item["id"]]["archive"]
                        if (row.get("historical_archive_match") is not True
                                or (row["archive"]["bytes"], row["archive"]["sha256"]) != (prior["bytes"], prior["sha256"])):
                            raise ValueError("Fresh AHPS baseline differs from historical archive")
                    else:
                        check_guards(row)
                        rep = row["selected_representation"] if method == "selected" else method
                        check_metadata(row["metadata"], item, rep, row["archive"])
                        if method in VARIANTS and row["metadata"]["archive_sha256"] != row["archive"]["sha256"]:
                            raise ValueError("Encoded archive hash differs from saved artifact")
                        if any(row["metadata"][key] != row["inspected_metadata"][key] for key in META_FIELDS):
                            raise ValueError("Recorded metadata differs from inspected archive")
                records.append(row)
        per_input = []
        for item in freeze["inputs"]:
            rows = {r["method"]: r for r in records if r["input"] == item["id"] and r["status"] == "ok"}
            available = item["table_status"] == "available"
            eligible = VARIANTS if available else VARIANTS[:2]
            required = ("ahps",) + eligible + ("selected",)
            done = all(name in rows for name in required) and item["id"] in verified_inputs
            result = {"input": item["id"], "source_bytes": item["input"]["bytes"], "table_status": item["table_status"],
                      "status": ("ok" if available else "complete_without_table") if done else "incomplete",
                      "archive_bytes": {name: row["archive"]["bytes"] for name, row in rows.items()},
                      "backend_modes": {name: row["metadata"]["backend_mode"] for name, row in rows.items() if name != "ahps"}}
            sizes = result["archive_bytes"]
            if "raw" in rows and "ahps" in rows:
                result["raw_saving_vs_ahps_percent"] = 100 * (1 - sizes["raw"] / sizes["ahps"])
            if "raw" in rows and "exe" in rows:
                result["exe_saving_vs_raw_bytes"] = sizes["raw"] - sizes["exe"]
            if "table_byte" in rows and "table_word" in rows:
                result.update(word_saving_vs_table_byte_bytes=sizes["table_byte"] - sizes["table_word"],
                              word_saving_vs_table_byte_percent=100 * (1 - sizes["table_word"] / sizes["table_byte"]))
            if all(name in rows for name in required):
                winner = check_selection(rows, available)
                selected = rows["selected"]
                components = {name: rows[name]["encode_api_seconds"] for name in eligible}
                components["selection"] = selected["selection_api_seconds"]
                if selected["encode_component_seconds"] != components or selected["encode_component_sum_seconds"] != sum(components.values()):
                    raise ValueError("Candidate component sum includes incorrect work or omits eligible trials")
                result.update(selected_representation=winner, selected_minimum_verified=True,
                    selected_saving_vs_ahps_bytes=sizes["ahps"] - sizes["selected"],
                    selected_saving_vs_ahps_percent=100 * (1 - sizes["selected"] / sizes["ahps"]),
                    selected_saving_vs_raw_bytes=sizes["raw"] - sizes["selected"],
                    selected_encode_component_sum_seconds=selected["encode_component_sum_seconds"])
            per_input.append(result)
        complete = (len(records) == 30 and all(r["status"] == "ok" for r in records) and len(verified_inputs) == 5
                    and batch["status"] == "ok" and batch.get("identities_after") == "verified"
                    and batch.get("controller_priority_after") == BELOW_NORMAL)
        totals = None
        if complete:
            sizes = {method: sum(row["archive"]["bytes"] for row in records if row["method"] == method) for method in METHODS}
            totals = {"source_bytes": sum(i["input"]["bytes"] for i in freeze["inputs"]), "archive_bytes": sizes,
                      "selected_saving_vs_ahps_bytes": sizes["ahps"] - sizes["selected"],
                      "selected_saving_vs_ahps_percent": 100 * (1 - sizes["selected"] / sizes["ahps"]),
                      "selected_saving_vs_raw_bytes": sizes["raw"] - sizes["selected"],
                      "word_saving_vs_table_byte_bytes": sizes["table_byte"] - sizes["table_word"],
                      "scope": "Summed complete archives on five reused development files only"}
        report = {"schema": 1, "created_utc": now(), "freeze": freeze, "batch": batch, "jobs": jobs,
                  "records": records, "per_input": per_input, "totals": totals,
                  "planned_archive_encodes": 25, "planned_output_roundtrips": 30,
                  "completed_archive_encodes": sum(r.get("actual_archive_encode") is True and "encode_api_seconds" in r for r in records),
                  "completed_output_roundtrips": sum(r.get("roundtrip_verified") is True for r in records),
                  "all_cells_verified": complete, "summary_controller_priority_class": controller_priority,
                  "verdict": "complete_independent_backend_screen" if complete else "incomplete_or_failed",
                  "limitations": ["Five reused development files, not independent holdouts or general compression evidence.",
                      "Selected output always uses AHRC; AHPS is comparison-only and losses against it are preserved.",
                      "Stored-mode outcomes are explicit; a working independent backend is not proof it beats AHPS or LZMA2.",
                      "Exe versus table modes changes boundaries; only table byte versus word matches those boundaries.",
                      "Own encode component sums exclude AHPS, verification, metadata-only serialization and artifact I/O.",
                      "PC use confounds single-pass timing; shared Job peaks are not per-method memory measurements.",
                      "No automatic retuning, production adoption, corpus expansion or online research."]}
        verify_freeze(freeze)
        write_json(REPORT, report)
        print(json.dumps({"report": str(REPORT), "verdict": report["verdict"], "encodes": report["completed_archive_encodes"],
                          "roundtrips": report["completed_output_roundtrips"], "per_input": per_input, "totals": totals}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "worker", "summarize"))
    parser.add_argument("--directory", type=Path, default=DIRECTORY)
    parser.add_argument("--input", choices=FILENAMES)
    args = parser.parse_args()
    set_below_normal_priority()
    directory = args.directory.resolve()
    if args.command == "prepare":
        prepare(directory)
    elif args.command == "run":
        return run(directory)
    elif args.command == "summarize":
        summarize(directory)
    else:
        if args.input is None:
            parser.error("worker requires --input")
        return worker(directory, args.input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
