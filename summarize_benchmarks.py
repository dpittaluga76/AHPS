#!/usr/bin/env python3
"""Summarize explicit benchmark_ahps JSONL reports without reopening measured data.

Example: summarize_benchmarks.py reports/run.jsonl --plan jobs.json --output summary.md
An optional plan lists {file_id, codec, features, search} jobs; features default on.
Use --selection-reference '{"chunk_trial_preset":null,"exe32_chunk_rival":true}'
for explicit AHPS selection comparisons instead of requiring a BCJ2 reference.
This utility reports development evidence, never research/product acceptance.
"""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path, PureWindowsPath
import re
import statistics
import sys

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import benchmark_ahps as bench

SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
TIME_BOUNDARY = "process launch through exit, including I/O"
ALL_ENABLED = (True,) * 4
LEGACY_SEARCH = ""
EXHAUSTIVE_SEARCH = {"chunk_trial_preset": None, "exe32_chunk_rival": True}


def feature_key(value):
    effective = bench.parse_features(json.dumps(value))
    return tuple(effective[name] for name in bench.FEATURE_NAMES)


def search_key(value):
    return bench.canonical_search(bench.parse_search(json.dumps(value)))


def selection_reference_key(value):
    if value is None:
        return None
    key = search_key(value)
    if key != search_key(EXHAUSTIVE_SEARCH):
        raise ValueError("Selection reference must be explicit exhaustive search: null trial preset, enabled EXE32 chunk rival")
    return key


def job_key(entry, plan=False):
    if plan and set(entry) - {"file_id", "codec", "features", "search", "decode_api"}:
        raise ValueError("Unknown plan job fields")
    file_id, codec = entry.get("file_id"), entry.get("codec")
    if not isinstance(file_id, str) or not file_id or codec not in bench.CODECS:
        raise ValueError("Each job needs a nonempty file_id and a supported codec")
    settings = entry.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    supplied = entry.get("features", {}) if plan else settings.get("features", {})
    features = feature_key(supplied)
    if codec != "ahps" and features != ALL_ENABLED:
        raise ValueError("Non-AHPS jobs cannot declare disabled AHPS features")
    container = entry if plan else settings
    search = search_key(container["search"]) if "search" in container else LEGACY_SEARCH
    if codec != "ahps":
        if search not in (LEGACY_SEARCH, search_key(EXHAUSTIVE_SEARCH)):
            raise ValueError("Non-AHPS jobs cannot declare nondefault AHPS search settings")
        search = LEGACY_SEARCH
    decode_api = container.get("decode_api", "bytes")  # Rows from before the file decoder used the bytes API.
    if decode_api not in ("bytes", "file") or (codec != "ahps" and decode_api != "bytes"):
        raise ValueError("decode_api must be bytes or file, and file applies only to AHPS")
    return file_id, codec, features if codec == "ahps" else (), search, decode_api


def positive_int(value):
    return type(value) is int and value > 0


def finite_positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def frozen(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def artifact_map(implementation):
    """Compare recorded identities only. Paths here are never statted or opened."""
    artifacts = implementation.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Missing implementation artifact identities")
    result = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise ValueError("Invalid implementation artifact")
        name = PureWindowsPath(artifact["path"]).name.casefold()
        digest, size = artifact.get("sha256"), artifact.get("bytes")
        if not name or name in result or not isinstance(digest, str) or not SHA256.fullmatch(digest) or not positive_int(size):
            raise ValueError("Invalid/duplicate implementation artifact identity")
        result[name] = (digest.lower(), size)
    return result


def implementation_signature(implementation):
    if not isinstance(implementation, dict) or not isinstance(implementation.get("git_commit"), str) or not implementation["git_commit"]:
        raise ValueError("Missing implementation commit")
    normalized = dict(implementation)
    normalized["artifacts"] = artifact_map(implementation)
    return frozen(normalized)


def settings_signature(row):
    settings = dict(row["settings"])
    settings.pop("features", None)
    if row["codec"] == "ahps":
        settings.pop("search", None)  # Validated separately as a configuration identity.
        settings.pop("decode_api", None)  # So is the decoder under test.
    if row["codec"] != "ahps":
        settings.pop("block_size", None)  # Old harness recorded this irrelevant field.
    return frozen(settings)


def row_issues(row, key, size_only=False, selection_mode=False):
    issues = []
    if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
        issues.append("unsupported row schema")
    if not isinstance(row.get("run_id"), str) or not row["run_id"]:
        issues.append("missing run_id")
    if not positive_int(row.get("repeat")):
        issues.append("repeat must be a positive integer")
    if selection_mode and key[1] == "ahps" and key[3] == LEGACY_SEARCH:
        issues.append("selection comparison requires explicit settings.search; legacy/default is not exhaustive evidence")
    if not positive_int(row["settings"].get("requested_repeats")):
        issues.append("requested_repeats must be a positive integer")
    elif not size_only and row["settings"]["requested_repeats"] < 3:
        issues.append("time/memory comparisons require at least three repetitions; use --size-only for short runs")
    if row.get("status") != "ok":
        issues.append("recorded failure: " + str(row.get("error", "status=" + str(row.get("status")))))
    if row.get("error") and row.get("status") == "ok":
        issues.append("error on an ok row: " + str(row["error"]))
    for phase in ("encode", "decode"):
        details = row.get("phases", {}).get(phase, {}) if isinstance(row.get("phases"), dict) else {}
        if not isinstance(details, dict):
            issues.append(phase + " phase is not an object")
            continue
        if details.get("error"):
            issues.append(phase + ": " + str(details["error"]))
        if row.get("status") == "ok":
            if type(details.get("returncode")) is not int or details["returncode"] != 0:
                issues.append(phase + " did not report exit code zero")
            if not size_only:
                if not finite_positive(details.get("elapsed_seconds")):
                    issues.append(phase + " time must be positive and finite")
                if not positive_int(details.get("peak_working_set_bytes")) or not finite_positive(details.get("peak_working_set_bytes")):
                    issues.append(phase + " peak must be positive integer bytes")
                if details.get("timing_boundary") != TIME_BOUNDARY:
                    issues.append(phase + " is not the required process timing boundary")
                if not isinstance(details.get("peak_method"), str) or not details["peak_method"]:
                    issues.append(phase + " peak measurement method is missing")
    if row.get("status") == "ok":
        source = row.get("source", {})
        if not isinstance(source, dict):
            source = {}
        digest, size = source.get("sha256"), source.get("bytes")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest) or type(size) is not int or size < 0:
            issues.append("invalid source hash/size")
        restored = row.get("restored_sha256")
        if (row.get("roundtrip_verified") is not True or not isinstance(restored, str)
                or not isinstance(digest, str) or restored.lower() != digest.lower()):
            issues.append("round trip is not verified against the source hash")
        if not positive_int(row.get("compressed_bytes")) or not finite_positive(row.get("compressed_bytes")):
            issues.append("archive size must be positive finite integer bytes")
        if not isinstance(row.get("compressed_sha256"), str) or not SHA256.fullmatch(row["compressed_sha256"]):
            issues.append("archive hash is missing/invalid")
        try:
            implementation_signature(row.get("implementation"))
            settings_signature(row)
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(str(exc))
        if key[1] == "ahps":
            reports = [row["report_features"]] if "report_features" in row else []
            phases = row.get("phases") if isinstance(row.get("phases"), dict) else {}
            encode = phases.get("encode") if isinstance(phases.get("encode"), dict) else {}
            worker = encode.get("worker", {})
            if isinstance(worker, dict) and "report_features" in worker:
                reports.append(worker["report_features"])
            for report in reports:
                try:
                    if feature_key(report) != key[2]:
                        issues.append("reported features differ from effective features")
                except ValueError as exc:
                    issues.append("invalid reported features: " + str(exc))
            if not isinstance(worker, dict):
                worker = {}
            confirmations = (("report_search", row), ("encode.worker.report_search", worker),
                             ("encode.worker.search", worker))
            if key[3] != LEGACY_SEARCH:
                for name, container in confirmations:
                    value = container.get(name.rsplit(".", 1)[-1])
                    try:
                        if not isinstance(value, dict) or set(value) != set(EXHAUSTIVE_SEARCH):
                            raise ValueError("requires both search fields")
                        if search_key(value) != key[3]:
                            issues.append(name + " differs from effective settings.search")
                    except (ValueError, TypeError) as exc:
                        issues.append(name + " is missing/invalid: " + str(exc))
            elif "report_search" in row or "report_search" in worker or "search" in worker:
                issues.append("search confirmations require explicit settings.search")
    return issues


def spread(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def summarize(rows, plan=None, input_issues=(), *, size_only=False, selection_reference=None):
    groups, problems = {}, list(input_issues)
    reference_key = selection_reference_key(selection_reference)
    selection_mode = reference_key is not None
    expected = None
    if plan is not None:
        if not isinstance(plan, list) or not plan:
            raise ValueError("Plan must be a nonempty list of explicit jobs")
        expected = set()
        for entry in plan:
            if not isinstance(entry, dict):
                raise ValueError("Plan jobs must be objects")
            key = job_key(entry, plan=True)
            if selection_mode and key[1] == "ahps" and key[3] == LEGACY_SEARCH:
                raise ValueError("Selection plan AHPS jobs require explicit search settings")
            if key in expected:
                raise ValueError("Duplicate planned job: " + str(key))
            expected.add(key)
    seen_ids = defaultdict(list)
    comparison_ids, execution_orders, execution_issues = set(), defaultdict(list), []
    for index, row in enumerate(rows, 1):
        location = row.get("_location", f"row {index}") if isinstance(row, dict) else f"row {index}"
        if selection_mode:
            comparison_id = row.get("comparison_id") if isinstance(row, dict) else None
            order = row.get("execution_order") if isinstance(row, dict) else None
            if not isinstance(comparison_id, str) or not comparison_id.strip():
                execution_issues.append(f"{location}: selection comparison requires a nonempty comparison_id")
            else:
                comparison_ids.add(comparison_id)
            if not positive_int(order):
                execution_issues.append(f"{location}: execution_order must be a positive integer")
            else:
                execution_orders[order].append(location)
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            row = dict(row)
            row.setdefault("settings", {})
            key = job_key(row)
        except (ValueError, TypeError) as exc:
            problems.append(f"{location}: unassigned invalid row: {exc}; recorded error: {row.get('error', '') if isinstance(row, dict) else ''}")
            if isinstance(row, dict) and isinstance(row.get("run_id"), str) and row["run_id"]:
                seen_ids[row["run_id"]].append((None, location))
            continue
        group = groups.setdefault(key, {"key": key, "rows": [], "issues": [], "failed_rows": 0, "stats": None})
        group["rows"].append(row)
        issues = row_issues(row, key, size_only, selection_mode)
        if issues:
            group["failed_rows"] += 1
            group["issues"].extend(f"{location}, repeat {row.get('repeat')}, run {row.get('run_id')}: {issue}" for issue in issues)
        if isinstance(row.get("run_id"), str) and row["run_id"]:
            seen_ids[row["run_id"]].append((group, location))
    for run_id, occurrences in seen_ids.items():
        if len(occurrences) > 1:
            for group, location in occurrences:
                destination = group["issues"] if group is not None else problems
                destination.append(f"{location}: duplicate run_id {run_id}")
    if expected is not None:
        for key in expected:
            if key not in groups:
                groups[key] = {"key": key, "rows": [], "issues": ["entire expected job is missing"], "failed_rows": 0, "stats": None}
        for key, group in groups.items():
            if key not in expected:
                group["issues"].append("unexpected job not present in the supplied plan")
    if selection_mode:
        if len(comparison_ids) > 1:
            execution_issues.append("mixed comparison_id values: " + str(sorted(comparison_ids)))
        for order, locations in execution_orders.items():
            if len(locations) > 1:
                execution_issues.append(f"duplicate execution_order {order}: " + ", ".join(map(str, locations)))
        if execution_issues:
            problems.extend(execution_issues)
            for group in groups.values():
                group["issues"].append("comparison rejected: invalid selection execution metadata")
        if not any(key[1] == "ahps" for key in groups):
            problems.append("selection comparison requires AHPS jobs")
    for group in groups.values():
        counts = {row["settings"].get("requested_repeats") for row in group["rows"]
                  if positive_int(row["settings"].get("requested_repeats"))}
        group["expected_repeats"] = next(iter(counts)) if len(counts) == 1 else None
        if group["rows"] and len(counts) != 1:
            group["issues"].append("missing or mixed requested_repeats")
        repeats = [row.get("repeat") for row in group["rows"] if positive_int(row.get("repeat"))]
        if len(repeats) != len(set(repeats)):
            group["issues"].append("duplicate repetition numbers")
        if group["expected_repeats"] is not None:
            count = group["expected_repeats"]
            if len(repeats) != count or len(set(repeats)) != count or min(repeats, default=0) != 1 or max(repeats, default=0) != count:
                group["issues"].append(f"incomplete/invalid repetitions: got {sorted(repeats)}, expected 1..{group['expected_repeats']}")
        sizes = {row["compressed_bytes"] for row in group["rows"]
                 if positive_int(row.get("compressed_bytes")) and finite_positive(row.get("compressed_bytes"))}
        if len(sizes) > 1:
            group["issues"].append("nondeterministic archive sizes: " + str(sorted(sizes)))

    by_file = defaultdict(list)
    for group in groups.values():
        by_file[group["key"][0]].append(group)
    comparison_issues = {}
    provenance_sets = list(by_file.items())
    if selection_mode:
        # One comparison ID describes one implementation and settings cohort,
        # even when its bounded batches span different input files.
        provenance_sets.append((None, list(groups.values())))
    for file_id, file_groups in provenance_sets:
        # Invalid rows remain visible, but cannot short-circuit provenance checks
        # among the otherwise valid configurations.
        candidates = [row for group in file_groups for row in group["rows"]
                      if row.get("status") == "ok" and not row_issues(row, group["key"], size_only, selection_mode)]
        incompatibilities = []
        source_ids, commits, protocols, shared_artifacts = set(), set(), set(), defaultdict(set)
        by_codec = defaultdict(list)
        for row in candidates:
            try:
                source_ids.add((row["source"]["sha256"].lower(), row["source"]["bytes"]))
                commits.add(row["implementation"]["git_commit"])
                protocols.add(frozen({name: row["settings"].get(name) for name in (
                    "requested_repeats", "phase_timeout_seconds", "cache_policy", "threads")}))
                for name, identity in artifact_map(row["implementation"]).items():
                    shared_artifacts[name].add(identity)
                by_codec[row["codec"]].append(row)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue  # Invalid rows are already visible as failures above.
        if file_id is not None and len(source_ids) > 1:
            incompatibilities.append("mixed source hashes/sizes")
        if len(commits) > 1:
            incompatibilities.append("mixed implementation commits")
        if len(protocols) > 1:
            incompatibilities.append("mixed measurement settings")
        if any(len(values) > 1 for values in shared_artifacts.values()):
            incompatibilities.append("mixed implementation artifact hashes")
        for codec, codec_rows in by_codec.items():
            try:
                if len({implementation_signature(row["implementation"]) for row in codec_rows}) > 1:
                    incompatibilities.append(codec + ": mixed implementation identities")
                if len({settings_signature(row) for row in codec_rows}) > 1:
                    incompatibilities.append(codec + ": mixed settings across repetitions/configurations")
                for phase in (() if size_only else ("encode", "decode")):
                    methods = {row["phases"][phase]["peak_method"] for row in codec_rows}
                    if len(methods) > 1:
                        incompatibilities.append(codec + ": mixed peak measurement methods")
            except (KeyError, TypeError, ValueError):
                pass
        if incompatibilities:
            for group in file_groups:
                group["issues"].extend("comparison rejected: " + issue for issue in incompatibilities)
        if file_id is None:
            problems.extend("selection comparison: " + issue for issue in incompatibilities)
        else:
            comparison_issues[file_id] = incompatibilities

    for group in groups.values():
        if not group["issues"] and group["rows"]:
            group["stats"] = {"bytes": group["rows"][0]["compressed_bytes"]}
            for phase in (() if size_only else ("encode", "decode")):
                group["stats"][phase + "_seconds"] = spread([row["phases"][phase]["elapsed_seconds"] for row in group["rows"]])
                group["stats"][phase + "_peak_mib"] = spread([row["phases"][phase]["peak_working_set_bytes"] / (1 << 20)
                                                               for row in group["rows"]])
    for file_id, file_groups in by_file.items():
        baseline = groups.get((file_id, "7z-bcj2", (), LEGACY_SEARCH, "bytes"))
        if (baseline is None or baseline["stats"] is None) and not selection_mode:
            comparison_issues[file_id].append("BCJ2 baseline is missing or failed; ratios unavailable")
        for group in file_groups:
            group["ratios"] = None
            group["ablation"] = None
            group["selection"] = None
            enabled = groups.get((file_id, "ahps", ALL_ENABLED, group["key"][3], group["key"][4]))
            if group["stats"] and baseline and baseline["stats"]:
                own, rival = group["stats"], baseline["stats"]
                group["ratios"] = {"save_percent": 100 * (1 - own["bytes"] / rival["bytes"])}
                for name in (() if size_only else ("encode_seconds", "decode_seconds", "encode_peak_mib", "decode_peak_mib")):
                    group["ratios"][name] = own[name]["median"] / rival[name]["median"]
            if group["key"][1] == "ahps" and group["key"][2] != ALL_ENABLED and group["stats"] and enabled and enabled["stats"]:
                own, base = group["stats"], enabled["stats"]
                group["ablation"] = {"delta_bytes": own["bytes"] - base["bytes"]}
                if not size_only:
                    for phase in ("encode", "decode"):
                        group["ablation"][phase + "_time_saved_percent"] = 100 * (1 - own[phase + "_seconds"]["median"] / base[phase + "_seconds"]["median"])
            if selection_mode and group["key"][1] == "ahps":
                reference = groups.get((file_id, "ahps", group["key"][2], reference_key, group["key"][4]))
                if reference is None or reference["stats"] is None:
                    issue = "explicit exhaustive reference is missing or failed for " + label(group["key"])
                    if issue not in comparison_issues[file_id]:
                        comparison_issues[file_id].append(issue)
                elif group["stats"]:
                    own, base = group["stats"], reference["stats"]
                    group["selection"] = {"delta_bytes": own["bytes"] - base["bytes"],
                                          "delta_percent": 100 * (own["bytes"] / base["bytes"] - 1)}
                    if not size_only:
                        for name in ("encode_seconds", "decode_seconds", "encode_peak_mib", "decode_peak_mib"):
                            group["selection"][name] = own[name]["median"] / base[name]["median"]
                        group["selection"]["encode_time_saved_percent"] = 100 * (1 - group["selection"]["encode_seconds"])
    if not groups:
        problems.append("no benchmark groups were provided")
    complete = (expected is not None and not problems and all(group["stats"] for group in groups.values())
                and not any(comparison_issues.values()))
    return {"groups": groups, "problems": problems, "comparison_issues": comparison_issues,
            "planned": expected is not None, "matrix_complete": complete, "size_only": size_only,
            "selection_reference": reference_key,
            "comparison_id": next(iter(comparison_ids)) if len(comparison_ids) == 1 else None}


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def label(key):
    if key[1] != "ahps":
        return key[1]
    result = "ahps " + ",".join(name + "=" + str(int(value)) for name, value in zip(bench.FEATURE_NAMES, key[2]))
    if key[3] != LEGACY_SEARCH:
        result += " search=" + key[3]
    if key[4] != "bytes":
        result += " decode=" + key[4]
    return result


def metric(stats, name):
    if stats is None:
        return "—"
    values = stats[name]
    return f"{values['median']:.3f} [{values['min']:.3f}–{values['max']:.3f}]"


def render_markdown(summary):
    size_only = summary["size_only"]
    selection_mode = summary["selection_reference"] is not None
    show_bcj2 = not selection_mode or any(key[1] == "7z-bcj2" for key in summary["groups"])
    if not summary["planned"]:
        status = "Observed groups only; matrix completeness was not assessed."
    elif summary["matrix_complete"]:
        status = "Matrix complete for the supplied plan."
    else:
        status = "Matrix incomplete or invalid; no complete-matrix claim."
    lines = ["# Development benchmark summary", "", status + " Development evidence only; no research or product acceptance claim.", ""]
    if selection_mode:
        lines += ["Selection comparison: " + cell(summary["comparison_id"] or "invalid/missing identity") +
                  ". Explicit exhaustive reference: `" + summary["selection_reference"] + "`.", ""]
    if size_only:
        lines += ["Size-only view. Valid groups require hash-verified round trips; time and memory are not evaluated.", ""]
    else:
        reference = "matching file/features exhaustive median" if selection_mode else "same file's BCJ2 median"
        lines += ["Times and peaks are medians [min–max]. Ratios divide the configuration median by the " + reference +
                  "; repetitions are not paired by ordinal." + (" BCJ2 columns use the same file's BCJ2 median." if selection_mode and show_bcj2 else ""), ""]
    headers = ["File", "Configuration", "Rows / expected; failed rows", "Status", "Final bytes"]
    if show_bcj2:
        headers.append("Saved vs BCJ2")
    if not size_only:
        headers += ["Encode s", "Decode s", "Encode peak MiB", "Decode peak MiB"]
    lines += ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * 4 + ["---:"] * (len(headers) - 4)) + " |"]
    for key, group in sorted(summary["groups"].items()):
        stats, ratios = group["stats"], group["ratios"]
        values = [key[0], label(key), f"{len(group['rows'])}/{group['expected_repeats'] or '?'}; {group['failed_rows']} failed",
                  "VALID" if stats else "FAILED/MISSING", f"{stats['bytes']:,}" if stats else "—"]
        if show_bcj2:
            values.append(f"{ratios['save_percent']:+.2f}%" if ratios else "—")
        if not size_only:
            values += [metric(stats, name) for name in ("encode_seconds", "decode_seconds", "encode_peak_mib", "decode_peak_mib")]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    if not size_only and show_bcj2:
        lines += ["", "| File | Configuration | Encode time / BCJ2 | Decode time / BCJ2 | Encode peak / BCJ2 | Decode peak / BCJ2 |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for key, group in sorted(summary["groups"].items()):
            ratios = group["ratios"]
            values = [key[0], label(key)] + [f"{ratios[name]:.3f}×" if ratios else "—"
                                           for name in ("encode_seconds", "decode_seconds", "encode_peak_mib", "decode_peak_mib")]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    if selection_mode:
        lines += ["", "Selection versus explicit exhaustive with the same file and feature flags. Positive size delta means a larger archive.", ""]
        headers = ["File", "Configuration", "Δ bytes vs exhaustive", "Δ size %"]
        if not size_only:
            headers += ["Encode time / exhaustive", "Encode time saved", "Decode time / exhaustive",
                        "Encode peak / exhaustive", "Decode peak / exhaustive"]
        lines += ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * 2 + ["---:"] * (len(headers) - 2)) + " |"]
        for key, group in sorted(summary["groups"].items()):
            if key[1] != "ahps":
                continue
            measured = group["selection"]
            values = [key[0], label(key), f"{measured['delta_bytes']:+,}" if measured else "—",
                      f"{measured['delta_percent']:+.2f}%" if measured else "—"]
            if not size_only:
                values += [f"{measured['encode_seconds']:.3f}×" if measured else "—",
                           f"{measured['encode_time_saved_percent']:+.2f}%" if measured else "—"]
                values += [f"{measured[name]:.3f}×" if measured else "—"
                           for name in ("decode_seconds", "encode_peak_mib", "decode_peak_mib")]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    ablations = [(key, group["ablation"]) for key, group in sorted(summary["groups"].items()) if key[1] == "ahps" and key[2] != ALL_ENABLED]
    if ablations:
        lines += ["", "Ablations versus all-enabled AHPS on the same file and search settings; positive byte delta means a larger archive." +
                  ("" if size_only else " Positive time saved means faster."), ""]
        lines += (["| File | Configuration | Δ archive bytes |", "| --- | --- | ---: |"] if size_only else
                  ["| File | Configuration | Δ archive bytes | Encode time saved | Decode time saved |", "| --- | --- | ---: | ---: | ---: |"])
        for key, ablation in ablations:
            values = [key[0], label(key), f"{ablation['delta_bytes']:+,}" if ablation else "—"]
            if not size_only:
                values += [f"{ablation['encode_time_saved_percent']:+.2f}%" if ablation else "—",
                           f"{ablation['decode_time_saved_percent']:+.2f}%" if ablation else "—"]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    issues = list(summary["problems"])
    for key, group in sorted(summary["groups"].items()):
        issues.extend(f"{key[0]} / {label(key)}: {issue}" for issue in group["issues"])
    for file_id, reasons in summary["comparison_issues"].items():
        issues.extend(f"{file_id}: {reason}" for reason in reasons)
    if issues:
        lines += ["", "Failures and validation issues:", ""] + ["- " + cell(issue) for issue in issues]
    return "\n".join(lines) + "\n"


def load_reports(paths):
    rows, issues = [], []
    for path in paths:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            location = f"{path}:{number}"
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row must be an object")
                row["_location"] = location
                rows.append(row)
            except (ValueError, TypeError) as exc:
                issues.append(f"{location}: invalid JSONL row: {exc}")
    return rows, issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="Explicit benchmark_ahps .jsonl files; no globbing")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, help="Markdown output; defaults to stdout")
    parser.add_argument("--size-only", action="store_true", help="Validate size/round trips; permits fewer than three repetitions and omits time/memory comparisons")
    parser.add_argument("--selection-reference", help="JSON exhaustive search settings; enables AHPS selection comparisons without requiring BCJ2")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    args = parser.parse_args(argv)
    try:
        reference = bench.parse_search(args.selection_reference) if args.selection_reference is not None else None
        selection_reference_key(reference)  # Refuse an invalid reference before reading any files.
        root = bench.root_repo()
        manifest = (args.manifest or root / "corpus_manifest.json").resolve()
        corpus_root = (args.corpus_root or root).resolve()
        # Enumerate only top-level code names for output protection; never read them.
        code_paths = [path for directory in {REPO, root} for path in directory.iterdir()
                      if path.suffix.lower() in (".py", ".c", ".h", ".dll", ".exe", ".pyd")]
        protected = [*bench.protected_manifest_paths(manifest, corpus_root),
                     *bench.protected_runtime_paths(extra=code_paths)]
        inputs = [*args.reports, *([args.plan] if args.plan else [])]
        for path in inputs:
            if path.suffix.lower() != (".json" if path == args.plan else ".jsonl") or any(char in str(path) for char in "*?[]"):
                raise ValueError("Supply explicit JSONL reports and an optional JSON plan")
            bench.guard_destination(path, protected)
        if args.output:
            if args.output.suffix.lower() != ".md":
                raise ValueError("Summary output must use a .md filename")
            bench.guard_destination(args.output, [*protected, *inputs])
        rows, issues = load_reports(args.reports)
        plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
        summary = summarize(rows, plan, issues, size_only=args.size_only, selection_reference=reference)
        rendered = render_markdown(summary)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        failed = summary["problems"] or any(group["issues"] for group in summary["groups"].values()) or any(summary["comparison_issues"].values())
        return 1 if failed else 0
    except Exception as exc:
        print("Summary refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
