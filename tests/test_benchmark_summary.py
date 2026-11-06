"""Reporting tests use JSON evidence and synthetic files; measured source paths are never opened."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from tests.test_support import hardlink_or_skip
from unittest import mock

import summarize_benchmarks as summary


def rows(codec="ahps", sizes=80, times=(1, 100, 101), tag="", features=None):
    result = []
    for repeat, seconds in enumerate(times, 1):
        settings = {"preset": 9, "extreme_at_9": codec != "7z-bcj2", "threads": 1,
                    "requested_repeats": 3, "phase_timeout_seconds": 900, "cache_policy": "no cache flush"}
        if codec == "ahps":
            settings["block_size"] = 4096
        if features is not None:
            settings["features"] = features
        result.append({
            "schema_version": 1, "file_id": "sample", "codec": codec,
            "run_id": f"{codec}-{tag}-{repeat}", "repeat": repeat, "status": "ok", "settings": settings,
            "source": {"path": r"C:\DO_NOT_OPEN\source.bin", "sha256": "a" * 64, "bytes": 1000},
            "implementation": {"git_commit": "c" * 40, "python_version": "pinned-python",
                               "artifacts": [{"path": "/unopened/benchmark_ahps.py", "sha256": "1" * 64, "bytes": 100},
                                             {"path": f"/unopened/{codec}.bin", "sha256": "2" * 64, "bytes": 200}]},
            "roundtrip_verified": True, "restored_sha256": "a" * 64,
            "compressed_bytes": sizes, "compressed_sha256": "b" * 64,
            "phases": {phase: {"returncode": 0, "elapsed_seconds": seconds * factor,
                               "peak_working_set_bytes": repeat * (1 << 20) * factor,
                               "timing_boundary": summary.TIME_BOUNDARY, "peak_method": "pinned method"}
                       for phase, factor in (("encode", 1), ("decode", 2))},
        })
    return result


def baseline():
    return rows("7z-bcj2", sizes=100, times=(1, 2, 100))


PLAN = [{"file_id": "sample", "codec": "ahps"}, {"file_id": "sample", "codec": "7z-bcj2"}]
AHPS = summary.job_key(PLAN[0], plan=True)
EXHAUSTIVE = {"chunk_trial_preset": None, "exe32_chunk_rival": True}
CHEAP = {"chunk_trial_preset": 1, "exe32_chunk_rival": True}
CANDIDATE = {"chunk_trial_preset": 1, "exe32_chunk_rival": False}
SELECTION_PLAN = [{"file_id": "sample", "codec": "ahps", "search": search}
                  for search in (EXHAUSTIVE, CHEAP, CANDIDATE)]


def search_rows(search, *, tag, sizes=80, times=(1, 100, 101), features=None, order_start=1):
    data = rows(sizes=sizes, times=times, tag=tag, features=features)
    effective = summary.bench.parse_search(json.dumps(search))
    for index, row in enumerate(data):
        row["settings"].update(search=dict(effective), requested_repeats=len(data))
        row["report_search"] = dict(effective)
        row["phases"]["encode"]["worker"] = {"search": dict(effective), "report_search": dict(effective)}
        row.update(comparison_id="selection-test", batch_id=f"batch-{index + 1}", execution_order=order_start + index)
    return data


def selection_rows(repeats=3):
    # Each global repetition alternates the three-method execution order.
    data = []
    for method, search in enumerate((EXHAUSTIVE, CHEAP, CANDIDATE)):
        times = ((1, 100, 101), (1, 50, 100), (1, 2, 100))[method][:repeats]
        for row in search_rows(search, tag=f"method-{method}", sizes=80 + 4 * method, times=times):
            repeat = row["repeat"]
            row["execution_order"] = 3 * (repeat - 1) + (method + 1 if repeat % 2 else 3 - method)
            data.append(row)
    return data


class SummaryTests(unittest.TestCase):
    def test_medians_ranges_savings_and_ratio_of_medians(self):
        with mock.patch.object(Path, "open", side_effect=AssertionError("must not open recorded paths")):
            result = summary.summarize(rows() + baseline(), PLAN)
        group = result["groups"][AHPS]
        self.assertTrue(result["matrix_complete"])
        self.assertEqual(group["stats"]["bytes"], 80)
        self.assertEqual(group["stats"]["encode_seconds"], {"median": 100, "min": 1, "max": 101})
        self.assertAlmostEqual(group["ratios"]["save_percent"], 20)
        self.assertEqual(group["ratios"]["encode_seconds"], 50)  # Not median([1, 50, 1.01]).
        self.assertEqual(group["stats"]["decode_peak_mib"]["median"], 4)
        text = summary.render_markdown(result)
        self.assertIn("+20.00%", text)
        self.assertIn("50.000×", text)
        negative = summary.summarize(rows(sizes=120) + baseline())["groups"][AHPS]
        self.assertAlmostEqual(negative["ratios"]["save_percent"], -20)

    def test_legacy_and_explicit_enabled_flags_are_one_group(self):
        explicit = rows(tag="explicit", features={name: True for name in summary.bench.FEATURE_NAMES})
        result = summary.summarize(rows() + explicit + baseline())
        group = result["groups"][AHPS]
        self.assertEqual(len(group["rows"]), 6)
        self.assertIsNone(group["stats"])
        self.assertIn("duplicate repetition numbers", group["issues"])

    def test_duplicates_missing_repetitions_and_bad_requested_counts_fail(self):
        for kind in ("run_id", "repeat", "missing", "outside", "bool", "zero", "string", "mixed"):
            data = rows()
            if kind == "run_id":
                data[1]["run_id"] = data[0]["run_id"]
            elif kind == "repeat":
                data[1]["repeat"] = 1
            elif kind == "missing":
                data.pop()
            elif kind == "outside":
                data[-1]["repeat"] = 4
            else:
                data[0]["settings"]["requested_repeats"] = {"bool": True, "zero": 0, "string": "3", "mixed": 2}[kind]
            with self.subTest(kind=kind):
                result = summary.summarize(data + baseline(), PLAN)
                self.assertFalse(result["matrix_complete"])
                self.assertIsNone(result["groups"][AHPS]["stats"])

    def test_global_duplicate_run_id_fails_both_groups(self):
        data, rival = rows(), baseline()
        rival[0]["run_id"] = data[0]["run_id"]
        result = summary.summarize(data + rival)
        self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))
        self.assertIn("duplicate run_id", summary.render_markdown(result))

    def test_invalid_job_cannot_hide_global_duplicate_run_id(self):
        data = rows()
        invalid = dict(data[0], codec="unknown", error="unsupported job")
        result = summary.summarize(data + [invalid] + baseline())
        self.assertIsNone(result["groups"][AHPS]["stats"])
        self.assertIn("unsupported job", summary.render_markdown(result))

    def test_failed_repetition_is_preserved_without_success_only_medians(self):
        data = rows()
        data[1].update(status="error", error="encoding timeout", _location="fixture.jsonl:2")
        result = summary.summarize(data + baseline(), PLAN)
        group = result["groups"][AHPS]
        self.assertEqual(len(group["rows"]), 3)
        self.assertEqual(group["failed_rows"], 1)
        self.assertIsNone(group["stats"])
        self.assertIsNone(group["ratios"])
        text = summary.render_markdown(result)
        self.assertIn("fixture.jsonl:2", text)
        self.assertIn("encoding timeout", text)
        self.assertIn("3/3; 1 failed", text)

    def test_mixed_source_commit_artifact_or_settings_reject_comparison(self):
        for kind in ("source", "commit", "artifact", "settings"):
            data = rows()
            if kind == "source":
                data[0]["source"]["sha256"] = data[0]["restored_sha256"] = "d" * 64
            elif kind == "commit":
                data[0]["implementation"]["git_commit"] = "d" * 40
            elif kind == "artifact":
                data[0]["implementation"]["artifacts"][1]["sha256"] = "d" * 64
            else:
                data[0]["settings"]["preset"] = 6
            with self.subTest(kind=kind):
                result = summary.summarize(data + baseline(), PLAN)
                self.assertFalse(result["matrix_complete"])
                self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))
                self.assertIn("comparison rejected", summary.render_markdown(result))

    def test_nondeterministic_sizes_and_invalid_success_evidence_fail(self):
        for kind in ("size", "nan", "infinity", "peak_bool", "roundtrip", "reported_features", "source_type", "phase_type", "huge_time", "huge_peak", "huge_archive"):
            data = rows()
            if kind == "size":
                data[1]["compressed_bytes"] += 1
            elif kind in ("nan", "infinity"):
                data[0]["phases"]["encode"]["elapsed_seconds"] = float("nan" if kind == "nan" else "inf")
            elif kind == "peak_bool":
                data[0]["phases"]["decode"]["peak_working_set_bytes"] = True
            elif kind == "roundtrip":
                data[0]["roundtrip_verified"] = False
            elif kind == "reported_features":
                data[0]["report_features"] = {"exe32": False}
            elif kind == "source_type":
                data[0]["source"] = None
            elif kind == "huge_time":
                data[0]["phases"]["encode"]["elapsed_seconds"] = 10 ** 1000
            elif kind == "huge_peak":
                data[0]["phases"]["encode"]["peak_working_set_bytes"] = 10 ** 1000
            elif kind == "huge_archive":
                data[0]["compressed_bytes"] = 10 ** 1000
            else:
                data[0]["phases"] = None
            with self.subTest(kind=kind):
                result = summary.summarize(data + baseline())
                self.assertIsNone(result["groups"][AHPS]["stats"])

    def test_malformed_configuration_cannot_mask_mixed_valid_settings(self):
        alternate = rows(tag="alternate", features={"exe_split": False})
        invalid = rows(tag="invalid", features={"delta_split": False})
        for row in alternate:
            row["settings"]["preset"] = 6
        invalid[0]["settings"]["extra"] = float("nan")
        result = summary.summarize(rows() + alternate + invalid + baseline())
        self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))
        self.assertIn("mixed settings", summary.render_markdown(result))

    def test_plan_detects_entire_missing_jobs_and_no_plan_never_claims_complete(self):
        result = summary.summarize(rows() + baseline())
        self.assertFalse(result["matrix_complete"])
        self.assertIn("Observed groups only", summary.render_markdown(result))
        missing = {"file_id": "sample", "codec": "ahps", "features": {"per_chunk": False}}
        result = summary.summarize(rows() + baseline(), PLAN + [missing])
        key = summary.job_key(missing, plan=True)
        self.assertEqual(result["groups"][key]["rows"], [])
        self.assertIn("entire expected job is missing", result["groups"][key]["issues"])
        self.assertFalse(result["matrix_complete"])
        for invalid in ([], PLAN + [PLAN[0]], [{"file_id": "sample", "codec": "ahps", "features": {"exe32": 0}}]):
            with self.subTest(plan=invalid), self.assertRaises(ValueError):
                summary.summarize(rows(), invalid)

    def test_ablation_bytes_and_time_contributions(self):
        enabled = rows(times=(100, 100, 100))
        ablated = rows(sizes=88, times=(50, 50, 50), tag="ablation", features={"exe_split": False})
        result = summary.summarize(enabled + ablated + baseline())
        key = summary.job_key(ablated[0])
        self.assertEqual(result["groups"][key]["ablation"],
                         {"delta_bytes": 8, "encode_time_saved_percent": 50, "decode_time_saved_percent": 50})

    def test_size_only_accepts_one_repeat_without_metrics_and_shows_missing_jobs(self):
        data = [rows()[0], baseline()[0]]
        for row in data:
            row["settings"]["requested_repeats"] = 1
            row["phases"] = {phase: {"returncode": 0} for phase in ("encode", "decode")}
        result = summary.summarize(data, PLAN, size_only=True)
        self.assertTrue(result["matrix_complete"])
        self.assertEqual(result["groups"][AHPS]["stats"], {"bytes": 80})
        text = summary.render_markdown(result)
        self.assertIn("1/1; 0 failed", text)
        self.assertIn("+20.00%", text)
        for excluded in ("Encode s", "Decode s", "MiB", "Encode time /", "Decode time /", "medians"):
            self.assertNotIn(excluded, text)
        missing = {"file_id": "another-file", "codec": "ahps"}
        incomplete = summary.summarize(data, PLAN + [missing], size_only=True)
        self.assertFalse(incomplete["matrix_complete"])
        self.assertIn("entire expected job is missing", summary.render_markdown(incomplete))
        full_metrics = summary.summarize(data, PLAN)
        self.assertFalse(full_metrics["matrix_complete"])
        self.assertIsNone(full_metrics["groups"][AHPS]["stats"])
        self.assertIn("require at least three repetitions", summary.render_markdown(full_metrics))


class SummaryFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="benchmark-summary-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "runs.jsonl"
        self.report.write_text("\n".join(json.dumps(row) for row in rows() + baseline()) + "\n", encoding="utf-8")
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(PLAN), encoding="utf-8")
        self.held = self.root / "custom-held.bin"
        self.held.write_bytes(b"synthetic protected fixture")
        self.code = self.root / "test_local_code.py"
        self.code.write_text("# synthetic implementation", encoding="utf-8")
        self.runtime = self.root / "runtime-fixture.bin"
        self.runtime.write_bytes(b"synthetic runtime fixture")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "development": [],
                                            "sealed": [{"id": "held", "path": str(self.held)}]}), encoding="utf-8")

    def argv(self, *extra):
        return [str(self.report), "--plan", str(self.plan), "--manifest", str(self.manifest),
                "--corpus-root", str(self.root), *extra]

    def test_output_aliases_are_rejected_before_report_reads_or_writes(self):
        for index, protected in enumerate((self.report, self.plan, self.manifest, self.held, self.code, self.runtime)):
            output = self.root / f"alias-{index}.md"
            hardlink_or_skip(protected, output)
            original = protected.read_bytes()
            with self.subTest(path=protected), mock.patch.object(summary, "REPO", self.root), \
                    mock.patch.object(summary.bench, "root_repo", return_value=self.root), \
                    mock.patch.object(summary.bench, "protected_runtime_paths", side_effect=lambda **kwargs: [self.runtime, *kwargs["extra"]]), \
                    mock.patch.object(summary, "load_reports") as load, mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(summary.main(self.argv("--output", str(output))), 2)
                load.assert_not_called()
            self.assertEqual(protected.read_bytes(), original)

    def test_only_explicit_reports_plan_and_manifest_are_opened(self):
        permitted = {self.report.resolve(), self.plan.resolve(), self.manifest.resolve()}
        real_open = Path.open

        def checked_open(path, *args, **kwargs):
            self.assertIn(path.resolve(), permitted)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(summary, "REPO", self.root), mock.patch.object(summary.bench, "root_repo", return_value=self.root), \
                mock.patch.object(Path, "open", checked_open), mock.patch("sys.stdout", io.StringIO()) as stdout:
            self.assertEqual(summary.main(self.argv()), 0)
        self.assertIn("Matrix complete for the supplied plan", stdout.getvalue())

    def test_bad_jsonl_rows_remain_visible(self):
        self.report.write_text(self.report.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
        data, errors = summary.load_reports([self.report])
        result = summary.summarize(data, PLAN, errors)
        self.assertFalse(result["matrix_complete"])
        self.assertIn("invalid JSONL row", summary.render_markdown(result))

    def test_size_only_cli_keeps_deadline_missing_jobs_and_returns_failure(self):
        self.plan.write_text(json.dumps(PLAN + [{"file_id": "not-started", "codec": "ahps"}]), encoding="utf-8")
        with mock.patch.object(summary.bench, "root_repo", return_value=self.root), mock.patch("sys.stdout", io.StringIO()) as stdout:
            self.assertEqual(summary.main(self.argv("--size-only")), 1)
        self.assertIn("not-started", stdout.getvalue())
        self.assertIn("entire expected job is missing", stdout.getvalue())
        self.assertNotIn("Encode time /", stdout.getvalue())

    def test_decode_api_is_part_of_a_jobs_identity(self):
        file_job = {"file_id": "sample", "codec": "ahps", "decode_api": "file"}
        file_key = summary.job_key(file_job, plan=True)
        self.assertNotEqual(file_key, AHPS)
        self.assertEqual(file_key, summary.job_key({"file_id": "sample", "codec": "ahps",
                                                    "settings": {"decode_api": "file"}}))
        self.assertIn("decode=file", summary.label(file_key))
        self.assertNotIn("decode=", summary.label(AHPS))
        for invalid in ({"file_id": "sample", "codec": "ahps", "decode_api": "stream"},
                        {"file_id": "sample", "codec": "7z-bcj2", "decode_api": "file"}):
            with self.subTest(job=invalid), self.assertRaises(ValueError):
                summary.job_key(invalid, plan=True)

    def test_selection_cli_and_early_reference_validation(self):
        self.report.write_text("\n".join(json.dumps(row) for row in selection_rows(1)), encoding="utf-8")
        self.plan.write_text(json.dumps(SELECTION_PLAN), encoding="utf-8")
        with mock.patch.object(summary.bench, "root_repo", return_value=self.root), mock.patch("sys.stdout", io.StringIO()) as stdout:
            self.assertEqual(summary.main(self.argv("--size-only", "--selection-reference", json.dumps(EXHAUSTIVE))), 0)
        self.assertIn("Δ bytes vs exhaustive", stdout.getvalue())
        self.assertNotIn("Saved vs BCJ2", stdout.getvalue())
        with mock.patch.object(summary, "load_reports") as load, mock.patch.object(summary.bench, "root_repo") as root, \
                mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(summary.main(self.argv("--selection-reference", json.dumps(CANDIDATE))), 2)
            load.assert_not_called()
            root.assert_not_called()


class SelectionSummaryTests(unittest.TestCase):
    def summarize(self, data, plan=SELECTION_PLAN, *, size_only=False):
        return summary.summarize(data, plan, size_only=size_only, selection_reference=EXHAUSTIVE)

    def test_three_size_only_methods_without_bcj2(self):
        data = selection_rows(1)
        result = self.summarize(data, size_only=True)
        self.assertTrue(result["matrix_complete"])
        self.assertEqual(len(result["groups"]), 3)
        candidate = result["groups"][summary.job_key(SELECTION_PLAN[2], plan=True)]
        self.assertEqual(candidate["selection"]["delta_bytes"], 8)
        self.assertAlmostEqual(candidate["selection"]["delta_percent"], 10)
        text = summary.render_markdown(result)
        self.assertIn("+10.00%", text)
        self.assertNotIn("BCJ2", text)
        self.assertNotIn("Encode time /", text)
        self.assertNotIn("MiB", text)
        default = summary.summarize(data, SELECTION_PLAN, size_only=True)
        self.assertFalse(default["matrix_complete"])
        self.assertIn("BCJ2 baseline is missing", summary.render_markdown(default))

    def test_global_repetitions_across_batches_and_ratio_of_medians(self):
        result = self.summarize(selection_rows())
        self.assertTrue(result["matrix_complete"])
        candidate = result["groups"][summary.job_key(SELECTION_PLAN[2], plan=True)]["selection"]
        self.assertAlmostEqual(candidate["encode_seconds"], 0.02)
        self.assertAlmostEqual(candidate["encode_time_saved_percent"], 98)
        self.assertAlmostEqual(candidate["decode_seconds"], 0.02)
        self.assertEqual(candidate["encode_peak_mib"], 1)
        self.assertEqual(candidate["decode_peak_mib"], 1)
        text = summary.render_markdown(result)
        self.assertIn("0.020×", text)
        self.assertIn("+98.00%", text)
        self.assertIn("Decode peak / exhaustive", text)

    def test_legacy_default_remains_distinct_from_explicit_exhaustive(self):
        explicit = search_rows(EXHAUSTIVE, tag="explicit")
        result = summary.summarize(rows() + explicit + baseline())
        explicit_key = summary.job_key(explicit[0])
        self.assertNotEqual(AHPS, explicit_key)
        self.assertEqual(len(result["groups"]), 3)
        self.assertIsNotNone(result["groups"][AHPS]["stats"])
        self.assertIsNotNone(result["groups"][explicit_key]["stats"])
        self.assertNotIn("search=", summary.label(AHPS))
        legacy = rows()[0]
        legacy.update(comparison_id="selection-test", execution_order=20)
        legacy["settings"]["requested_repeats"] = 1
        selected = self.summarize(selection_rows(1) + [legacy], plan=None, size_only=True)
        self.assertIsNone(selected["groups"][AHPS]["stats"])
        self.assertIn("legacy/default is not exhaustive evidence", summary.render_markdown(selected))

    def test_all_search_confirmations_are_required_and_must_agree(self):
        for field in ("top", "worker_report", "worker_request"):
            for fault in ("missing", "mismatch", "partial"):
                data = selection_rows(1)
                container = data[0] if field == "top" else data[0]["phases"]["encode"]["worker"]
                name = "search" if field == "worker_request" else "report_search"
                if fault == "missing":
                    container.pop(name)
                else:
                    container[name] = dict(CANDIDATE) if fault == "mismatch" else {}
                with self.subTest(field=field, fault=fault):
                    result = self.summarize(data, size_only=True)
                    reference = result["groups"][summary.job_key(SELECTION_PLAN[0], plan=True)]
                    self.assertIsNone(reference["stats"])
                    self.assertFalse(result["matrix_complete"])
                    self.assertTrue(all(group["selection"] is None for group in result["groups"].values()))

    def test_invalid_search_types_and_nonexhaustive_references_are_rejected(self):
        for search in ({"chunk_trial_preset": True}, {"chunk_trial_preset": 1.0}, {"chunk_trial_preset": 10},
                       {"chunk_trial_preset": -1}, {"exe32_chunk_rival": 1}, {"unknown": True}, None):
            with self.subTest(search=search), self.assertRaises(ValueError):
                summary.job_key({"file_id": "sample", "codec": "ahps", "search": search}, plan=True)
        with self.assertRaises(ValueError):
            summary.summarize(selection_rows(), SELECTION_PLAN, selection_reference=CHEAP)
        with self.assertRaises(ValueError):
            self.summarize(selection_rows(), PLAN)

    def test_comparison_identity_and_order_validate_even_failed_or_invalid_rows(self):
        for fault in ("missing_id", "empty_id", "mixed_id", "duplicate_order", "zero_order", "bool_order", "float_order",
                      "failed_mixed_id", "invalid_duplicate_order"):
            data = selection_rows(1)
            if fault == "missing_id":
                data[1].pop("comparison_id")
            elif fault == "empty_id":
                data[1]["comparison_id"] = " "
            elif fault in ("mixed_id", "failed_mixed_id"):
                data[1]["comparison_id"] = "other-comparison"
                if fault == "failed_mixed_id":
                    data[1].update(status="error", error="deadline reached")
            elif fault in ("duplicate_order", "invalid_duplicate_order"):
                data[1]["execution_order"] = data[0]["execution_order"]
                if fault == "invalid_duplicate_order":
                    data[1]["codec"] = "invalid"
            else:
                data[1]["execution_order"] = {"zero_order": 0, "bool_order": True, "float_order": 1.0}[fault]
            with self.subTest(fault=fault):
                result = self.summarize(data, size_only=True)
                self.assertFalse(result["matrix_complete"])
                self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))
                self.assertTrue(result["problems"])
        data = selection_rows(1)
        for row in data:
            row["execution_order"] *= 7  # Global order can legitimately have gaps.
        self.assertTrue(self.summarize(data, size_only=True)["matrix_complete"])

    def test_reset_duplicate_and_missing_repetitions_stay_failures(self):
        for fault in ("reset", "missing", "failed", "duplicate_run_id"):
            data = selection_rows()
            if fault == "reset":
                data[1]["repeat"] = 1
            elif fault == "missing":
                data.pop(1)
            elif fault == "failed":
                data[1].update(status="error", error="deadline reached")
            else:
                data[1]["run_id"] = data[0]["run_id"]
            with self.subTest(fault=fault):
                result = self.summarize(data)
                self.assertFalse(result["matrix_complete"])
                self.assertIsNone(result["groups"][summary.job_key(SELECTION_PLAN[0], plan=True)]["stats"])
                self.assertTrue(all(group["selection"] is None for group in result["groups"].values()))

    def test_source_commit_artifact_and_other_settings_still_match_across_search_methods(self):
        for fault in ("preset", "block_size", "source", "commit", "artifact"):
            data = selection_rows(1)
            candidate = data[2]
            if fault in ("preset", "block_size"):
                candidate["settings"][fault] += 1
            elif fault == "source":
                candidate["source"]["sha256"] = candidate["restored_sha256"] = "f" * 64
            elif fault == "commit":
                candidate["implementation"]["git_commit"] = "f" * 40
            else:
                candidate["implementation"]["artifacts"][1]["sha256"] = "f" * 64
            with self.subTest(fault=fault):
                result = self.summarize(data, size_only=True)
                self.assertFalse(result["matrix_complete"])
                self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))

    def test_single_comparison_cannot_mix_provenance_between_files(self):
        for fault in (None, "commit", "artifact", "preset"):
            first, second = selection_rows(1), selection_rows(1)
            for row in second:
                row.update(file_id="second", run_id="second-" + row["run_id"], execution_order=row["execution_order"] + 10)
                row["source"]["sha256"] = row["restored_sha256"] = "d" * 64
                if fault == "commit":
                    row["implementation"]["git_commit"] = "f" * 40
                elif fault == "artifact":
                    row["implementation"]["artifacts"][1]["sha256"] = "f" * 64
                elif fault == "preset":
                    row["settings"]["preset"] = 6
            plan = SELECTION_PLAN + [dict(job, file_id="second") for job in SELECTION_PLAN]
            with self.subTest(fault=fault):
                result = self.summarize(first + second, plan, size_only=True)
                self.assertEqual(result["matrix_complete"], fault is None)
                if fault is not None:
                    self.assertTrue(all(group["stats"] is None for group in result["groups"].values()))
                    self.assertIn("selection comparison: mixed" if fault != "preset" else "mixed settings", summary.render_markdown(result))

    def test_reference_matches_features_and_ablation_matches_search(self):
        data = selection_rows()
        features = {"per_chunk": False}
        candidate = search_rows(CANDIDATE, tag="candidate-disabled", sizes=96, features=features, order_start=20)
        extra = {"file_id": "sample", "codec": "ahps", "features": features, "search": CANDIDATE}
        result = self.summarize(data + candidate, SELECTION_PLAN + [extra])
        group = result["groups"][summary.job_key(extra, plan=True)]
        self.assertEqual(group["ablation"]["delta_bytes"], 8)  # 96 - candidate's 88, not exhaustive's 80.
        self.assertIsNone(group["selection"])
        self.assertFalse(result["matrix_complete"])
        reference = search_rows(EXHAUSTIVE, tag="exhaustive-disabled", sizes=92, features=features, order_start=30)
        reference_plan = dict(extra, search=EXHAUSTIVE)
        complete = self.summarize(data + candidate + reference, SELECTION_PLAN + [extra, reference_plan])
        self.assertTrue(complete["matrix_complete"])
        self.assertEqual(complete["groups"][summary.job_key(extra, plan=True)]["selection"]["delta_bytes"], 4)

    def test_optional_bcj2_ratios_and_planned_missing_jobs_remain_visible(self):
        data = selection_rows(1)
        bcj2 = baseline()[0]
        bcj2["settings"]["requested_repeats"] = 1
        bcj2.update(comparison_id="selection-test", execution_order=50)
        plan = SELECTION_PLAN + [PLAN[1]]
        result = self.summarize(data + [bcj2], plan, size_only=True)
        self.assertTrue(result["matrix_complete"])
        candidate = result["groups"][summary.job_key(SELECTION_PLAN[2], plan=True)]
        self.assertAlmostEqual(candidate["ratios"]["save_percent"], 12)
        self.assertIn("Saved vs BCJ2", summary.render_markdown(result))
        missing = self.summarize(data, plan, size_only=True)
        self.assertFalse(missing["matrix_complete"])
        self.assertIn("entire expected job is missing", summary.render_markdown(missing))
        bcj2["comparison_id"] = "other-comparison"
        self.assertFalse(self.summarize(data + [bcj2], plan, size_only=True)["matrix_complete"])


if __name__ == "__main__":
    unittest.main()
