"""Synthetic checks for corpus harness identities in exported source trees."""

from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import benchmark_ahps as bench


class SourceCheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ahps-source-checkout-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        # The export's parent is deliberately a checkout. It must not supply
        # either the corpus root or the implementation revision for this tree.
        (self.parent / ".git").mkdir()
        self.repo = self.parent / "source-export"
        self.repo.mkdir()
        self.interpreter = self.repo / "python-test.exe"
        self.sevenz = self.repo / "7z-test.exe"
        self.source_names = (
            "ahps.py", "ahps_simd.c", "ahps_simd.dll", "ahps_csv.py",
            "ahps_safetensors.py", "ahps_rans.py", "ahps_rans.c",
        )
        for name in (*self.source_names, "ahps_progress.py", "benchmark_ahps.py",
                     "python-test.exe", "_lzma-test.pyd"):
            (self.repo / name).write_bytes(("synthetic " + name).encode())
        for patch in (
            mock.patch.object(bench, "REPO", self.repo),
            mock.patch.object(bench, "__file__", str(self.repo / "benchmark_ahps.py")),
            mock.patch.object(sys.modules["_lzma"], "__file__", str(self.repo / "_lzma-test.pyd")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_snapshot_root_does_not_invoke_git_or_adopt_ancestor_checkout(self):
        with mock.patch.object(bench.subprocess, "run", side_effect=AssertionError("Git invoked")):
            self.assertEqual(bench.root_repo(), self.repo.resolve())

    def test_checkout_root_retains_relative_common_directory(self):
        (self.repo / ".git").mkdir()
        with mock.patch.object(bench.subprocess, "run", return_value=SimpleNamespace(stdout=".git\n")) as run:
            self.assertEqual(bench.root_repo(), self.repo.resolve())
        run.assert_called_once_with(["git", "rev-parse", "--git-common-dir"], cwd=self.repo,
                                    check=True, capture_output=True, text=True)

    def test_worktree_marker_retains_common_checkout_root(self):
        (self.repo / ".git").write_text("gitdir: ../.git/worktrees/source-export\n", encoding="utf-8")
        common = self.parent / ".git"
        with mock.patch.object(bench.subprocess, "run", return_value=SimpleNamespace(stdout=str(common) + "\n")):
            self.assertEqual(bench.root_repo(), self.parent.resolve())

    def test_broken_checkout_is_not_silently_treated_as_snapshot(self):
        (self.repo / ".git").mkdir()
        with mock.patch.object(bench.subprocess, "run", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                bench.root_repo()
            with self.assertRaises(subprocess.CalledProcessError):
                bench.identities("ahps", self.interpreter, self.sevenz)

    def test_snapshot_identity_fingerprints_required_dependencies_without_git(self):
        with mock.patch.object(bench.subprocess, "run", side_effect=AssertionError("Git invoked")):
            identity = bench.identities("ahps", self.interpreter, self.sevenz)
        self.assertEqual(identity["source_kind"], "source_snapshot")
        self.assertIsNone(identity["git_commit"])
        artifacts = {Path(item["path"]).name: item for item in identity["artifacts"]}
        self.assertEqual(set(artifacts), {*self.source_names, "ahps_progress.py", "benchmark_ahps.py",
                                         "python-test.exe", "_lzma-test.pyd"})
        for name, artifact in artifacts.items():
            with self.subTest(name=name):
                self.assertEqual(artifact, bench.fingerprint(self.repo / name))

    def test_checkout_identity_records_commit(self):
        (self.repo / ".git").mkdir()
        revision = "a" * 40
        with mock.patch.object(bench.subprocess, "run", return_value=SimpleNamespace(stdout=revision + "\n")) as run:
            identity = bench.identities("ahps", self.interpreter, self.sevenz)
        self.assertEqual(identity["source_kind"], "git_checkout")
        self.assertEqual(identity["git_commit"], revision)
        run.assert_called_once_with(["git", "rev-parse", "HEAD"], cwd=self.repo,
                                    check=True, capture_output=True, text=True)

    def test_optional_rans_native_is_fingerprinted_only_when_present(self):
        for name in ("ahps_rans.dll", "libahps_rans.so", "libahps_rans.dylib"):
            with self.subTest(name=name):
                native = self.repo / name
                absent = bench.identities("ahps", self.interpreter, self.sevenz)
                self.assertNotIn(str(native.resolve()), [item["path"] for item in absent["artifacts"]])
                native.write_bytes(b"synthetic native library; never loaded")
                present = bench.identities("ahps", self.interpreter, self.sevenz)
                self.assertIn(bench.fingerprint(native), present["artifacts"])
                native.unlink()

    def test_snapshot_keeps_required_simd_dll_fingerprint(self):
        (self.repo / "ahps_simd.dll").unlink()
        with self.assertRaises(FileNotFoundError):
            bench.identities("ahps", self.interpreter, self.sevenz)

    def test_new_dependencies_are_protected_before_reads_or_writes(self):
        native = self.repo / "ahps_rans.dll"
        native.write_bytes(b"synthetic native library; never loaded")
        runtime = bench.protected_runtime_paths(self.sevenz)
        names = ("ahps_csv.py", "ahps_safetensors.py", "ahps_rans.py", "ahps_rans.c", "ahps_rans.dll")
        with mock.patch.object(Path, "open", side_effect=AssertionError("Unexpected content access")):
            for name in names:
                with self.subTest(name=name):
                    artifact = self.repo / name
                    self.assertIn(artifact.resolve(), runtime)
                    with self.assertRaisesRegex(ValueError, "Output must not overwrite"):
                        bench.guard_destination(artifact, runtime)
                    with self.assertRaisesRegex(ValueError, "Implementation artifact aliases protected data"):
                        bench.guard_implementation_inputs("ahps", self.interpreter, self.sevenz, [artifact])
                    bench.guard_implementation_inputs("xz", self.interpreter, self.sevenz, [artifact])
            # Reserve native output names even before the optional build exists.
            for name in ("libahps_rans.so", "libahps_rans.dylib"):
                with self.assertRaisesRegex(ValueError, "Output must not overwrite"):
                    bench.guard_destination(self.repo / name, runtime)


if __name__ == "__main__":
    unittest.main()
