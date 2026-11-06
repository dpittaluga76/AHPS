"""Parent-only corpus progress without running codecs or reading corpus inputs."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import ahps_progress
import benchmark_ahps as bench


class RecordingProgress:
    def __init__(self, label, enabled=None):
        self.label = label
        self.enabled = enabled
        self.updates = []
        self.lines = []
        self.closed = False
        self.failed_stage = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def update(self, fraction, stage):
        self.updates.append((fraction, stage))

    def write_line(self, text, *, file=None):
        self.lines.append(text)
        print(text, file=file or sys.stdout, flush=True)

    def mark_failed(self, stage):
        self.failed_stage = stage


class BenchmarkProgressTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "results.jsonl"
        for name in ("first", "second"):
            (self.root / name).write_bytes(b"synthetic metadata fixture")
        self.manifest.write_text(json.dumps({
            "schema_version": 1,
            "development": [{"id": name, "path": name} for name in ("first", "second")],
            "sealed": [],
        }))

    def arguments(self, *extra):
        return ["--manifest", str(self.manifest), "--corpus-root", str(self.root),
                "--files", "first", "--codecs", "xz", "--repeats", "1",
                "--output", str(self.output), *extra]

    def test_auto_enabled_and_disabled_flags_reach_renderer(self):
        for options, expected in (((), None), (("--progress",), True), (("--no-progress",), False)):
            captured = []

            def make_progress(*args, **kwargs):
                captured.append(RecordingProgress(*args, **kwargs))
                return captured[-1]

            with self.subTest(options=options), \
                    mock.patch.object(bench, "root_repo", return_value=self.root), \
                    mock.patch.object(bench, "RunLock", return_value=contextlib.nullcontext()), \
                    mock.patch.object(bench, "one_run", return_value={"status": "ok"}), \
                    mock.patch.object(ahps_progress, "TerminalProgress", side_effect=make_progress), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(bench.main(self.arguments(*options)), 0)
            self.assertEqual(len(captured), 1)
            self.assertIs(captured[0].enabled, expected)
            self.assertTrue(captured[0].closed)
            self.assertIsNone(captured[0].failed_stage)
            self.assertEqual(stdout.getvalue(), "first xz repetition 1: ok \n")
            self.assertEqual(captured[0].updates[0][0], 0)
            self.assertEqual(captured[0].updates[-1][0], 1)

    def test_completed_job_progress_preserves_rows_and_execution_order(self):
        progress = RecordingProgress("Benchmark")
        expected_rows = []

        def one_run(args, file_id, source, codec, repeat, batch_id, interpreter):
            row = {"schema_version": 1, "file_id": file_id, "codec": codec,
                   "repeat": repeat, "status": "error" if repeat == 3 else "ok"}
            expected_rows.append({**row, "comparison_id": "progress-test",
                                  "execution_order": 7 + len(expected_rows)})
            return row

        args = self.arguments("--files", "first", "second", "--codecs", "xz", "xz-bcj",
                              "--repeats", "3", "--repeat-indices", "1", "3",
                              "--comparison-id", "progress-test", "--order-start", "7")
        with mock.patch.object(bench, "root_repo", return_value=self.root), \
                mock.patch.object(bench, "RunLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(bench, "one_run", side_effect=one_run), \
                mock.patch.object(ahps_progress, "TerminalProgress", return_value=progress), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(bench.main(args), 1)
        rows = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(rows, expected_rows)
        fractions = [fraction for fraction, _ in progress.updates]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(set(fractions), {completed / 8 for completed in range(9)})
        self.assertEqual(len(progress.lines), 8)
        self.assertEqual(stdout.getvalue().splitlines(), progress.lines)
        self.assertTrue(progress.closed)
        self.assertEqual(progress.failed_stage, "Completed with errors")

    def test_flags_are_mutually_exclusive_before_any_input_access(self):
        with mock.patch.object(bench, "select_inputs") as selected, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                bench.main(self.arguments("--progress", "--no-progress"))
        self.assertEqual(error.exception.code, 2)
        selected.assert_not_called()

    def test_worker_dispatch_does_not_create_progress(self):
        with mock.patch.object(bench, "worker", return_value=17) as worker, \
                mock.patch.object(ahps_progress, "TerminalProgress") as progress:
            self.assertEqual(bench.main(["_worker", "synthetic-worker-arguments"]), 17)
        worker.assert_called_once_with(["synthetic-worker-arguments"])
        progress.assert_not_called()

    def test_progress_dependency_is_protected_and_fingerprinted(self):
        renderer = bench.REPO / "ahps_progress.py"
        self.assertIn(renderer.resolve(), bench.protected_runtime_paths())
        for codec in bench.CODECS:
            with self.subTest(codec=codec):
                self.assertIn(renderer, bench.implementation_artifact_paths(
                    codec, Path(sys.executable), bench.SEVEN_ZIP
                ))
        with mock.patch.object(bench, "root_repo", return_value=self.root), \
                mock.patch.object(bench, "one_run") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bench.main(self.arguments("--output", str(renderer))), 2)
        run.assert_not_called()

    def test_unexpected_failure_closes_progress_without_claiming_completion(self):
        progress = RecordingProgress("Benchmark")
        with mock.patch.object(bench, "root_repo", return_value=self.root), \
                mock.patch.object(bench, "RunLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(bench, "one_run", side_effect=RuntimeError("synthetic failure")), \
                mock.patch.object(ahps_progress, "TerminalProgress", return_value=progress), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(bench.main(self.arguments()), 2)
        self.assertTrue(progress.closed)
        self.assertEqual(progress.updates[-1][0], 0)
        self.assertIn("synthetic failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
