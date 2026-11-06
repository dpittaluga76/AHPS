#!/usr/bin/env python3
"""Frozen whole-file comparison of optional exception-table word deltas.

Three encodes and one stable archive selection per complete development file.
Preparation, execution and summary are exclusive, immutable operations. Timings
are diagnostic observations while the owner uses the PC, not throughput claims.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
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
    check_identity, check_runtime, extract_exception_table, identity, now,
    read_json, runtime, sha, shared_lock, write_bytes, write_json,
)
from research.compare_relational_corpus import (
    BELOW_NORMAL, CORPUS, FILENAMES, check_core, inventory, priority_class,
    set_below_normal_priority,
)

DIRECTORY = ROOT / "experiments" / "relational-hybrid-20261103"
REPORT = ROOT / "reports" / "relational-hybrid-20261103.json"
PRIOR_REPORT = ROOT / "reports" / "relational-corpus-20261103.json"
FILE_CAP = 128 * 1024**2
TABLE_CAP = 8 * 1024**2
METHODS = ("ahps", "aligned_byte", "aligned_word", "selective_hybrid")
ENCODE_METHODS = METHODS[:3]
SELECTED_NAMES = {"baseline": "ahps", "byte": "aligned_byte", "word": "aligned_word"}
LIMITS = {"input_worker_seconds": 300, "batch_seconds": 900,
          "job_committed_bytes": 2 * 1024**3}
SETTINGS = {"preset": 6, "xz_check": "CRC64", "file_cap_bytes": FILE_CAP,
            "table_cap_bytes": TABLE_CAP, "width": 4, "fields": 3, "stride": 12,
            "ahps_block_bytes": 4096, "ahps_features": "DEFAULT_FEATURES",
            "ahps_search": {"chunk_trial_preset": 1, "exe32_chunk_rival": False},
            "research_magic_hex": b"AHRH\x01".hex(), "word_opcode": 10,
            "selection_order": list(ENCODE_METHODS)}
BASE_SOURCES = (
    "ahps.py", "ahps_simd.c", "ahps_simd.dll", "benchmark_ahps.py",
    "research/__init__.py", "research/relational.py", "research/relational_file.py",
    "research/relational_hybrid.py", "research/compare_relational.py",
    "research/compare_relational_corpus.py", "research/compare_relational_hybrid.py",
    "research/job_runner.py", "docs/selective-word-hybrid-experiment.md",
    "docs/format.md", "reports/evaluation-protocol.md", "corpus_manifest.json",
    "reports/relational-corpus-20261103.json",
)


def source_names():
    tests = sorted(path.name for path in ROOT.glob("test_*.py"))
    if not {"test_relational.py", "test_relational_file.py", "test_relational_hybrid.py"}.issubset(tests):
        raise ValueError("Core, wrapper and hybrid regression tests must exist before freezing")
    return list(BASE_SOURCES) + tests


def historical_baselines():
    """Pin the four completed old baselines; Node had no completed AHPS result."""
    previous = read_json(PRIOR_REPORT)
    if tuple(previous["freeze"]["input_ids"]) != FILENAMES:
        raise ValueError("Historical report does not cover the exact five-file cohort")
    result = {}
    for name in FILENAMES:
        rows = [row for row in previous["records"] if row["input"] == name and row["method"] == "ahps"]
        if len(rows) != 1:
            raise ValueError("Historical AHPS result slot missing or duplicated")
        row = rows[0]
        if name == "node.exe":
            if row["status"] == "ok":
                raise ValueError("Node unexpectedly has a completed historical baseline")
            result[name] = None
        else:
            if row["status"] != "ok":
                raise ValueError("Expected completed historical baseline: " + name)
            check_identity(row["archive"])
            result[name] = {"archive": row["archive"], "source_bytes": row["source_bytes"],
                            "source_sha256": row["source_sha256"]}
    return result


def manifest_entries():
    result = {}
    paths = {"corpus_development/" + name for name in FILENAMES}
    for item in read_json(ROOT / "corpus_manifest.json")["development"]:
        if item.get("path") in paths:
            name = Path(item["path"]).name
            if item.get("split") != "development" or name in result:
                raise ValueError("Invalid or duplicate development manifest entry")
            result[name] = item
    if tuple(sorted(result)) != FILENAMES:
        raise ValueError("Manifest must contain the five exact complete development files")
    return result


def prepare(directory):
    controller_priority = set_below_normal_priority()
    directory = directory.resolve()
    if (not directory.is_relative_to(ROOT / "experiments") or directory.exists()
            or REPORT.exists()):
        raise ValueError("Use a new experiment directory; no overwrite or resume")
    with shared_lock():
        check_core()
        inventory()
        previous = historical_baselines()
        pins = [identity(ROOT / name) for name in source_names()]
        run_runtime = runtime()
        manifest = manifest_entries()
        parents = []
        for name in FILENAMES:
            parent = identity(CORPUS / name)
            expected = manifest[name]
            if ((parent["bytes"], parent["sha256"]) != (expected["bytes"], expected["sha256"])
                    or parent["bytes"] > FILE_CAP):
                raise ValueError("Development input differs from manifest or exceeds cap: " + name)
            old = previous[name]
            if old and (old["source_bytes"], old["source_sha256"]) != (parent["bytes"], parent["sha256"]):
                raise ValueError("Historical baseline used a different complete input")
            parents.append(parent)
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
        for name, parent in zip(FILENAMES, parents):
            target = directory / "inputs" / name
            with Path(parent["path"]).open("rb") as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest, length=1024**2)
            snapshot = identity(target)
            check_identity(parent)
            if (snapshot["bytes"], snapshot["sha256"]) != (parent["bytes"], parent["sha256"]):
                raise ValueError("Complete source snapshot differs from its parent")
            item = {"id": name, "manifest_id": manifest[name]["id"], "source": parent,
                    "input": snapshot, "scope": "complete file", "table_status": "available"}
            try:
                table, region = extract_exception_table(target, max_bytes=TABLE_CAP)
                if (not 0 < len(table) <= TABLE_CAP or len(table) % 12
                        or region["bytes"] != len(table)
                        or not 0 <= region["file_offset"] <= snapshot["bytes"] - len(table)):
                    raise ValueError("Invalid complete exception table bounds")
                item.update(region=region, table_sha256=sha(table))
                del table
            except (ValueError, OSError) as exc:
                item.update(table_status="unavailable", table_reason=f"{type(exc).__name__}: {exc}")
            check_identity(snapshot)
            check_identity(parent)
            inputs.append(item)
        inventory()
        for pin in pins:
            check_identity(pin)
        for parent in parents:
            check_identity(parent)
        check_runtime(run_runtime)
        freeze = {"schema": 1, "prepared_utc": now(), "baseline_main_commit": "0dac8d2",
                  "research_checkpoint": "e16f3b2", "git_head": subprocess.check_output(
                      ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                  "corpus_directory": str(CORPUS.resolve()), "input_ids": list(FILENAMES),
                  "methods": list(METHODS), "pins": pins, "snapshots": snapshots, "runtime": run_runtime,
                  "inputs": inputs, "settings": SETTINGS, "limits": LIMITS,
                  "historical_report": identity(PRIOR_REPORT), "historical_baselines": previous,
                  "preparation_priority_class": controller_priority, "worker_priority_class": BELOW_NORMAL,
                  "owner_using_pc": True, "report_path": str(REPORT),
                  "timing_scope": "Single-pass encode component sums exclude metadata serialization, verification and artifact I/O; not standalone full encode API measurements",
                  "memory_scope": "Shared Job peaks across all methods; not per-method memory comparisons",
                  "comparison": "Whole-file solid-XZ archives; aligned controls share boundaries, outside programs and framing",
                  "reproduction": ["python -B research/compare_relational_hybrid.py " + command
                                   for command in ("prepare", "run", "summarize")]}
        write_json(directory / "freeze.json", freeze)
        print(json.dumps({"prepared": str(directory), "inputs": [{"id": i["id"],
              "bytes": i["input"]["bytes"], "table_status": i["table_status"],
              "table_bytes": i.get("region", {}).get("bytes")} for i in inputs]}))


def verify_freeze(freeze, selected=None):
    check_core()
    check_runtime(freeze["runtime"])
    if (tuple(freeze["input_ids"]) != FILENAMES or tuple(i["id"] for i in freeze["inputs"]) != FILENAMES
            or tuple(freeze["methods"]) != METHODS or freeze["settings"] != SETTINGS
            or freeze["limits"] != LIMITS or freeze["worker_priority_class"] != BELOW_NORMAL
            or freeze["preparation_priority_class"] != BELOW_NORMAL or freeze["owner_using_pc"] is not True
            or freeze["corpus_directory"] != str(CORPUS.resolve()) or freeze["report_path"] != str(REPORT)):
        raise ValueError("Frozen settings, cohort or resource limits changed")
    expected_paths = [str((ROOT / name).resolve()) for name in source_names()]
    if [pin["path"] for pin in freeze["pins"]] != expected_paths:
        raise ValueError("Frozen source/test inventory changed")
    if len(freeze["snapshots"]) != len(freeze["pins"]):
        raise ValueError("Incomplete source snapshots")
    for pin, pair in zip(freeze["pins"], freeze["snapshots"]):
        check_identity(pin)
        check_identity(pair["snapshot"])
        if (pair["live"] != pin or (pin["bytes"], pin["sha256"]) !=
                (pair["snapshot"]["bytes"], pair["snapshot"]["sha256"])):
            raise ValueError("Frozen source snapshot mismatch")
    check_identity(freeze["historical_report"])
    if freeze["historical_report"] != identity(PRIOR_REPORT) or freeze["historical_baselines"] != historical_baselines():
        raise ValueError("Historical baseline evidence changed")
    inventory()
    manifest = manifest_entries()
    for item in freeze["inputs"]:
        if item["table_status"] not in ("available", "unavailable"):
            raise ValueError("Unknown table availability")
        expected = manifest[item["id"]]
        if (item["manifest_id"] != expected["id"] or item["source"]["path"] != str((CORPUS / item["id"]).resolve())
                or (item["source"]["bytes"], item["source"]["sha256"]) != (expected["bytes"], expected["sha256"])
                or (item["input"]["bytes"], item["input"]["sha256"]) != (expected["bytes"], expected["sha256"])
                or item["input"]["bytes"] > FILE_CAP):
            raise ValueError("Frozen input no longer agrees with its manifest")
        if selected is None or item["id"] == selected:
            check_identity(item["source"])
            check_identity(item["input"])


def marker(path, phase):
    # Ephemeral Job observation only; all evidence artifacts use exclusive writes.
    Path(path).write_text(json.dumps({"phase": phase}), encoding="utf-8")


def check_restored(data, expected):
    if len(data) != expected["bytes"] or sha(data) != expected["sha256"]:
        raise ValueError("Complete file length/SHA-256 mismatch")


def check_control_plans(baseline, byte, word, source_bytes, table_start, table_length):
    """Independent descriptor audit: only the table's arithmetic may differ."""
    table_end = table_start + table_length
    for plan in (baseline, byte, word):
        cursor = 0
        for segment in plan:
            if segment["start"] != cursor or segment["length"] <= 0:
                raise ValueError("Plan has a gap, overlap or empty segment")
            cursor += segment["length"]
        if cursor != source_bytes:
            raise ValueError("Plan does not cover the complete source")
    if not (0 <= table_start < table_end <= source_bytes and table_length % 12 == 0):
        raise ValueError("Invalid table interval")
    if [(s["start"], s["length"]) for s in byte] != [(s["start"], s["length"]) for s in word]:
        raise ValueError("Aligned controls have different boundaries")
    indices = [i for i, s in enumerate(byte) if (s["start"], s["length"]) == (table_start, table_length)]
    if len(indices) != 1:
        raise ValueError("Aligned plan must contain exactly one complete-table segment")
    table_index = indices[0]
    split = {"opcode": 7, "params_hex": "0c000000"}
    if byte[table_index]["program"] != [{"opcode": 2, "params_hex": "0c000000"}, split]:
        raise ValueError("Byte table does not use the fixed D1+SPLIT control")
    if word[table_index]["program"] != [{"opcode": 10, "params_hex": "0c000000"}, split]:
        raise ValueError("Word table does not use the fixed WDELTA32+SPLIT control")
    outside = [s for i, s in enumerate(byte) if i != table_index]
    if outside != [s for i, s in enumerate(word) if i != table_index]:
        raise ValueError("Aligned controls changed outside programs")
    expected = []
    for segment in baseline:
        start, end = segment["start"], segment["start"] + segment["length"]
        for left, right in ((start, min(end, table_start)), (max(start, table_end), end)):
            if left >= right:
                continue
            program = []
            for step in segment["program"]:
                params = bytes.fromhex(step["params_hex"])
                if step["opcode"] in (8, 9):
                    params = ((int.from_bytes(params, "big") + left - start) & 0xFFFFFFFF).to_bytes(4, "big")
                program.append({"opcode": step["opcode"], "params_hex": params.hex()})
            expected.append({"start": left, "length": right - left, "program": program})
    if outside != expected:
        raise ValueError("Outside fragments do not preserve/rebase the baseline programs")
    return True


def check_selection(rows, table_available):
    """Audit complete archive minimum and tie order from verified artifact hashes."""
    candidates = ENCODE_METHODS if table_available else ("ahps",)
    selected = rows["selective_hybrid"]
    sizes = {method: rows[method]["archive"]["bytes"] for method in candidates}
    expected = min(candidates, key=lambda method: sizes[method])
    if (selected["selected_method"] != expected or selected["candidate_bytes"] != sizes
            or selected["archive"]["bytes"] != sizes[expected]
            or selected["archive"]["sha256"] != rows[expected]["archive"]["sha256"]
            or selected["plan"] != rows[expected]["plan"]):
        raise ValueError("Selected archive differs from the stable minimum candidate")
    if selected["archive"]["bytes"] > rows["ahps"]["archive"]["bytes"]:
        raise ValueError("Selected archive exceeds the exact AHPS fallback")
    return expected


def worker(directory, input_id):
    actual_priority = set_below_normal_priority()
    freeze = read_json(directory / "freeze.json")
    verify_freeze(freeze, input_id)
    import ahps
    from research import relational_hybrid as codec
    if ahps.SIMD_BRIDGE.lib is None or ahps.SIMD_BRIDGE.lib.ahps_abi_version() != 206:
        raise RuntimeError("Native ABI 206 required; silent fallback forbidden")
    native = identity(ahps.SIMD_BRIDGE.lib._name)
    if native != identity(ROOT / "ahps_simd.dll"):
        raise RuntimeError("Loaded native DLL differs from the frozen baseline")
    item = next(i for i in freeze["inputs"] if i["id"] == input_id)
    expected = item["input"]
    data = Path(expected["path"]).read_bytes()
    check_restored(data, expected)
    available = item["table_status"] == "available"
    if available:
        region = item["region"]
        if sha(memoryview(data)[region["file_offset"]:region["file_offset"] + region["bytes"]]) != item["table_sha256"]:
            raise ValueError("Actual exception table differs from frozen extraction")
    attempt = directory / "runs" / input_id
    write_json(attempt / "worker-started.json", {"pid": os.getpid(), "started_utc": now(),
               "runtime": runtime(), "native": native, "abi": 206, "input": expected,
               "priority_class": actual_priority, "priority_set_before_heavy_work": True})
    archives, rows, plans = {}, {}, {}
    failed = False
    baseline_segments = None
    for method in METHODS:
        path = attempt / method
        path.mkdir()
        record = {"input": input_id, "method": method, "status": "started", "pid": os.getpid(),
                  "started_utc": now(), "source_bytes": len(data), "source_sha256": expected["sha256"],
                  "complete_file": True, "table_status": item["table_status"]}
        write_json(path / "started.json", record)
        try:
            if failed:
                record.update(status="unstarted_dependency_error", reason="An earlier required phase failed")
            elif method in ("aligned_byte", "aligned_word") and not available:
                record.update(status="unavailable_table", reason=item["table_reason"])
            else:
                record["priority_before_encode"] = priority_class()
                marker(attempt / "phase.json", method + ":encode")
                if method == "ahps":
                    start = time.perf_counter()
                    archive, baseline_report = codec.baseline_archive(data, use_c=True, preset=6)
                    record["encode_api_seconds"] = time.perf_counter() - start
                    record["ahps_report"] = dataclasses.asdict(baseline_report)
                    record["actual_archive_encode"] = True
                elif method in ("aligned_byte", "aligned_word"):
                    start = time.perf_counter()
                    segments = codec.aligned_plan(baseline_segments, len(data), region["file_offset"],
                                                  region["bytes"], word=method == "aligned_word")
                    record["plan_build_seconds"] = time.perf_counter() - start
                    start = time.perf_counter()
                    archive = codec.encode_plan(data, segments, use_c=True, preset=6)
                    record["encode_api_seconds"] = time.perf_counter() - start
                    record["build_and_encode_seconds"] = record["plan_build_seconds"] + record["encode_api_seconds"]
                    record["actual_archive_encode"] = True
                    plans[method] = codec.serializable_plan(segments)
                    del segments
                else:
                    marker(attempt / "phase.json", method + ":selection")
                    start = time.perf_counter()
                    archive, selected_name = codec.select_archive(archives["ahps"],
                        archives.get("aligned_byte"), archives.get("aligned_word"))
                    record["selection_api_seconds"] = time.perf_counter() - start
                    selected_method = SELECTED_NAMES[selected_name]
                    record.update(selected_method=selected_method,
                                  candidate_bytes={name: len(value) for name, value in archives.items()},
                                  actual_archive_encode=False,
                                  fallback_exact_bytes=(archive == archives["ahps"]) if selected_method == "ahps" else None)
                    plans[method] = plans[selected_method]
                    components = {"baseline_encode": rows["ahps"]["encode_api_seconds"],
                                  "baseline_plan_recovery": rows["ahps"]["plan_recovery_seconds"],
                                  "selection": record["selection_api_seconds"]}
                    for candidate in ("aligned_byte", "aligned_word"):
                        if available:
                            components[candidate + "_build_encode"] = rows[candidate]["build_and_encode_seconds"]
                    record["encode_component_seconds"] = components
                    record["encode_component_sum_seconds"] = sum(components.values())
                    record["timing_note"] = "Measured encode component sum; excludes metadata serialization, verification and artifact I/O; not standalone full encode API timing"
                write_bytes(path / "archive.bin", archive)
                record["archive"] = identity(path / "archive.bin")
                record["priority_before_decode"] = priority_class()
                marker(attempt / "phase.json", method + ":decode")
                record["decoder_api"] = ("ahps.ahps_decompress_bytes" if method == "ahps"
                                         else "research.relational_hybrid.decode")
                start = time.perf_counter()
                if method == "ahps":
                    restored = ahps.ahps_decompress_bytes(archive, force_python=False,
                        limits=ahps.DecodeLimits(max_dict_bytes=128 * 1024**2,
                            max_container_bytes=25 + codec.SEGMENT_TABLE_CAP + FILE_CAP,
                            max_output_bytes=len(data)))
                else:
                    restored = codec.decode(archive, use_c=True, max_output_bytes=len(data))
                record["decode_api_seconds"] = time.perf_counter() - start
                marker(attempt / "phase.json", method + ":verification")
                check_restored(restored, expected)
                write_bytes(path / "restored.bin", restored)
                record["restored"] = identity(path / "restored.bin")
                record["roundtrip_verified"] = True
                del restored
                if method == "ahps":
                    marker(attempt / "phase.json", method + ":plan_recovery")
                    start = time.perf_counter()
                    baseline_segments = codec.baseline_plan(archive, max_output_bytes=len(data))
                    record["plan_recovery_seconds"] = time.perf_counter() - start
                    plans[method] = codec.serializable_plan(baseline_segments)
                    historical = freeze["historical_baselines"][input_id]
                    record["historical_archive_match"] = None
                    if historical:
                        prior = historical["archive"]
                        if (record["archive"]["bytes"], record["archive"]["sha256"]) != (prior["bytes"], prior["sha256"]):
                            raise ValueError("Fresh AHPS archive differs from the frozen historical baseline")
                        record["historical_archive_match"] = True
                elif method in ("aligned_byte", "aligned_word"):
                    metadata = codec.inspect_archive(archive, max_output_bytes=len(data))
                    if metadata["segments"] != plans[method]:
                        raise ValueError("Serialized archive plan differs from the intended aligned plan")
                    record["container_metadata"] = metadata
                    if method == "aligned_word":
                        check_control_plans(plans["ahps"], plans["aligned_byte"], plans["aligned_word"],
                                            len(data), region["file_offset"], region["bytes"])
                        record["matched_outside_plans_verified"] = True
                record["plan"] = plans[method]
                if method == "selective_hybrid":
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
                          "archive_bytes": record.get("archive", {}).get("bytes")}), flush=True)
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
            for input_id in FILENAMES:
                priority_class()
                attempt = runs / input_id
                attempt.mkdir()
                if time.monotonic() >= deadline:
                    result = {"input": input_id, "status": "unstarted_batch_deadline"}
                else:
                    argv = [freeze["runtime"]["artifacts"]["python_exe"]["path"], "-I", "-B",
                            str(Path(__file__).resolve()), "worker", "--directory", str(directory), "--input", input_id]
                    result = run_job(argv, cwd=ROOT,
                        deadline=min(deadline, time.monotonic() + LIMITS["input_worker_seconds"]),
                        memory_limit_bytes=LIMITS["job_committed_bytes"], phase_file=attempt / "phase.json",
                        stdout_path=attempt / "stdout.log", stderr_path=attempt / "stderr.log")
                    result["input"] = input_id
                write_json(attempt / "job.json", result)
                batch["records"].append({"input": input_id, "status": result["status"], "job": identity(attempt / "job.json")})
                print(json.dumps({"input": input_id, "status": result["status"], "elapsed_seconds": result.get("elapsed_seconds")}), flush=True)
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


def read_partial(path):
    """A timeout can leave an exclusive JSON write partial; retain that slot."""
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (ValueError, OSError) as exc:
        return {"status": "unreadable_result", "error": f"{type(exc).__name__}: {exc}",
                "artifact": identity(path)}


def check_timing(row):
    for key, value in row.items():
        if key.endswith("_seconds"):
            values = value.values() if key == "encode_component_seconds" and isinstance(value, dict) else (value,)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
                raise ValueError("Invalid recorded phase time")


def summarize(directory):
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
            raise ValueError("Controller schedule, priority or batch deadline mismatch")
        for entry in batch["records"]:
            check_identity(entry["job"])
        native = identity(ROOT / "ahps_simd.dll")
        records, jobs, verified_inputs = [], [], set()
        previous_job_end = batch["started_monotonic"]
        for item in freeze["inputs"]:
            attempt = directory / "runs" / item["id"]
            job = read_partial(attempt / "job.json")
            if job is not None:
                jobs.append(job)
            first = read_partial(attempt / "worker-started.json")
            last = read_partial(attempt / "worker-finished.json")
            if job and "pid" in job:
                if (not job.get("tree_cleaned") or not job.get("assignment_before_resume")
                        or job.get("breakaway_allowed") or job.get("memory_limit_bytes") != LIMITS["job_committed_bytes"]
                        or job["deadline_monotonic"] > batch["deadline_monotonic"] + 0.01
                        or job["deadline_monotonic"] - job["started_monotonic"] > LIMITS["input_worker_seconds"] + 0.01
                        or job["started_monotonic"] < previous_job_end - 0.05):
                    raise ValueError("Job resources, deadline, serial schedule or tree cleanup mismatch")
                previous_job_end = job["started_monotonic"] + job["elapsed_seconds"]
            if first and first.get("status") != "unreadable_result":
                if (job is None or first["pid"] != job.get("pid") or first["runtime"] != freeze["runtime"]
                        or first["native"] != native or first["abi"] != 206 or first["input"] != item["input"]
                        or first["priority_class"] != BELOW_NORMAL or not first.get("priority_set_before_heavy_work")):
                    raise ValueError("Worker identity, native ABI, runtime or priority mismatch")
            if job and job.get("status") == "ok":
                if (not first or not last or first.get("status") == "unreadable_result"
                        or last.get("status") != "ok" or last["pid"] != job["pid"]
                        or last.get("identities_after") != "verified" or last["priority_class"] != BELOW_NORMAL
                        or job.get("returncode") != 0):
                    raise ValueError("Successful Job lacks verified worker completion")
                verified_inputs.add(item["id"])
            for method in METHODS:
                path = attempt / method / "result.json"
                row = read_partial(path)
                if row is None or row.get("status") == "unreadable_result":
                    started = read_partial(attempt / method / "started.json")
                    records.append({"input": item["id"], "method": method,
                                    "status": "no_result" if row is None else "unreadable_result",
                                    "job_status": job.get("status") if job else "unstarted",
                                    "started": started, "partial_result": row})
                    continue
                if (row["input"] != item["id"] or row["method"] != method or job is None or row["pid"] != job.get("pid")):
                    raise ValueError("Result does not belong to the scheduled worker")
                for key in ("archive", "restored"):
                    if key in row:
                        check_identity(row[key])
                if row["status"] == "ok":
                    if (not row.get("roundtrip_verified") or not row.get("complete_file")
                            or row["source_bytes"] != item["input"]["bytes"] or row["source_sha256"] != item["input"]["sha256"]
                            or (row["restored"]["bytes"], row["restored"]["sha256"]) != (item["input"]["bytes"], item["input"]["sha256"])
                            or any(row[key] != BELOW_NORMAL for key in ("priority_before_encode", "priority_before_decode", "priority_after_method"))):
                        raise ValueError("Full-file restoration or method priority mismatch")
                    check_timing(row)
                    if method in ENCODE_METHODS and row.get("actual_archive_encode") is not True:
                        raise ValueError("A successful candidate must record an actual archive encode")
                    expected_decoder = ("ahps.ahps_decompress_bytes" if method == "ahps"
                                        else "research.relational_hybrid.decode")
                    if row["decoder_api"] != expected_decoder:
                        raise ValueError("Recorded decoder path differs from the protocol")
                    if method == "ahps":
                        historical = freeze["historical_baselines"][item["id"]]
                        if historical:
                            prior = historical["archive"]
                            if (row.get("historical_archive_match") is not True
                                    or (row["archive"]["bytes"], row["archive"]["sha256"]) != (prior["bytes"], prior["sha256"])):
                                raise ValueError("Baseline differs from historical archive")
                        elif row.get("historical_archive_match") is not None:
                            raise ValueError("Unexpected historical baseline claim")
                records.append(row)
        per_input = []
        for item in freeze["inputs"]:
            rows = {r["method"]: r for r in records if r["input"] == item["id"] and r["status"] == "ok"}
            available = item["table_status"] == "available"
            required = METHODS if available else ("ahps", "selective_hybrid")
            complete_file = all(method in rows for method in required) and item["id"] in verified_inputs
            result = {"input": item["id"], "source_bytes": item["input"]["bytes"],
                      "table_status": item["table_status"],
                      "status": ("ok" if available else "verified_fallback_table_unavailable") if complete_file else "incomplete",
                      "archive_bytes": {method: row["archive"]["bytes"] for method, row in rows.items()}}
            if all(method in rows for method in ENCODE_METHODS):
                region = item["region"]
                check_control_plans(rows["ahps"]["plan"], rows["aligned_byte"]["plan"], rows["aligned_word"]["plan"],
                                    item["input"]["bytes"], region["file_offset"], region["bytes"])
                for method in ("aligned_byte", "aligned_word"):
                    if rows[method]["container_metadata"]["segments"] != rows[method]["plan"]:
                        raise ValueError("Recorded serialized plan mismatch")
                sizes = result["archive_bytes"]
                result.update(word_saving_vs_aligned_byte_bytes=sizes["aligned_byte"] - sizes["aligned_word"],
                    word_saving_vs_aligned_byte_percent=100 * (1 - sizes["aligned_word"] / sizes["aligned_byte"]),
                    aligned_byte_saving_vs_original_bytes=sizes["ahps"] - sizes["aligned_byte"],
                    outside_descriptors_match=True)
            if all(method in rows for method in required):
                winner = check_selection(rows, available)
                selected = rows["selective_hybrid"]
                components = {"baseline_encode": rows["ahps"]["encode_api_seconds"],
                              "baseline_plan_recovery": rows["ahps"]["plan_recovery_seconds"],
                              "selection": selected["selection_api_seconds"]}
                if available:
                    for method in ("aligned_byte", "aligned_word"):
                        row = rows[method]
                        if row["build_and_encode_seconds"] != row["plan_build_seconds"] + row["encode_api_seconds"]:
                            raise ValueError("Candidate timing component sum mismatch")
                        components[method + "_build_encode"] = row["build_and_encode_seconds"]
                if (selected["encode_component_seconds"] != components or selected["encode_component_sum_seconds"] != sum(components.values())
                        or selected.get("actual_archive_encode") is not False
                        or (winner == "ahps" and selected.get("fallback_exact_bytes") is not True)):
                    raise ValueError("Selection timing or exact fallback claim mismatch")
                sizes = result["archive_bytes"]
                result.update(selected_method=winner, selected_never_larger_verified=True,
                    exact_original_fallback_verified=winner == "ahps",
                    selected_saving_vs_original_bytes=sizes["ahps"] - sizes["selective_hybrid"],
                    selected_saving_vs_original_percent=100 * (1 - sizes["selective_hybrid"] / sizes["ahps"]),
                    selected_encode_component_sum_seconds=selected["encode_component_sum_seconds"])
            per_input.append(result)
        complete = (len(records) == 20 and all(r["status"] == "ok" for r in records)
                    and len(verified_inputs) == 5 and batch["status"] == "ok"
                    and batch.get("identities_after") == "verified" and batch.get("controller_priority_after") == BELOW_NORMAL)
        totals = None
        if complete:
            sizes = {method: sum(r["archive"]["bytes"] for r in records if r["method"] == method) for method in METHODS}
            totals = {"source_bytes": sum(i["input"]["bytes"] for i in freeze["inputs"]), "archive_bytes": sizes,
                      "selected_saving_vs_original_bytes": sizes["ahps"] - sizes["selective_hybrid"],
                      "selected_saving_vs_original_percent": 100 * (1 - sizes["selective_hybrid"] / sizes["ahps"]),
                      "word_saving_vs_aligned_byte_bytes": sizes["aligned_byte"] - sizes["aligned_word"],
                      "word_saving_vs_aligned_byte_percent": 100 * (1 - sizes["aligned_word"] / sizes["aligned_byte"]),
                      "scope": "Ratio of summed complete archives over these five reused development files only"}
        report = {"schema": 1, "created_utc": now(), "freeze": freeze, "batch": batch, "jobs": jobs,
                  "records": records, "per_input": per_input, "totals": totals,
                  "planned_archive_encodes": 15, "planned_output_roundtrips": 20,
                  "completed_archive_encodes": sum(r.get("actual_archive_encode") is True and "encode_api_seconds" in r for r in records),
                  "completed_output_roundtrips": sum(r.get("roundtrip_verified") is True for r in records),
                  "all_cells_verified": complete, "summary_controller_priority_class": controller_priority,
                  "verdict": "complete_selective_hybrid_screen" if complete else "incomplete_or_failed",
                  "limitations": ["Five reused development files, not holdouts or a general compression claim.",
                      "Aligned byte versus original includes boundary resets and framing changes.",
                      "Word versus aligned byte isolates the table arithmetic within matched outside plans and framing.",
                      "A selected aligned-byte win is not a word-arithmetic win; exact fallback alone is not a new compression gain.",
                      "The selected encode component sum includes all candidate builds/encodes, excluding metadata serialization, verification and artifact I/O; it is not a standalone full encode API measurement.",
                      "PC use confounds single-pass timings; shared Job peaks are not per-method memory comparisons.",
                      "No production adoption, other files, model tuning or additional backend experiments."]}
        verify_freeze(freeze)
        write_json(REPORT, report)
        print(json.dumps({"report": str(REPORT), "verdict": report["verdict"],
                          "encodes": report["completed_archive_encodes"], "roundtrips": report["completed_output_roundtrips"],
                          "per_input": per_input, "totals": totals}))


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
