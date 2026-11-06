#!/usr/bin/env python3
"""Frozen, bounded first comparison for the isolated relational codec.

Run prepare, then run, then summarize. No command resumes or overwrites a run.
Only the pinned Blender development parent and constructed controls are read.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import platform
import random
import struct
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

METHODS = ("raw", "shuffle", "delta", "independent", "cross", "adaptive", "auto", "xz", "ahps")
INPUTS = ("blender-exception-table", "planted-exact", "planted-noisy", "independent-fields")
CONTROLS = ("raw", "shuffle", "delta", "independent", "xz", "ahps")
CROSS = ("cross", "adaptive")
PROFILES = {
    "first": {"input_ids": INPUTS, "table_max_bytes": 2 * 1024**2,
              "directory": ROOT / "experiments" / "relational-20261103",
              "report": ROOT / "reports" / "relational-first-20261103.json"},
    "table-only": {"input_ids": (INPUTS[0],), "table_max_bytes": 3 * 1024**2,
                   "directory": ROOT / "experiments" / "relational-table-20261103",
                   "report": ROOT / "reports" / "relational-table-20261103.json"},
}
EXPECTED_MAIN = "0dac8d2"
EXPECTED_PRODUCTION = {
    "ahps.py": "c7dcc554c64f4abf78fef6852603e91de9db4048ae5ffe81c4ff007b61eead2d",
    "ahps_simd.c": "4e340be401057d53aa897cb3af67b557f233031c2339542deea971c841fbfb16",
    "ahps_simd.dll": "71df88e8d41c9f233838c6563de714427ccc46782407d20836c0bc1e937c6022",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_bytes(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def check_identity(expected):
    actual = identity(expected["path"])
    if actual != expected:
        raise ValueError("Frozen identity changed: " + expected["path"])


def check_production():
    for name, expected in EXPECTED_PRODUCTION.items():
        if identity(ROOT / name)["sha256"] != expected:
            raise ValueError("Production baseline changed: " + name)


def runtime():
    import _lzma
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    artifacts = {"python_exe": identity(base), "lzma_extension": identity(_lzma.__file__)}
    dll = base.parent / ("python%d%d.dll" % sys.version_info[:2])
    if dll.exists():
        artifacts["python_dll"] = identity(dll)
    return {"version": sys.version, "implementation": platform.python_implementation(),
            "platform": platform.platform(), "artifacts": artifacts}


def check_runtime(expected):
    if runtime() != expected:
        raise ValueError("Python/LZMA runtime differs from frozen preparation")


def shared_lock():
    from benchmark_ahps import RunLock
    # Git's common directory is shared by all worktrees; the project's normal
    # benchmark lock uses its parent as the lock root.
    git_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip()
    common = (ROOT / git_dir).resolve()
    return RunLock(common.parent / ".ahps-benchmark.lock")


def extract_exception_table(path, max_bytes=2 * 1024**2):
    """Read the complete file-backed PE32+ exception directory under the profile cap.

    The caller must verify the complete development-parent identity first.
    This parser neither executes code nor scans other regions for alternatives.
    """
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as stream:
        def at(offset, length):
            if offset < 0 or length < 0 or offset + length > size:
                raise ValueError("PE range leaves the source file")
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("Truncated PE source")
            return data

        dos = at(0, 64)
        if dos[:2] != b"MZ":
            raise ValueError("Missing DOS signature")
        pe_offset = struct.unpack_from("<I", dos, 60)[0]
        coff = at(pe_offset, 24)
        if coff[:4] != b"PE\0\0":
            raise ValueError("Missing PE signature")
        machine, sections = struct.unpack_from("<HH", coff, 4)
        optional_size = struct.unpack_from("<H", coff, 20)[0]
        if machine != 0x8664 or not 1 <= sections <= 96 or optional_size < 144:
            raise ValueError("Not a bounded x64 PE32+ section layout")
        optional = at(pe_offset + 24, optional_size)
        if struct.unpack_from("<H", optional)[0] != 0x20B:
            raise ValueError("Not PE32+")
        directories = struct.unpack_from("<I", optional, 108)[0]
        if directories < 4 or 112 + min(directories, 16) * 8 > optional_size:
            raise ValueError("Incomplete PE data-directory table")
        rva, length = struct.unpack_from("<II", optional, 112 + 3 * 8)
        if not rva or not length or length > max_bytes or length % 12:
            raise ValueError("Exception directory absent, too large or not whole records")
        if rva + length > 2**32:
            raise ValueError("Exception RVA range wraps")
        headers = at(pe_offset + 24 + optional_size, sections * 40)
        mappings = []
        ranges = []
        for i in range(sections):
            section = headers[40*i:40*(i+1)]
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", section, 8)
            if raw_size:
                if raw_offset + raw_size > size:
                    raise ValueError("PE section raw range leaves the source")
                if any(raw_offset < end and start < raw_offset + raw_size for start, end in ranges):
                    raise ValueError("PE raw sections overlap")
                ranges.append((raw_offset, raw_offset + raw_size))
            if virtual_address <= rva and rva + length <= virtual_address + raw_size:
                mappings.append((raw_offset + rva - virtual_address,
                                 section[:8].rstrip(b"\0").decode("ascii", errors="replace"),
                                 virtual_address, virtual_size, raw_size))
        if len(mappings) != 1:
            raise ValueError("Exception directory is not uniquely file-backed")
        offset, section_name, virtual_address, virtual_size, raw_size = mappings[0]
        data = at(offset, length)
        if any(begin >= end for begin, end, unwind in struct.iter_unpack("<III", data)):
            raise ValueError("Exception table includes an invalid begin/end interval")
        return data, {"file_offset": offset, "rva": rva, "bytes": length,
                      "records": length // 12, "section": section_name,
                      "section_rva": virtual_address, "section_virtual_bytes": virtual_size,
                      "section_raw_bytes": raw_size, "machine": "0x8664", "optional_magic": "0x20b",
                      "layout": ["begin_rva:u32le", "end_rva:u32le", "unwind_rva:u32le"]}


def synthetic(which):
    rng = random.Random(20261104 if which == "independent-fields" else 20261103)
    data = bytearray()
    for i in range(16384):
        if which == "independent-fields":
            values = [rng.getrandbits(32) for _ in range(3)]
        else:
            x = rng.getrandbits(32)
            y = (3 * x + 7) & 0xFFFFFFFF
            noisy = which == "planted-noisy" and (i + 1) % 32 == 0
            if noisy:
                y = (y + rng.getrandbits(8)) & 0xFFFFFFFF
            z = (-5 * y + 11) & 0xFFFFFFFF
            if noisy:
                z = (z + rng.getrandbits(8)) & 0xFFFFFFFF
            values = (x, y, z)
        data.extend(struct.pack("<III", *values))
    return bytes(data)


def prepare(directory, profile_name="first"):
    config = PROFILES[profile_name]
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "experiments"):
        raise ValueError("Experiment artifacts must stay under this workspace's experiments directory")
    if directory.exists():
        raise FileExistsError("Preparation directory already exists; no overwrite or resume")
    with shared_lock():
        check_production()
        sources = ["ahps.py", "ahps_simd.c", "ahps_simd.dll", "benchmark_ahps.py",
                   "research/__init__.py", "research/relational.py", "research/compare_relational.py",
                   "research/job_runner.py", "docs/relational-compression.md", "docs/format.md",
                   "reports/evaluation-protocol.md", "corpus_manifest.json"]
        sources += sorted(p.name for p in ROOT.glob("test*relational*.py"))
        prior_result = None
        if profile_name == "table-only":
            previous_report = PROFILES["first"]["report"]
            previous = read_json(previous_report)
            if (previous["freeze"]["inputs"][0]["status"] != "unavailable"
                    or previous["completed_roundtrips"] != 27
                    or previous["continuation_criterion_pass"]):
                raise ValueError("Table follow-up requires the preserved first-cohort unavailable outcome")
            # This follow-up changes only input eligibility. Preserve the exact
            # tested codec, tests and runner before any real-table measurement.
            for pin in previous["freeze"]["pins"]:
                name = Path(pin["path"]).relative_to(ROOT).as_posix()
                if (name in ("research/relational.py", "research/__init__.py", "research/job_runner.py")
                        or (name.startswith("test") and "relational" in name and name.endswith(".py"))):
                    check_identity(pin)
            sources += ["docs/relational-table-followup.md", "reports/relational-first-20261103.json"]
            prior_result = {"identity": identity(previous_report), "verdict": previous["verdict"],
                            "reason": "The original 2 MiB cap excluded the complete 2,484,204-byte table before any real-table compression; this separate profile raises only that cap to 3 MiB."}
        pins = [identity(ROOT / name) for name in sources]
        run_runtime = runtime()
        manifest = read_json(ROOT / "corpus_manifest.json")
        blender = next(entry for entry in manifest["development"] if entry["id"] == "blender")
        if blender["path"] != "blender.exe" or blender.get("split") != "development":
            raise ValueError("Unexpected Blender development manifest entry")
        parent = identity(ROOT / "blender.exe")
        if parent["sha256"] != blender["sha256"] or parent["bytes"] != blender["bytes"]:
            raise ValueError("Blender parent differs from frozen development manifest")
        directory.mkdir(parents=True)
        snapshots = []
        for pin in pins:
            relative = Path(pin["path"]).relative_to(ROOT)
            snapshot = directory / "frozen" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            write_bytes(snapshot, Path(pin["path"]).read_bytes())
            saved = identity(snapshot)
            if (saved["bytes"], saved["sha256"]) != (pin["bytes"], pin["sha256"]):
                raise ValueError("Source changed while creating frozen snapshot: " + str(relative))
            snapshots.append({"live": pin, "snapshot": saved})
        inputs_dir = directory / "inputs"
        inputs_dir.mkdir()
        inputs = []
        for name in config["input_ids"]:
            record = {"id": name, "width": 4, "fields": 3, "status": "available"}
            try:
                if name == INPUTS[0]:
                    data, source = extract_exception_table(parent["path"], max_bytes=config["table_max_bytes"])
                    record["source"] = {"parent": parent, "region": source}
                else:
                    data = synthetic(name)
                    record["source"] = {"constructed": True, "records": 16384,
                        "seed": 20261104 if name == "independent-fields" else 20261103,
                        "noise": "Every 32nd one-based record: y+=getrandbits(8), then z=-5*y+11, then z+=getrandbits(8); all modulo 2^32" if name == "planted-noisy" else None}
                path = inputs_dir / (name + ".bin")
                write_bytes(path, data)
                record["input"] = identity(path)
            except ValueError as exc:
                if name != INPUTS[0]:
                    raise
                record.update(status="unavailable", reason=str(exc), parent=parent)
            inputs.append(record)
        check_identity(parent)  # Bracket extraction with the same complete parent hash.
        freeze = {"schema": 1, "prepared_utc": now(), "base_commit": EXPECTED_MAIN,
                  "profile": profile_name, "input_ids": list(config["input_ids"]),
                  "table_max_bytes": config["table_max_bytes"], "report_path": str(config["report"]),
                  "prior_result": prior_result,
                  "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                  "pins": pins, "snapshots": snapshots, "runtime": run_runtime, "inputs": inputs, "methods": list(METHODS),
                  "preset": 6, "xz_check": "CRC64", "ahps_block_bytes": 4096,
                  "ahps_search": {"chunk_trial_preset": 1, "exe32_chunk_rival": False},
                  "limits": {"batch_seconds": 600, "input_worker_seconds": 120,
                             "job_committed_bytes": 2 * 1024**3},
                  "criterion": "All %d planned round trips pass; real best cross/adaptive <=99%% of best raw/shuffle/delta/independent/xz/ahps, with at least one nonzero-affine coefficient in winning archive" % (len(config["input_ids"]) * len(METHODS)),
                  "reproduction": ["python -B research/compare_relational.py %s --profile %s" % (command, profile_name) for command in ("prepare", "run", "summarize")],
                  "timing_scope": "Single-pass encode/decode API perf_counter durations, allocation included, file I/O and verification excluded; diagnostic only",
                  "memory_scope": "Job-level process/resource evidence across all methods in each input worker; no per-method memory comparison",
                  "input_scope": "Complete selected development table plus planted controls; no whole-executable, broader-corpus or sealed-evaluation claim"}
        write_json(directory / "freeze.json", freeze)
        print(json.dumps({"prepared": str(directory), "inputs": [{"id": r["id"], "status": r["status"], "bytes": r.get("input", {}).get("bytes")} for r in inputs]}))


def verify_freeze(freeze):
    config = PROFILES[freeze["profile"]]
    check_production()
    check_runtime(freeze["runtime"])
    if (tuple(freeze["methods"]) != METHODS or tuple(i["id"] for i in freeze["inputs"]) != config["input_ids"]
            or tuple(freeze["input_ids"]) != config["input_ids"]
            or freeze["table_max_bytes"] != config["table_max_bytes"]
            or freeze["report_path"] != str(config["report"])):
        raise ValueError("Frozen experiment schedule changed")
    if freeze["limits"] != {"batch_seconds": 600, "input_worker_seconds": 120, "job_committed_bytes": 2 * 1024**3}:
        raise ValueError("Frozen resource limits differ from the contract")
    if freeze["preset"] != 6 or freeze["xz_check"] != "CRC64" or freeze["ahps_block_bytes"] != 4096:
        raise ValueError("Frozen codec settings differ from the contract")
    for pin in freeze["pins"]:
        check_identity(pin)
    for snapshot in freeze["snapshots"]:
        check_identity(snapshot["snapshot"])
    for item in freeze["inputs"]:
        if item["status"] == "available":
            check_identity(item["input"])


def marker(path, phase):
    Path(path).write_text(json.dumps({"phase": phase}), encoding="utf-8")


def worker(directory, input_id):
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze)
    import ahps
    from research import relational
    if ahps.SIMD_BRIDGE.lib is None or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206:
        raise RuntimeError("Native ABI 206 required; fallback is forbidden")
    native = identity(ahps.SIMD_BRIDGE.lib._name)
    if native != identity(ROOT / "ahps_simd.dll"):
        raise RuntimeError("Loaded native DLL is not the frozen production library")
    item = next(i for i in freeze["inputs"] if i["id"] == input_id)
    if item["status"] != "available":
        raise ValueError("Unavailable input cannot be measured")
    attempt = directory / "runs" / input_id
    data = Path(item["input"]["path"]).read_bytes()
    write_json(attempt / "worker-started.json", {"pid": os.getpid(), "started_utc": now(),
               "runtime": runtime(), "native": native, "abi": 206, "input": item["input"]})
    failed = False
    fixed_archives = {}
    for method in METHODS:
        path = attempt / method
        path.mkdir()
        record = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                  "started_utc": now(), "source_bytes": len(data), "source_sha256": sha(data)}
        write_json(path / "started.json", record)
        try:
            marker(attempt / "phase.json", method + ":encode")
            start = time.perf_counter()
            if method == "xz":
                archive = lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6)
            elif method == "ahps":
                archive, ahps_report = ahps.ahps_compress_bytes(data, block_size=4096,
                    force_python=False, lzma_preset=6, search=ahps.FAST_CHUNKS)
            else:
                archive = relational.encode(data, width=4, fields=3, method=method, preset=6)
            record["encode_api_seconds"] = time.perf_counter() - start
            write_bytes(path / "archive.bin", archive)
            record["archive"] = identity(path / "archive.bin")
            marker(attempt / "phase.json", method + ":decode")
            start = time.perf_counter()
            if method == "xz":
                restored = lzma.decompress(archive, format=lzma.FORMAT_XZ, memlimit=128 * 1024**2)
            elif method == "ahps":
                restored = ahps.ahps_decompress_bytes(archive, force_python=False)
            else:
                restored = relational.decode(archive, max_output_bytes=8 * 1024**2)
            record["decode_api_seconds"] = time.perf_counter() - start
            marker(attempt / "phase.json", method + ":verification")
            if len(restored) != len(data) or sha(restored) != item["input"]["sha256"]:
                raise ValueError("Restored length/SHA-256 mismatch")
            write_bytes(path / "restored.bin", restored)
            record["restored"] = identity(path / "restored.bin")
            if method not in ("xz", "ahps"):
                record["metadata"] = relational.inspect_archive(archive)
                if method != "auto":
                    fixed_archives[method] = archive
                else:
                    best_method = min(fixed_archives, key=lambda key: len(fixed_archives[key]))
                    if archive != fixed_archives[best_method]:
                        raise ValueError("Auto did not select exact shortest fixed-method archive")
                    record["selected_method"] = best_method
            elif method == "ahps":
                record["ahps_report"] = dataclasses.asdict(ahps_report)
            record.update(status="ok", finished_utc=now())
        except Exception as exc:
            failed = True
            record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                          traceback=traceback.format_exc(), finished_utc=now())
        write_json(path / "result.json", record)
        print(json.dumps({"input": input_id, "method": method, "status": record["status"],
                          "archive_bytes": record.get("archive", {}).get("bytes")}), flush=True)
    verify_freeze(freeze)
    write_json(attempt / "worker-finished.json", {"status": "error" if failed else "ok", "finished_utc": now(),
               "pid": os.getpid(), "identities_after": "verified"})
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
        deadline = start + freeze["limits"]["batch_seconds"]
        batch = {"started_utc": now(), "freeze": identity(freeze_path), "status": "running",
                 "started_monotonic": start, "deadline_monotonic": deadline,
                 "identities_before": "verified", "schedule": freeze["input_ids"], "records": []}
        write_json(runs / "started.json", batch)
        try:
            for item in freeze["inputs"]:
                input_id = item["id"]
                attempt = runs / input_id
                attempt.mkdir()
                if item["status"] != "available":
                    result = {"input": input_id, "status": "unavailable", "reason": item["reason"]}
                elif time.monotonic() >= deadline:
                    result = {"input": input_id, "status": "unstarted_batch_deadline"}
                else:
                    child_deadline = min(deadline, time.monotonic() + freeze["limits"]["input_worker_seconds"])
                    argv = [freeze["runtime"]["artifacts"]["python_exe"]["path"], "-I", "-B", str(Path(__file__).resolve()),
                            "worker", "--directory", str(directory), "--input", input_id]
                    result = run_job(argv, cwd=ROOT, deadline=child_deadline,
                        memory_limit_bytes=freeze["limits"]["job_committed_bytes"],
                        phase_file=attempt / "phase.json", stdout_path=attempt / "stdout.log", stderr_path=attempt / "stderr.log")
                    result["input"] = input_id
                write_json(attempt / "job.json", result)
                batch["records"].append({"input": input_id, "status": result["status"], "job": identity(attempt / "job.json")})
                print(json.dumps({"input": input_id, "status": result["status"], "elapsed_seconds": result.get("elapsed_seconds")}), flush=True)
            verify_freeze(freeze)
            batch["identities_after"] = "verified"
            batch["status"] = "ok" if all(r["status"] == "ok" for r in batch["records"]) else "incomplete_or_failed"
        except BaseException as exc:
            batch.update(status="interrupted_or_error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            batch.update(finished_utc=now(), elapsed_seconds=time.monotonic() - start)
            write_json(runs / "finished.json", batch)
    return 0 if batch["status"] == "ok" else 1


def has_nonzero_affine(metadata):
    # Codec metadata contains descriptor dictionaries with numeric kind/a.
    return any(model.get("kind") == 2 and model.get("a", 0) != 0 for model in metadata.get("models", []))


def summarize(directory):
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze)
    batch = read_json(directory / "runs" / "finished.json")
    check_identity(batch["freeze"])
    for batch_record in batch["records"]:
        check_identity(batch_record["job"])
    records = []
    jobs = []
    worker_integrity = True
    for item in freeze["inputs"]:
        attempt = directory / "runs" / item["id"]
        job = None
        if (attempt / "job.json").exists():
            job = read_json(attempt / "job.json")
            jobs.append(job)
        started, finished = attempt / "worker-started.json", attempt / "worker-finished.json"
        if job is not None and job.get("status") == "ok" and started.exists() and finished.exists():
            start_record, end_record = read_json(started), read_json(finished)
            if (start_record["pid"] != job["pid"] or end_record["pid"] != job["pid"]
                    or end_record["status"] != "ok" or end_record.get("identities_after") != "verified"
                    or start_record["runtime"] != freeze["runtime"] or start_record["abi"] != 206
                    or not job.get("tree_cleaned") or not job.get("assignment_before_resume")
                    or job.get("breakaway_allowed") or job.get("returncode") != 0):
                raise ValueError("Worker/Job identity, completion or cleanup mismatch")
        else:
            worker_integrity = False
        for method in METHODS:
            path = attempt / method / "result.json"
            if not path.exists():
                records.append({"input": item["id"], "method": method, "status": "no_result"})
                continue
            row = read_json(path)
            if row["input"] != item["id"] or row["method"] != method:
                raise ValueError("Record schedule identity mismatch")
            if job is None or row["pid"] != job.get("pid"):
                raise ValueError("Method result PID differs from the actual worker")
            if row["status"] == "ok":
                check_identity(row["archive"])
                check_identity(row["restored"])
                if row["restored"]["sha256"] != item["input"]["sha256"] or row["restored"]["bytes"] != item["input"]["bytes"]:
                    raise ValueError("Result restoration differs from frozen input")
            records.append(row)
    complete = len(records) == len(freeze["input_ids"]) * len(METHODS) and all(r["status"] == "ok" for r in records)
    complete = complete and worker_integrity and batch["status"] == "ok" and batch.get("identities_after") == "verified"
    per_input = []
    for item in freeze["inputs"]:
        rows = {r["method"]: r for r in records if r["input"] == item["id"] and r["status"] == "ok"}
        if not all(m in rows for m in METHODS):
            per_input.append({"input": item["id"], "status": "incomplete"})
            continue
        best_control = min(CONTROLS, key=lambda m: rows[m]["archive"]["bytes"])
        best_cross = min(CROSS, key=lambda m: rows[m]["archive"]["bytes"])
        control_bytes, cross_bytes = (rows[m]["archive"]["bytes"] for m in (best_control, best_cross))
        affine_used = has_nonzero_affine(rows[best_cross].get("metadata", {}))
        per_input.append({"input": item["id"], "status": "ok", "best_control": best_control,
                          "control_archive_bytes": control_bytes, "best_cross": best_cross,
                          "cross_archive_bytes": cross_bytes,
                          "cross_saving_percent": 100 * (1 - cross_bytes / control_bytes),
                          "nonzero_affine_in_winner": affine_used,
                          "size_gate": 100 * cross_bytes <= 99 * control_bytes,
                          "individual_gate": 100 * cross_bytes <= 99 * control_bytes and affine_used})
    real = per_input[0]
    accepted = complete and real.get("individual_gate", False)
    report = {"schema": 1, "created_utc": now(), "freeze": freeze, "batch": batch,
              "jobs": jobs, "records": records, "per_input": per_input,
              "completed_roundtrips": sum(r["status"] == "ok" for r in records),
              "planned_roundtrips": len(freeze["input_ids"]) * len(METHODS), "all_roundtrips_pass": complete,
              "continuation_criterion_pass": accepted,
              "verdict": "continue_on_another_development_input" if accepted else "criterion_not_met",
              "limitations": ["One known-layout development region; synthetic controls are not evidence of general compression gains.",
                              "Single-pass API times are diagnostic, not stable performance comparisons.",
                              "Per-input workers retain state across methods; job resource metrics are not per-codec memory measurements.",
                              "No production codec, format, defaults or native kernel was changed."]}
    report_path = Path(freeze["report_path"])
    write_json(report_path, report)
    print(json.dumps({"report": str(report_path), "verdict": report["verdict"], "roundtrips": report["completed_roundtrips"], "per_input": per_input}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize", "worker"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="first")
    parser.add_argument("--input", choices=INPUTS)
    args = parser.parse_args()
    directory = (args.directory or PROFILES[args.profile]["directory"]).resolve()
    if args.command == "prepare":
        prepare(directory, args.profile)
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
