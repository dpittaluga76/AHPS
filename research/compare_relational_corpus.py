#!/usr/bin/env python3
"""Immutable complete-file AHRL comparison on the named development folder.

One below-normal worker processes each complete file under a Windows Job.
Single observations while the owner uses the PC are size/correctness evidence,
not stable throughput or per-method memory measurements.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import dataclasses
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.compare_relational import (
    check_identity, check_production, check_runtime, identity, now, read_json,
    runtime, sha, shared_lock, write_bytes, write_json,
)

FILENAMES = ("7zip-cli.exe", "chrome.exe", "git-cli.exe", "node.exe", "vlc.exe")
METHODS = ("xz", "ahrl_raw", "ahrl_auto", "ahps")
CORPUS = ROOT / "corpus_development"
DEFAULT_DIRECTORY = ROOT / "experiments" / "relational-corpus-20261103"
REPORT = ROOT / "reports" / "relational-corpus-20261103.json"
CORE_SHA256 = "6c65a1517a0c10e25261cf9eb242ab9d3e52b35f7948d31a5cd915fa53dcc041"
CHUNK_BYTES = 8_388_600
FILE_CAP = 128 * 1024**2
BELOW_NORMAL = 0x4000
LIMITS = {"batch_seconds": 900, "input_worker_seconds": 300,
          "job_committed_bytes": 2 * 1024**3}
SETTINGS = {"preset": 6, "xz_check": "CRC64", "width": 4, "fields": 3,
            "chunk_bytes": CHUNK_BYTES, "file_cap_bytes": FILE_CAP,
            "ahps_block_bytes": 4096,
            "ahps_search": {"chunk_trial_preset": 1, "exe32_chunk_rival": False}}


def priority_class(set_below_normal=False):
    if os.name != "nt":
        raise RuntimeError("This corpus comparison requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel.GetPriorityClass.restype = wintypes.DWORD
    process = kernel.GetCurrentProcess()
    if set_below_normal and not kernel.SetPriorityClass(process, BELOW_NORMAL):
        raise ctypes.WinError(ctypes.get_last_error())
    actual = int(kernel.GetPriorityClass(process))
    if not actual:
        raise ctypes.WinError(ctypes.get_last_error())
    if actual != BELOW_NORMAL:
        raise RuntimeError(f"Required BELOW_NORMAL priority, got 0x{actual:x}")
    return actual


def set_below_normal_priority():
    """Lower this process and verify 0x4000; reusable for the preflight suite."""
    return priority_class(set_below_normal=True)


def check_core():
    check_production()
    if identity(ROOT / "research" / "relational.py")["sha256"] != CORE_SHA256:
        raise ValueError("The previously tested AHRL core changed")


def inventory():
    entries = tuple(sorted(p.name for p in CORPUS.iterdir()))
    if entries != FILENAMES:
        raise ValueError("The requested corpus folder has missing or unexpected entries")
    for name in FILENAMES:
        path = CORPUS / name
        if not path.is_file() or path.is_symlink() or path.resolve().parent != CORPUS.resolve():
            raise ValueError("Unexpected non-file or redirected corpus entry: " + name)


def prepare(directory):
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "experiments"):
        raise ValueError("Artifacts must remain in this workspace's experiments directory")
    if directory.exists():
        raise FileExistsError("Preparation directory already exists; no overwrite or resume")
    if REPORT.exists():
        raise FileExistsError("Corpus report already exists; no replacement experiment")
    with shared_lock():
        check_core()
        inventory()
        sources = ["ahps.py", "ahps_simd.c", "ahps_simd.dll", "benchmark_ahps.py",
                   "research/__init__.py", "research/relational.py",
                   "research/relational_file.py", "research/compare_relational.py",
                   "research/compare_relational_corpus.py", "research/job_runner.py",
                   "docs/relational-corpus-experiment.md", "docs/format.md",
                   "reports/evaluation-protocol.md", "corpus_manifest.json"]
        tests = sorted(p.name for p in ROOT.glob("test*relational*.py"))
        if "test_relational.py" not in tests or "test_relational_file.py" not in tests:
            raise ValueError("Core and file-wrapper regression tests are required before preparation")
        sources += tests
        pins = [identity(ROOT / name) for name in sources]
        run_runtime = runtime()
        manifest = read_json(ROOT / "corpus_manifest.json")
        selected = {}
        for entry in manifest["development"]:
            if entry.get("path") in {"corpus_development/" + name for name in FILENAMES}:
                if entry.get("split") != "development":
                    raise ValueError("Non-development manifest entry")
                name = Path(entry["path"]).name
                if name in selected:
                    raise ValueError("Duplicate corpus manifest entry")
                selected[name] = entry
        if tuple(sorted(selected)) != FILENAMES:
            raise ValueError("Manifest does not identify all five exact corpus files")
        originals = []
        for name in FILENAMES:
            original = identity(CORPUS / name)
            expected = selected[name]
            if original["bytes"] != expected["bytes"] or original["sha256"] != expected["sha256"]:
                raise ValueError("Development parent differs from manifest: " + name)
            if original["bytes"] > FILE_CAP:
                raise ValueError("Complete file exceeds the declared wrapper cap: " + name)
            originals.append(original)

        directory.mkdir(parents=True)
        snapshots = []
        for pin in pins:
            relative = Path(pin["path"]).relative_to(ROOT)
            snapshot = directory / "frozen" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            write_bytes(snapshot, Path(pin["path"]).read_bytes())
            saved = identity(snapshot)
            if (saved["bytes"], saved["sha256"]) != (pin["bytes"], pin["sha256"]):
                raise ValueError("Source changed while snapshotting: " + str(relative))
            snapshots.append({"live": pin, "snapshot": saved})
        inputs_dir = directory / "inputs"
        inputs_dir.mkdir()
        inputs = []
        for name, original in zip(FILENAMES, originals):
            destination = inputs_dir / name
            with Path(original["path"]).open("rb") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024**2)
            snapshot = identity(destination)
            check_identity(original)
            if (snapshot["bytes"], snapshot["sha256"]) != (original["bytes"], original["sha256"]):
                raise ValueError("Corpus snapshot does not match its parent: " + name)
            inputs.append({"id": name, "manifest_id": selected[name]["id"],
                           "source": original, "input": snapshot, "scope": "complete file"})
        inventory()
        for original in originals:
            check_identity(original)
        freeze = {"schema": 1, "prepared_utc": now(),
                  "baseline_main_commit": "0dac8d2", "research_checkpoint": "91fc26e",
                  "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                  "corpus_directory": str(CORPUS.resolve()), "input_ids": list(FILENAMES),
                  "methods": list(METHODS), "pins": pins, "snapshots": snapshots,
                  "runtime": run_runtime, "inputs": inputs, "settings": SETTINGS,
                  "limits": LIMITS, "worker_priority_class": BELOW_NORMAL,
                  "report_path": str(REPORT), "owner_using_pc": True,
                  "interpretation": "Blind fixed three-u32 layout over every complete executable; no schema discovery or typed-region inference",
                  "timing_scope": "One encode/decode API observation per file/method, including allocations, excluding I/O and verification; owner activity and shared worker state confound timings",
                  "memory_scope": "Job-wide resource evidence across all four methods per input; no per-method peak comparison",
                  "comparison": "AHRL AUTO versus identical AHRL RAW framing separately from complete solid XZ and frozen AHPS",
                  "reproduction": ["python -B research/compare_relational_corpus.py " + command for command in ("prepare", "run", "summarize")]}
        write_json(directory / "freeze.json", freeze)
        print(json.dumps({"prepared": str(directory), "inputs": [{"id": i["id"], "bytes": i["input"]["bytes"]} for i in inputs]}))


def verify_freeze(freeze, selected_input=None):
    check_core()
    check_runtime(freeze["runtime"])
    if (tuple(freeze["input_ids"]) != FILENAMES
            or tuple(item["id"] for item in freeze["inputs"]) != FILENAMES
            or tuple(freeze["methods"]) != METHODS
            or freeze["settings"] != SETTINGS or freeze["limits"] != LIMITS
            or freeze["worker_priority_class"] != BELOW_NORMAL
            or freeze["corpus_directory"] != str(CORPUS.resolve())
            or freeze["report_path"] != str(REPORT) or freeze["owner_using_pc"] is not True):
        raise ValueError("Frozen corpus settings or schedule changed")
    for pin in freeze["pins"]:
        check_identity(pin)
    for pair in freeze["snapshots"]:
        check_identity(pair["snapshot"])
    inventory()
    for item in freeze["inputs"]:
        if selected_input is None or item["id"] == selected_input:
            check_identity(item["source"])
            check_identity(item["input"])


def marker(path, phase):
    Path(path).write_text(json.dumps({"phase": phase}), encoding="utf-8")


def model_kind(metadata):
    mode = metadata["storage_mode"]
    if mode == 0:
        return "raw"
    if mode == 1:
        return "shuffle"
    models = metadata["models"]
    if all(model["kind"] == 1 for model in models):
        return "previous_per_field"
    if any(model["kind"] == 2 and model["a"] != 0 for model in models):
        return "contains_cross_field_model"
    return "other_independent_models"


def worker(directory, input_id):
    # Called before validation, imports of codecs, corpus reads and timing.
    actual_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze, input_id)
    import ahps
    from research import relational_file
    if ahps.SIMD_BRIDGE.lib is None or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206:
        raise RuntimeError("Native ABI 206 required; fallback is forbidden")
    native = identity(ahps.SIMD_BRIDGE.lib._name)
    if native != identity(ROOT / "ahps_simd.dll"):
        raise RuntimeError("Loaded native DLL differs from the production pin")
    item = next(item for item in freeze["inputs"] if item["id"] == input_id)
    attempt = directory / "runs" / input_id
    data = Path(item["input"]["path"]).read_bytes()
    if len(data) != item["input"]["bytes"] or sha(data) != item["input"]["sha256"]:
        raise ValueError("Actual input bytes differ from frozen complete file")
    write_json(attempt / "worker-started.json", {"pid": os.getpid(), "started_utc": now(),
               "runtime": runtime(), "native": native, "abi": 206, "input": item["input"],
               "priority_class": actual_priority, "priority_set_before_heavy_work": True})
    failed = False
    raw_archive_bytes = None
    for method in METHODS:
        path = attempt / method
        path.mkdir()
        record = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                  "started_utc": now(), "source_bytes": len(data),
                  "source_sha256": item["input"]["sha256"], "complete_file": True}
        write_json(path / "started.json", record)
        try:
            record["priority_before_encode"] = priority_class()
            marker(attempt / "phase.json", method + ":encode")
            start = time.perf_counter()
            if method == "xz":
                archive = lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6)
            elif method == "ahps":
                archive, ahps_report = ahps.ahps_compress_bytes(data, block_size=4096,
                    force_python=False, lzma_preset=6, search=ahps.FAST_CHUNKS)
            else:
                archive = relational_file.encode(data, method="raw" if method == "ahrl_raw" else "auto",
                    width=4, fields=3, chunk_bytes=CHUNK_BYTES, preset=6)
            record["encode_api_seconds"] = time.perf_counter() - start
            write_bytes(path / "archive.bin", archive)
            record["archive"] = identity(path / "archive.bin")
            record["priority_before_decode"] = priority_class()
            marker(attempt / "phase.json", method + ":decode")
            start = time.perf_counter()
            if method == "xz":
                restored = lzma.decompress(archive, format=lzma.FORMAT_XZ, memlimit=128 * 1024**2)
            elif method == "ahps":
                restored = ahps.ahps_decompress_bytes(archive, force_python=False)
            else:
                restored = relational_file.decode(archive, max_output_bytes=FILE_CAP)
            record["decode_api_seconds"] = time.perf_counter() - start
            marker(attempt / "phase.json", method + ":verification")
            if len(restored) != len(data) or sha(restored) != item["input"]["sha256"]:
                raise ValueError("Complete restored length/SHA-256 mismatch")
            write_bytes(path / "restored.bin", restored)
            record["restored"] = identity(path / "restored.bin")
            del restored
            if method in ("ahrl_raw", "ahrl_auto"):
                record["metadata"] = relational_file.inspect_archive(archive)
                record["frame_representations"] = [model_kind(frame["metadata"])
                                                    for frame in record["metadata"]["frames"]]
                record["selection_note"] = "Exact serialized models are recorded; equivalent generating methods may produce the same archive"
                if method == "ahrl_raw":
                    raw_archive_bytes = len(archive)
                elif raw_archive_bytes is None or len(archive) > raw_archive_bytes:
                    raise ValueError("AUTO failed the complete matched-RAW size bound")
            elif method == "ahps":
                record["ahps_report"] = dataclasses.asdict(ahps_report)
            record["priority_after_method"] = priority_class()
            record.update(status="ok", finished_utc=now())
            del archive
        except Exception as exc:
            failed = True
            record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                          traceback=traceback.format_exc(), finished_utc=now())
        write_json(path / "result.json", record)
        print(json.dumps({"input": input_id, "method": method, "status": record["status"],
                          "archive_bytes": record.get("archive", {}).get("bytes")}), flush=True)
    verify_freeze(freeze, input_id)
    write_json(attempt / "worker-finished.json", {"status": "error" if failed else "ok", "finished_utc": now(),
               "pid": os.getpid(), "identities_after": "verified", "priority_class": priority_class()})
    return 1 if failed else 0


def run(directory):
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
                 "identities_before": "verified", "schedule": list(FILENAMES), "records": []}
        write_json(runs / "started.json", batch)
        try:
            for item in freeze["inputs"]:
                input_id = item["id"]
                attempt = runs / input_id
                attempt.mkdir()
                if time.monotonic() >= deadline:
                    result = {"input": input_id, "status": "unstarted_batch_deadline"}
                else:
                    child_deadline = min(deadline, time.monotonic() + LIMITS["input_worker_seconds"])
                    argv = [freeze["runtime"]["artifacts"]["python_exe"]["path"], "-I", "-B", str(Path(__file__).resolve()),
                            "worker", "--directory", str(directory), "--input", input_id]
                    result = run_job(argv, cwd=ROOT, deadline=child_deadline,
                        memory_limit_bytes=LIMITS["job_committed_bytes"], phase_file=attempt / "phase.json",
                        stdout_path=attempt / "stdout.log", stderr_path=attempt / "stderr.log")
                    result["input"] = input_id
                write_json(attempt / "job.json", result)
                batch["records"].append({"input": input_id, "status": result["status"], "job": identity(attempt / "job.json")})
                print(json.dumps({"input": input_id, "status": result["status"], "elapsed_seconds": result.get("elapsed_seconds")}), flush=True)
            verify_freeze(freeze)
            batch["identities_after"] = "verified"
            batch["status"] = "ok" if all(record["status"] == "ok" for record in batch["records"]) else "incomplete_or_failed"
        except BaseException as exc:
            batch.update(status="interrupted_or_error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            batch.update(finished_utc=now(), elapsed_seconds=time.monotonic() - start)
            write_json(runs / "finished.json", batch)
    return 0 if batch["status"] == "ok" else 1


def summarize(directory):
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze)
    batch = read_json(directory / "runs" / "finished.json")
    check_identity(batch["freeze"])
    for batch_record in batch["records"]:
        check_identity(batch_record["job"])
    records, jobs = [], []
    worker_integrity = True
    for item in freeze["inputs"]:
        attempt = directory / "runs" / item["id"]
        job = read_json(attempt / "job.json") if (attempt / "job.json").exists() else None
        if job is not None:
            jobs.append(job)
        started, finished = attempt / "worker-started.json", attempt / "worker-finished.json"
        if job is not None and job.get("status") == "ok" and started.exists() and finished.exists():
            start_record, end_record = read_json(started), read_json(finished)
            if (start_record["pid"] != job["pid"] or end_record["pid"] != job["pid"]
                    or end_record["status"] != "ok" or end_record.get("identities_after") != "verified"
                    or start_record["runtime"] != freeze["runtime"] or start_record["abi"] != 206
                    or start_record["priority_class"] != BELOW_NORMAL
                    or not start_record.get("priority_set_before_heavy_work")
                    or end_record["priority_class"] != BELOW_NORMAL
                    or not job.get("tree_cleaned") or not job.get("assignment_before_resume")
                    or job.get("breakaway_allowed") or job.get("returncode") != 0
                    or job.get("memory_limit_bytes") != LIMITS["job_committed_bytes"]):
                raise ValueError("Worker/Job completion, priority or resource mismatch")
        else:
            worker_integrity = False
        for method in METHODS:
            path = attempt / method / "result.json"
            if not path.exists():
                records.append({"input": item["id"], "method": method, "status": "no_result",
                                "job_status": job.get("status") if job else "unstarted"})
                continue
            row = read_json(path)
            if row["input"] != item["id"] or row["method"] != method or job is None or row["pid"] != job.get("pid"):
                raise ValueError("Method result identity does not match the scheduled worker")
            if row["status"] == "ok":
                check_identity(row["archive"])
                check_identity(row["restored"])
                if (row["source_bytes"] != item["input"]["bytes"] or row["source_sha256"] != item["input"]["sha256"]
                        or row["restored"]["sha256"] != item["input"]["sha256"]
                        or row["restored"]["bytes"] != item["input"]["bytes"] or not row.get("complete_file")
                        or any(row[key] != BELOW_NORMAL for key in ("priority_before_encode", "priority_before_decode", "priority_after_method"))):
                    raise ValueError("Full-file restoration or priority differs from the frozen requirement")
            records.append(row)
    complete = (len(records) == 20 and all(row["status"] == "ok" for row in records)
                and worker_integrity and batch["status"] == "ok" and batch.get("identities_after") == "verified")
    per_input = []
    for item in freeze["inputs"]:
        rows = {row["method"]: row for row in records if row["input"] == item["id"] and row["status"] == "ok"}
        summary = {"input": item["id"], "source_bytes": item["input"]["bytes"],
                   "status": "ok" if all(method in rows for method in METHODS) else "incomplete",
                   "archive_bytes": {method: row["archive"]["bytes"] for method, row in rows.items()}}
        if all(method in rows for method in METHODS):
            sizes = summary["archive_bytes"]
            if sizes["ahrl_auto"] > sizes["ahrl_raw"]:
                raise ValueError("AUTO complete archive exceeds matched RAW")
            summary.update(auto_saving_vs_ahps_percent=100 * (1 - sizes["ahrl_auto"] / sizes["ahps"]),
                           auto_saving_vs_matched_raw_percent=100 * (1 - sizes["ahrl_auto"] / sizes["ahrl_raw"]),
                           auto_saving_vs_solid_xz_percent=100 * (1 - sizes["ahrl_auto"] / sizes["xz"]),
                           matched_raw_bound_pass=True,
                           auto_frame_representations=rows["ahrl_auto"]["frame_representations"])
        per_input.append(summary)
    totals = None
    if complete:
        sizes = {method: sum(row["archive"]["bytes"] for row in records if row["method"] == method) for method in METHODS}
        totals = {"source_bytes": sum(item["input"]["bytes"] for item in freeze["inputs"]), "archive_bytes": sizes,
                  "auto_saving_vs_ahps_percent": 100 * (1 - sizes["ahrl_auto"] / sizes["ahps"]),
                  "auto_saving_vs_matched_raw_percent": 100 * (1 - sizes["ahrl_auto"] / sizes["ahrl_raw"]),
                  "auto_saving_vs_solid_xz_percent": 100 * (1 - sizes["ahrl_auto"] / sizes["xz"]),
                  "scope": "Ratio of summed bytes over these five complete development files only"}
    report = {"schema": 1, "created_utc": now(), "freeze": freeze, "batch": batch,
              "jobs": jobs, "records": records, "per_input": per_input, "totals": totals,
              "completed_roundtrips": sum(row["status"] == "ok" for row in records),
              "planned_roundtrips": 20, "all_roundtrips_pass": complete,
              "verdict": "complete_development_screen" if complete else "incomplete_or_failed",
              "limitations": ["Blind fixed record layout, not discovered executable structure or a typed-table result.",
                              "The owner was using the PC; single-pass timings are diagnostic only.",
                              "Methods share input workers; job memory metrics are not per-codec peaks.",
                              "Chunk-reset/framing effects are included; matched RAW and whole-file baseline comparisons are distinct.",
                              "No sealed files, reserves, production codec changes or core-model retuning."]}
    write_json(REPORT, report)
    print(json.dumps({"report": str(REPORT), "verdict": report["verdict"],
                      "roundtrips": report["completed_roundtrips"], "per_input": per_input, "totals": totals}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize", "worker"))
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--input", choices=FILENAMES)
    args = parser.parse_args()
    directory = args.directory.resolve()
    # The controller also stays below-normal while hashing/copying the corpus.
    set_below_normal_priority()
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
