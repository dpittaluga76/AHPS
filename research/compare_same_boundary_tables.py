#!/usr/bin/env python3
"""Frozen same-boundary AHPS byte-delta versus AHRL word-delta experiment.

Archive sizes and residual-only diagnostics are separate measurements. No
codec/planner changes or whole-executable compression are performed here.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import traceback
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.compare_relational import (
    check_identity, check_runtime, extract_exception_table, identity, now,
    read_json, runtime, sha, shared_lock, write_bytes, write_json,
)
from research.compare_relational_corpus import (
    BELOW_NORMAL, check_core, inventory, priority_class, set_below_normal_priority,
)

TABLE_LIMIT = 8 * 1024**2
FIXED_METHODS = ("ahps_raw", "ahps_split12", "ahps_delta1_split12",
                 "ahps_delta2_split12", "ahrl_word_delta")
METHODS = ("xz",) + FIXED_METHODS + ("ahps_auto",)
FIXED_AHPS = FIXED_METHODS[:-1]
EXISTING_METHODS = ("xz",) + FIXED_AHPS + ("ahps_auto",)
INPUTS = ("blender", "7zip-cli.exe", "chrome.exe", "git-cli.exe", "node.exe", "vlc.exe")
PARENTS = {name: ("blender.exe" if name == "blender" else "corpus_development/" + name)
           for name in INPUTS}
DIRECTORY = ROOT / "experiments" / "same-boundary-tables-20261103"
REPORT = ROOT / "reports" / "same-boundary-tables-20261103.json"
LIMITS = {"table_worker_seconds": 120, "batch_seconds": 600,
          "job_committed_bytes": 2 * 1024**3}
SETTINGS = {"preset": 6, "xz_check": "CRC64", "width": 4, "fields": 3,
            "stride": 12, "table_cap_bytes": TABLE_LIMIT, "ahps_block_bytes": 4096,
            "ahps_search": {"chunk_trial_preset": 1, "exe32_chunk_rival": False}}


def table_bytes(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("table/residual must be bytes-like")
    size = data.nbytes if isinstance(data, memoryview) else len(data)
    if not 0 < size <= TABLE_LIMIT or size % 12:
        raise ValueError("table/residual must be nonempty complete 12-byte records within cap")
    return bytes(data)


def bounded_xz(archive, max_bytes):
    """Read exactly one CRC64 XZ stream with an actual expansion bound."""
    if not isinstance(archive, (bytes, bytearray, memoryview)):
        raise TypeError("archive must be bytes-like")
    size = archive.nbytes if isinstance(archive, memoryview) else len(archive)
    if size > TABLE_LIMIT + 1024**2:
        raise ValueError("compressed research input exceeds cap")
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 * 1024**2)
        result = decoder.decompress(bytes(archive), max_length=max_bytes + 1)
    except lzma.LZMAError as exc:
        raise ValueError("invalid XZ archive") from exc
    if len(result) > max_bytes or not decoder.eof:
        raise ValueError("truncated or excessive XZ expansion")
    if decoder.unused_data or decoder.check != lzma.CHECK_CRC64:
        raise ValueError("trailing/concatenated or non-CRC64 XZ stream")
    return result


def fixed_program(method):
    import ahps
    stride = bytes((12, 0, 0, 0))
    if method == "ahps_raw":
        return []
    if method == "ahps_split12":
        return [(ahps.OP_SPLIT, stride)]
    if method == "ahps_delta1_split12":
        return [(ahps.OP_DELTA1_LANE, stride), (ahps.OP_SPLIT, stride)]
    if method == "ahps_delta2_split12":
        return [(ahps.OP_DELTA2_LANE, stride), (ahps.OP_SPLIT, stride)]
    raise ValueError("method has no fixed AHPS program")


def require_engine(use_c):
    import ahps
    if not isinstance(use_c, bool):
        raise TypeError("use_c must be boolean")
    if use_c and (ahps.SIMD_BRIDGE.lib is None or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206):
        raise RuntimeError("Native ABI 206 required; no silent fallback")


def encode_fixed(data, method, preset=6, use_c=True):
    data = table_bytes(data)
    require_engine(use_c)
    if method == "ahrl_word_delta":
        from research import relational
        return relational.encode(data, 4, 3, "delta", preset=preset)
    import ahps
    program = fixed_program(method)
    container = ahps.pack_container(data, [ahps.Segment(0, len(data), program)], use_c)
    return lzma.compress(container, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=preset)


def restore_fixed_residual(residual, method, use_c=True):
    residual = table_bytes(residual)
    require_engine(use_c)
    import ahps
    if method == "ahrl_word_delta":
        # Independent inverse: undo the same 12-lane permutation, then carry
        # uint32 history per field. No AHRL predictor-search or decoder call.
        records = len(residual) // 12
        interleaved = bytearray(len(residual))
        for lane in range(12):
            interleaved[lane::12] = residual[lane * records:(lane + 1) * records]
        output = bytearray(len(residual))
        previous = (0, 0, 0)
        for index, row in enumerate(struct.iter_unpack("<III", interleaved)):
            current = tuple((row[j] + previous[j]) & 0xFFFFFFFF for j in range(3))
            struct.pack_into("<III", output, index * 12, *current)
            previous = current
        return bytes(output)
    for opcode, params in reversed(fixed_program(method)):
        residual = ahps.lift_step(opcode, params, residual, use_c, inverse=True)
    return residual


def serialized_segments(segments):
    result, offset = [], 0
    for length, program in segments:
        result.append({"start": offset, "length": length,
                       "program": [{"opcode": op, "params_hex": params.hex()} for op, params in program]})
        offset += length
    return result


def extract_fixed_residual(archive, method, use_c=True):
    require_engine(use_c)
    if method == "ahrl_word_delta":
        from research import relational
        payload, row, models, metadata = relational._read_container(archive, TABLE_LIMIT)
        table_bytes(payload)
        if (metadata["width"] != 4 or metadata["fields"] != 3 or metadata["storage_mode"] != 2
                or models != [(1, 0, 0, 0)] * 3):
            raise ValueError("archive is not the declared AHRL word-delta representation")
        relational._restore(payload, row, models, metadata)  # Includes restored CRC.
        return payload, {"format": "AHRL1", "source_bytes": len(payload), "region_count": 1,
                         "model_metadata": metadata, "container_prefix_bytes": 78}
    import ahps
    expected = fixed_program(method)
    container = bounded_xz(archive, TABLE_LIMIT + 40)
    if container[:5] != ahps.MAGIC_HEADER:
        raise ValueError("fixed table archive must be AHPS v5")
    segments, start, size, crc = ahps.parse_container(container)
    residual = table_bytes(container[start:])
    if segments != [(len(residual), expected)] or size != len(residual):
        raise ValueError("archive does not contain one canonical fixed table program")
    if zlib.crc32(restore_fixed_residual(residual, method, use_c)) != crc:
        raise ValueError("fixed archive restored checksum mismatch")
    return residual, {"format": "AHPS5", "source_bytes": size, "segment_count": 1,
                      "segments": serialized_segments(segments), "container_prefix_bytes": start,
                      "crc32": crc}


def automatic_metadata(archive):
    import ahps
    container = bounded_xz(archive, TABLE_LIMIT + 1024**2)
    if container[:5] != ahps.MAGIC_HEADER:
        raise ValueError("automatic table archive must be AHPS v5")
    segments, start, size, crc = ahps.parse_container(container)
    table_bytes(container[start:])
    return {"format": "AHPS5", "source_bytes": size, "segment_count": len(segments),
            "segments": serialized_segments(segments), "container_prefix_bytes": start,
            "crc32": crc, "internal_boundaries_selected_by_ahps": True}


def prepare(directory):
    controller_priority = set_below_normal_priority()
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "experiments") or directory.exists() or REPORT.exists():
        raise ValueError("Use a new artifact directory under experiments; no overwrite/resume")
    with shared_lock():
        check_core()
        inventory()
        sources = ["ahps.py", "ahps_simd.c", "ahps_simd.dll", "benchmark_ahps.py",
                   "research/__init__.py", "research/relational.py", "research/compare_relational.py",
                   "research/compare_relational_corpus.py", "research/compare_same_boundary_tables.py",
                   "research/job_runner.py", "docs/same-boundary-table-experiment.md", "docs/format.md",
                   "reports/evaluation-protocol.md", "corpus_manifest.json"]
        tests = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))
        if "tests/test_same_boundary_tables.py" not in tests:
            raise ValueError("Same-boundary helper regression tests are required")
        sources += ["tests/__init__.py"] + tests
        pins = [identity(ROOT / name) for name in sources]
        run_runtime = runtime()
        entries = {entry["path"]: entry for entry in read_json(ROOT / "corpus_manifest.json")["development"]}
        parents = []
        for name in INPUTS:
            path = PARENTS[name]
            entry = entries[path]
            if entry.get("split") != "development":
                raise ValueError("Not a development input")
            actual = identity(ROOT / path)
            if actual["bytes"] != entry["bytes"] or actual["sha256"] != entry["sha256"]:
                raise ValueError("Development parent differs from manifest: " + name)
            parents.append(actual)
        directory.mkdir(parents=True)
        snapshots = []
        for pin in pins:
            relative = Path(pin["path"]).relative_to(ROOT)
            target = directory / "frozen" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_bytes(target, Path(pin["path"]).read_bytes())
            saved = identity(target)
            if (saved["bytes"], saved["sha256"]) != (pin["bytes"], pin["sha256"]):
                raise ValueError("Source changed while snapshotting")
            snapshots.append({"live": pin, "snapshot": saved})
        (directory / "inputs").mkdir()
        inputs = []
        for name, parent in zip(INPUTS, parents):
            item = {"id": name, "parent": parent, "status": "available",
                    "role": "previously_used_reference" if name == "blender" else "development_followup"}
            try:
                data, region = extract_exception_table(parent["path"], max_bytes=TABLE_LIMIT)
                table_bytes(data)
                target = directory / "inputs" / (name + ".table.bin")
                write_bytes(target, data)
                item.update(input=identity(target), region=region)
            except (ValueError, OSError) as exc:
                item.update(status="unavailable", reason=f"{type(exc).__name__}: {exc}")
            check_identity(parent)
            inputs.append(item)
        freeze = {"schema": 1, "prepared_utc": now(), "baseline_main_commit": "0dac8d2",
                  "research_checkpoint": "de5f78c", "git_head": subprocess.check_output(
                      ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                  "pins": pins, "snapshots": snapshots, "runtime": run_runtime, "inputs": inputs,
                  "input_ids": list(INPUTS), "methods": list(METHODS), "probe_methods": list(FIXED_METHODS),
                  "settings": SETTINGS, "limits": LIMITS, "worker_priority_class": BELOW_NORMAL,
                  "preparation_priority_class": controller_priority,
                  "owner_using_pc": True, "report_path": str(REPORT),
                  "scope": "Complete known exception tables; Blender reference plus five development follow-ups",
                  "timing_scope": "Single API observations; owner activity/shared worker state confound timing",
                  "diagnostic_scope": "Residual-only XZ excludes transform metadata and is not a standalone codec",
                  "memory_scope": "Job-wide evidence, not per-method memory peaks"}
        write_json(directory / "freeze.json", freeze)
        print(json.dumps({"prepared": str(directory), "inputs": [{"id": i["id"], "status": i["status"],
              "bytes": i.get("input", {}).get("bytes"), "reason": i.get("reason")} for i in inputs]}))


def verify_freeze(freeze, selected=None):
    check_core()
    check_runtime(freeze["runtime"])
    if (tuple(freeze["input_ids"]) != INPUTS or tuple(i["id"] for i in freeze["inputs"]) != INPUTS
            or tuple(freeze["methods"]) != METHODS or tuple(freeze["probe_methods"]) != FIXED_METHODS
            or freeze["settings"] != SETTINGS or freeze["limits"] != LIMITS
            or freeze["worker_priority_class"] != BELOW_NORMAL or freeze["owner_using_pc"] is not True
            or freeze["preparation_priority_class"] != BELOW_NORMAL
            or freeze["report_path"] != str(REPORT)):
        raise ValueError("Frozen protocol/settings differ")
    for pin in freeze["pins"]:
        check_identity(pin)
    for pair in freeze["snapshots"]:
        check_identity(pair["snapshot"])
    inventory()
    for item in freeze["inputs"]:
        if selected is None or selected == item["id"]:
            check_identity(item["parent"])
            if item["status"] == "available":
                check_identity(item["input"])


def marker(path, phase):
    Path(path).write_text(json.dumps({"phase": phase}), encoding="utf-8")


def check_restored(data, expected):
    if len(data) != expected["bytes"] or sha(data) != expected["sha256"]:
        raise ValueError("Restored table length/SHA-256 mismatch")


def worker(directory, input_id):
    actual_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze, input_id)
    import ahps
    from research import relational
    require_engine(True)
    native = identity(ahps.SIMD_BRIDGE.lib._name)
    if native != identity(ROOT / "ahps_simd.dll"):
        raise RuntimeError("Loaded native DLL differs from frozen baseline")
    item = next(i for i in freeze["inputs"] if i["id"] == input_id)
    if item["status"] != "available":
        raise ValueError("Unavailable input cannot run")
    expected = item["input"]
    data = table_bytes(Path(expected["path"]).read_bytes())
    check_restored(data, expected)
    attempt = directory / "runs" / input_id
    write_json(attempt / "worker-started.json", {"pid": os.getpid(), "started_utc": now(),
               "runtime": runtime(), "native": native, "abi": 206, "input": expected,
               "priority_class": actual_priority, "priority_set_before_heavy_work": True})
    failed = False
    for method in METHODS:
        path = attempt / method
        path.mkdir()
        record = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                  "started_utc": now(), "source_bytes": len(data), "source_sha256": sha(data),
                  "scope": "complete exception table"}
        write_json(path / "started.json", record)
        try:
            record["priority_before_encode"] = priority_class()
            marker(attempt / "phase.json", method + ":encode")
            start = time.perf_counter()
            if method == "xz":
                archive = lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6)
            elif method == "ahps_auto":
                archive, report = ahps.ahps_compress_bytes(data, block_size=4096,
                    force_python=False, lzma_preset=6, search=ahps.FAST_CHUNKS)
            else:
                archive = encode_fixed(data, method, preset=6, use_c=True)
            record["encode_api_seconds"] = time.perf_counter() - start
            write_bytes(path / "archive.bin", archive)
            record["archive"] = identity(path / "archive.bin")
            record["priority_before_decode"] = priority_class()
            marker(attempt / "phase.json", method + ":decode")
            start = time.perf_counter()
            if method == "xz":
                restored = bounded_xz(archive, TABLE_LIMIT)
            elif method == "ahrl_word_delta":
                restored = relational.decode(archive, max_output_bytes=TABLE_LIMIT)
            else:
                restored = ahps.ahps_decompress_bytes(archive, force_python=False,
                    limits=ahps.DecodeLimits(max_dict_bytes=128 * 1024**2,
                        max_container_bytes=TABLE_LIMIT + 1024**2, max_output_bytes=TABLE_LIMIT))
            record["decode_api_seconds"] = time.perf_counter() - start
            marker(attempt / "phase.json", method + ":verification")
            check_restored(restored, expected)
            write_bytes(path / "restored.bin", restored)
            record["restored"] = identity(path / "restored.bin")
            if method in FIXED_METHODS:
                residual, metadata = extract_fixed_residual(archive, method, use_c=True)
                record["metadata"] = metadata
                write_bytes(path / "residual.bin", residual)
                record["residual"] = identity(path / "residual.bin")
            elif method == "ahps_auto":
                record["metadata"] = automatic_metadata(archive)
                record["ahps_report"] = dataclasses.asdict(report)
            record.update(status="ok", priority_after_method=priority_class(), finished_utc=now())
        except Exception as exc:
            failed = True
            record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                          traceback=traceback.format_exc(), finished_utc=now())
        write_json(path / "result.json", record)
        print(json.dumps({"input": input_id, "method": method, "status": record["status"],
                          "archive_bytes": record.get("archive", {}).get("bytes")}), flush=True)

    # Probes follow all seven primary cells so diagnostics cannot consume the
    # early primary-method budget. Every probe has its own immutable record.
    probes = attempt / "probes"
    probes.mkdir()
    for method in FIXED_METHODS:
        path = probes / method
        path.mkdir()
        probe = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                 "started_utc": now(), "diagnostic_only": True}
        write_json(path / "started.json", probe)
        try:
            primary = read_json(attempt / method / "result.json")
            if primary["status"] != "ok":
                probe.update(status="unstarted_primary_failure", finished_utc=now())
            else:
                check_identity(primary["residual"])
                residual = Path(primary["residual"]["path"]).read_bytes()
                probe["residual"] = primary["residual"]
                probe["priority_before_encode"] = priority_class()
                marker(attempt / "phase.json", method + ":residual_encode")
                start = time.perf_counter()
                compressed = lzma.compress(residual, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6)
                probe["encode_api_seconds"] = time.perf_counter() - start
                write_bytes(path / "residual.xz", compressed)
                probe["compressed_residual"] = identity(path / "residual.xz")
                probe["priority_before_decode"] = priority_class()
                marker(attempt / "phase.json", method + ":residual_inverse")
                start = time.perf_counter()
                decoded_residual = bounded_xz(compressed, TABLE_LIMIT)
                if decoded_residual != residual:
                    raise ValueError("Residual XZ restoration mismatch")
                restored = restore_fixed_residual(decoded_residual, method, use_c=True)
                probe["decode_and_inverse_api_seconds"] = time.perf_counter() - start
                check_restored(restored, expected)
                write_bytes(path / "restored.bin", restored)
                probe["restored"] = identity(path / "restored.bin")
                probe.update(status="ok", priority_after_method=priority_class(), finished_utc=now())
        except Exception as exc:
            failed = True
            probe.update(status="error", error=f"{type(exc).__name__}: {exc}",
                         traceback=traceback.format_exc(), finished_utc=now())
        write_json(path / "result.json", probe)
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
                 "controller_priority_before": controller_priority,
                 "identities_before": "verified", "schedule": list(INPUTS), "records": []}
        write_json(runs / "started.json", batch)
        try:
            for item in freeze["inputs"]:
                priority_class()
                input_id = item["id"]
                attempt = runs / input_id
                attempt.mkdir()
                if item["status"] != "available":
                    result = {"input": input_id, "status": "unavailable", "reason": item["reason"]}
                elif time.monotonic() >= deadline:
                    result = {"input": input_id, "status": "unstarted_batch_deadline"}
                else:
                    argv = [freeze["runtime"]["artifacts"]["python_exe"]["path"], "-I", "-B",
                            str(Path(__file__).resolve()), "worker", "--directory", str(directory), "--input", input_id]
                    result = run_job(argv, cwd=ROOT, deadline=min(deadline, time.monotonic() + LIMITS["table_worker_seconds"]),
                        memory_limit_bytes=LIMITS["job_committed_bytes"], phase_file=attempt / "phase.json",
                        stdout_path=attempt / "stdout.log", stderr_path=attempt / "stderr.log")
                    result["input"] = input_id
                write_json(attempt / "job.json", result)
                batch["records"].append({"input": input_id, "status": result["status"], "job": identity(attempt / "job.json")})
                print(json.dumps({"input": input_id, "status": result["status"], "elapsed_seconds": result.get("elapsed_seconds")}), flush=True)
            verify_freeze(freeze)
            batch["identities_after"] = "verified"
            batch["controller_priority_after"] = priority_class()
            batch["status"] = "ok" if all(r["status"] == "ok" for r in batch["records"]) else "incomplete_or_failed"
        except BaseException as exc:
            batch.update(status="interrupted_or_error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            batch.update(finished_utc=now(), elapsed_seconds=time.monotonic() - start)
            write_json(runs / "finished.json", batch)
    return 0 if batch["status"] == "ok" else 1


def summarize(directory):
    controller_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze)
    batch = read_json(directory / "runs" / "finished.json")
    check_identity(batch["freeze"])
    for entry in batch["records"]:
        check_identity(entry["job"])
    records, probes, jobs = [], [], []
    verified_inputs = set()
    integrity = True
    for item in freeze["inputs"]:
        attempt = directory / "runs" / item["id"]
        job = read_json(attempt / "job.json") if (attempt / "job.json").exists() else None
        if job is not None:
            jobs.append(job)
        started, finished = attempt / "worker-started.json", attempt / "worker-finished.json"
        if job and job.get("status") == "ok" and started.exists() and finished.exists():
            first, last = read_json(started), read_json(finished)
            if (first["pid"] != job["pid"] or last["pid"] != job["pid"] or last["status"] != "ok"
                    or last.get("identities_after") != "verified" or first["runtime"] != freeze["runtime"]
                    or first["input"] != item["input"] or first["native"] != identity(ROOT / "ahps_simd.dll")
                    or first["abi"] != 206 or first["priority_class"] != BELOW_NORMAL
                    or not first.get("priority_set_before_heavy_work") or last["priority_class"] != BELOW_NORMAL
                    or not job.get("tree_cleaned") or not job.get("assignment_before_resume")
                    or job.get("breakaway_allowed") or job.get("returncode") != 0
                    or job.get("memory_limit_bytes") != LIMITS["job_committed_bytes"]):
                raise ValueError("Worker/Job identity, priority or completion mismatch")
            verified_inputs.add(item["id"])
        else:
            integrity = False
        for is_probe, methods, output in ((False, METHODS, records), (True, FIXED_METHODS, probes)):
            for method in methods:
                path = attempt / ("probes" if is_probe else "") / method / "result.json"
                if not path.exists():
                    output.append({"input": item["id"], "method": method, "status": "no_result",
                                   "job_status": job.get("status") if job else "unstarted"})
                    continue
                try:
                    row = read_json(path)
                except (ValueError, OSError) as exc:
                    # A deadline may terminate a worker during its exclusive
                    # JSON write. Preserve that partial artifact as a failed
                    # slot instead of losing the entire batch summary.
                    output.append({"input": item["id"], "method": method, "status": "unreadable_result",
                                   "job_status": job.get("status") if job else "unstarted",
                                   "error": f"{type(exc).__name__}: {exc}", "path": str(path)})
                    integrity = False
                    continue
                if row["input"] != item["id"] or row["method"] != method or job is None or row["pid"] != job.get("pid"):
                    raise ValueError("Method/probe identity mismatch")
                if row["status"] == "ok":
                    for key in (("compressed_residual", "residual", "restored") if is_probe else ("archive", "restored")):
                        check_identity(row[key])
                    if not is_probe and method in FIXED_METHODS:
                        check_identity(row["residual"])
                    if (row["restored"]["bytes"] != item["input"]["bytes"]
                            or row["restored"]["sha256"] != item["input"]["sha256"]
                            or any(row[key] != BELOW_NORMAL for key in ("priority_before_encode", "priority_before_decode", "priority_after_method"))):
                        raise ValueError("Restoration/priority mismatch")
                output.append(row)
    complete_archives = len(records) == 42 and all(r["status"] == "ok" for r in records)
    complete_probes = len(probes) == 30 and all(r["status"] == "ok" for r in probes)
    complete = (complete_archives and complete_probes and integrity and batch["status"] == "ok"
                and batch.get("identities_after") == "verified"
                and batch.get("controller_priority_before") == BELOW_NORMAL
                and batch.get("controller_priority_after") == BELOW_NORMAL)
    analyses = []
    for item in freeze["inputs"]:
        rows = {r["method"]: r for r in records if r["input"] == item["id"] and r["status"] == "ok"}
        diagnostic = {r["method"]: r for r in probes if r["input"] == item["id"] and r["status"] == "ok"}
        summary = {"input": item["id"], "role": item["role"], "availability": item["status"],
                   "status": "ok" if (len(rows) == 7 and len(diagnostic) == 5
                                           and item["id"] in verified_inputs) else "incomplete",
                   "archive_bytes": {m: r["archive"]["bytes"] for m, r in rows.items()},
                   "residual_xz_bytes": {m: r["compressed_residual"]["bytes"] for m, r in diagnostic.items()}}
        if len(rows) == 7:
            sizes = summary["archive_bytes"]
            best_existing = min(EXISTING_METHODS, key=lambda m: sizes[m])
            best_fixed = min(FIXED_AHPS, key=lambda m: sizes[m])
            word = sizes["ahrl_word_delta"]
            summary.update(best_existing=best_existing, best_fixed_ahps=best_fixed,
                word_saving_vs_fixed_d1_percent=100 * (1 - word / sizes["ahps_delta1_split12"]),
                word_saving_vs_best_existing_percent=100 * (1 - word / sizes[best_existing]),
                word_saving_vs_auto_percent=100 * (1 - word / sizes["ahps_auto"]),
                best_fixed_saving_vs_auto_percent=100 * (1 - sizes[best_fixed] / sizes["ahps_auto"]),
                word_framed_beats_best_existing=word < sizes[best_existing])
        if len(diagnostic) == 5:
            sizes = summary["residual_xz_bytes"]
            summary.update(word_residual_saving_vs_d1_percent=100 * (1 - sizes["ahrl_word_delta"] / sizes["ahps_delta1_split12"]),
                           word_residual_beats_d1=sizes["ahrl_word_delta"] < sizes["ahps_delta1_split12"])
        if summary["status"] == "ok":
            summary["word_candidate_supported_on_table"] = (summary["word_framed_beats_best_existing"]
                                                             and summary["word_residual_beats_d1"])
        analyses.append(summary)
    report = {"schema": 1, "created_utc": now(), "freeze": freeze, "batch": batch, "jobs": jobs,
              "records": records, "residual_probes": probes, "per_input": analyses,
              "planned_archive_cells": 42, "completed_archive_roundtrips": sum(r["status"] == "ok" for r in records),
              "planned_residual_probes": 30, "completed_residual_checks": sum(r["status"] == "ok" for r in probes),
              "all_cells_verified": complete,
              "summary_controller_priority_class": controller_priority,
              "control_scope": "Best tested existing control among bare XZ, four fixed AHPS programs and automatic AHPS",
              "verdict": "complete_same_boundary_screen" if complete else "incomplete_or_failed",
              "limitations": ["Blender is a reused reference; other tables are development follow-ups, not final holdouts.",
                              "Table savings are not whole-executable gains.",
                              "Residual-only XZ excludes transform metadata and is not a standalone format.",
                              "Compressed header/residual sizes are not additive costs.",
                              "D1 and word delta match first-record warmup and lane order; D2 is not an order-matched arithmetic control.",
                              "The tested existing controls do not exhaust all possible AHPS programs.",
                              "Single-pass timings are confounded by PC use; Job peaks are not per-method memory.",
                              "Fixed-program wins identify selection/boundary opportunities, not their precise cause."]}
    write_json(REPORT, report)
    print(json.dumps({"report": str(REPORT), "verdict": report["verdict"],
                      "archive_roundtrips": report["completed_archive_roundtrips"],
                      "residual_checks": report["completed_residual_checks"], "per_input": analyses}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize", "worker"))
    parser.add_argument("--directory", type=Path, default=DIRECTORY)
    parser.add_argument("--input", choices=INPUTS)
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
