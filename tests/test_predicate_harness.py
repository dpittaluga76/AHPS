"""Synthetic predicate-harness checks. No development/holdout corpus is read."""

import ctypes
import io
import json
import lzma
import os
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import benchmark_predicates as pred
from tests.test_support import hardlink_or_skip


class XorKernel:
    """Deterministic reversible stand-in: tests framing without native compilation."""
    abi = 206

    def __init__(self, *_args):
        self.calls = []

    def lift(self, data, transform, inverse=False):
        self.calls.append((transform, inverse))
        return bytes(value ^ 0x5A for value in data)


class PredicateHarnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="predicate-harness-test-")
        self.addCleanup(temporary.cleanup)
        # Guards return canonical paths; temp roots can use OS-specific aliases.
        self.root = Path(temporary.name).resolve()
        self.manifest = self.root / "manifest.json"
        self.source = self.root / "synthetic.bin"
        self.source.write_bytes(b"\x89\xe8\0\0\0\0\xe9\xff\xff\xff\xff" * 10)
        self.manifest.write_text(json.dumps({"schema_version": 1,
            "development": [{"id": "sample", "path": "synthetic.bin"}],
            "sealed": [{"id": "held", "path": "held.bin"}]}), encoding="utf-8")
        self.dll = self.root / "ahps_simd.dll"
        self.dll.write_bytes(b"synthetic production DLL stand-in")
        self.experiment = self.root / "experiments" / "unit-test" / pred.EXPERIMENT_DLL
        self.experiment.parent.mkdir(parents=True)
        self.experiment.write_bytes(b"synthetic experimental DLL stand-in")
        self.patch_root = mock.patch.object(pred, "REPO", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def worker_args(self, phase, source, destination):
        return [phase, "baseline", "exe32", str(source), str(destination),
                "--dll", str(self.dll), "--dll-sha256", pred.bench.sha256(self.dll),
                "--manifest", str(self.manifest), "--corpus-root", str(self.root),
                "--file-id", "sample"]

    def test_experiment_location_and_production_alias_guards(self):
        self.assertEqual(pred.validate_experiment_path(self.experiment), self.experiment)
        for bad in (self.dll, self.root / pred.EXPERIMENT_DLL,
                    self.root / "experiments" / pred.EXPERIMENT_DLL,
                    self.root / "experiments" / "unit-test" / "ahps_simd.dll"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                pred.validate_experiment_path(bad)
        (self.experiment.parent / "ahps.py").write_text("# no co-located loader", encoding="utf-8")
        with self.assertRaises(ValueError):
            pred.validate_experiment_path(self.experiment)

    def test_cpu_gate_precedes_dynamic_library_loading(self):
        with mock.patch.object(pred, "cpu_supports_native", return_value=False), \
                mock.patch.object(pred.ctypes, "CDLL") as load:
            with self.assertRaisesRegex(RuntimeError, "AVX2"):
                pred.Kernel(self.dll, "baseline")
            load.assert_not_called()

    def test_wrong_abi_never_calls_transform_functions(self):
        for path, variant, wrong in ((self.dll, "baseline", 9206), (self.experiment, "no-modrm", 206)):
            library = SimpleNamespace(ahps_abi_version=mock.Mock(return_value=wrong),
                                      ahps_forward_lift=mock.Mock(), ahps_inverse_lift=mock.Mock())
            with self.subTest(variant=variant), mock.patch.object(pred, "cpu_supports_native", return_value=True), \
                    mock.patch.object(pred.ctypes, "CDLL", return_value=library):
                with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
                    pred.Kernel(path, variant)
                library.ahps_forward_lift.assert_not_called()
                library.ahps_inverse_lift.assert_not_called()

    def test_signatures_and_matching_inverse_are_used(self):
        def invert(_opcode, _params, data, output, length):
            ctypes.memmove(output, bytes(value ^ 0x5A for value in data[:length]), length)
        library = SimpleNamespace(ahps_abi_version=mock.Mock(return_value=206),
            ahps_forward_lift=mock.Mock(side_effect=invert), ahps_inverse_lift=mock.Mock(side_effect=invert))
        with mock.patch.object(pred, "cpu_supports_native", return_value=True), \
                mock.patch.object(pred.ctypes, "CDLL", return_value=library):
            kernel = pred.Kernel(self.dll, "baseline")
        original = b"\xe8\0\0\0\0\x90\xe9\xff\xff\xff\xff"
        residual = kernel.lift(original, "exe_split")
        self.assertEqual(kernel.lift(residual, "exe_split", inverse=True), original)
        self.assertEqual(library.ahps_forward_lift.argtypes[-1], ctypes.c_int)
        self.assertEqual(library.ahps_inverse_lift.argtypes, library.ahps_forward_lift.argtypes)
        self.assertEqual(library.ahps_forward_lift.call_count, 1)
        self.assertEqual(library.ahps_inverse_lift.call_count, 1)

    def test_int_max_rejected_before_allocation_or_c_call(self):
        kernel = pred.Kernel.__new__(pred.Kernel)
        kernel.lib = SimpleNamespace(ahps_forward_lift=mock.Mock(), ahps_inverse_lift=mock.Mock())
        oversized = mock.MagicMock()
        oversized.__len__.return_value = pred.INT_MAX + 1
        with mock.patch.object(pred.ctypes, "create_string_buffer") as allocate:
            with self.assertRaisesRegex(ValueError, "INT_MAX"):
                kernel.lift(oversized, "exe32")
            allocate.assert_not_called()
        kernel.lib.ahps_forward_lift.assert_not_called()

    def test_residual_is_plain_xz_and_roundtrip_uses_inverse(self):
        original = self.source.read_bytes()
        kernel = XorKernel()
        encoded = pred.encode_residual(kernel, original, "exe32")
        self.assertEqual(encoded[:6], b"\xfd7zXZ\x00")
        self.assertEqual(lzma.decompress(encoded), bytes(value ^ 0x5A for value in original))
        self.assertEqual(pred.decode_residual(kernel, encoded, "exe32", len(original)), original)
        self.assertEqual(kernel.calls, [("exe32", False), ("exe32", True)])

    def test_decode_length_and_trailing_stream_errors(self):
        kernel = XorKernel()
        encoded = pred.encode_residual(kernel, b"abc", "exe32")
        for damaged, length in ((encoded, 2), (encoded, 4), (encoded + b"trailing", 3)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                pred.decode_residual(kernel, damaged, "exe32", length)

    def test_worker_guards_before_binary_reads_or_kernel_load(self):
        protected = pred.protected_paths(self.manifest, self.root, self.experiment)
        for destination in (self.source, self.manifest, self.dll, self.experiment,
                            self.experiment.with_name("build.json"), self.root / "output.ahps"):
            args = self.worker_args("encode", self.source, destination)
            with self.subTest(destination=destination), mock.patch.object(Path, "read_bytes") as read, \
                    mock.patch.object(pred, "Kernel") as kernel, mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(pred.worker(args), 1)
                read.assert_not_called()
                kernel.assert_not_called()
        self.assertIn(self.experiment, protected)

    def test_worker_rejects_held_input_before_read(self):
        args = self.worker_args("decode", self.root / "held.bin", self.root / "restored.bin")
        with mock.patch.object(Path, "read_bytes") as read, mock.patch.object(pred, "Kernel") as kernel, \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(pred.worker(args), 1)
            read.assert_not_called()
            kernel.assert_not_called()

    def test_worker_never_uses_an_ahps_archive_writer(self):
        encoded = self.root / "residual.xz"
        restored = self.root / "restored.bin"
        forbidden_ahps = mock.Mock()
        forbidden_ahps.ahps_compress_bytes.side_effect = AssertionError("archive writer must not be called")
        forbidden_ahps.ahps_decompress_bytes.side_effect = AssertionError("archive decoder must not be called")
        with mock.patch.dict("sys.modules", {"ahps": forbidden_ahps}), \
                mock.patch.object(pred, "Kernel", XorKernel), \
                mock.patch.object(pred.bench, "process_peak", return_value=1024), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(pred.worker(self.worker_args("encode", self.source, encoded)), 0)
            self.assertEqual(pred.worker(self.worker_args("decode", encoded, restored)), 0)
        self.assertEqual(restored.read_bytes(), self.source.read_bytes())
        forbidden_ahps.ahps_compress_bytes.assert_not_called()
        forbidden_ahps.ahps_decompress_bytes.assert_not_called()

    def test_build_metadata_rejects_wrong_abi_flags_and_artifacts(self):
        (self.root / "ahps_simd.c").write_text("/* synthetic source */", encoding="utf-8")
        valid = {"schema_version": 1, "status": "ok", "abi": 9206, "flags": list(pred.FLAGS),
                 "library": pred.bench.fingerprint(self.experiment),
                 "source": pred.bench.fingerprint(self.root / "ahps_simd.c"),
                 "compiler": {"path": str(self.root / "pinned-gcc.exe"), "sha256": "a" * 64}}
        path = self.experiment.with_name("build.json")
        path.write_text(json.dumps(valid), encoding="utf-8")
        self.assertEqual(pred.validate_build_metadata(self.experiment), valid)
        for changes in ({"abi": 206}, {"flags": []}, {"library": {}}, {"source": {}}, {"compiler": {}}):
            path.write_text(json.dumps({**valid, **changes}), encoding="utf-8")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pred.validate_build_metadata(self.experiment)

    def test_report_cannot_overwrite_build_compiler_or_hardlink(self):
        compiler = self.root / "pinned-gcc.exe"
        compiler.write_bytes(b"synthetic compiler")
        alias = self.root / "compiler-alias.jsonl"
        hardlink_or_skip(compiler, alias)
        metadata = {"schema_version": 1, "status": "ok", "abi": 9206, "flags": list(pred.FLAGS),
                    "compiler": {"path": str(compiler), "sha256": "a" * 64}}
        self.experiment.with_name("build.json").write_text(json.dumps(metadata), encoding="utf-8")
        for output in (compiler, alias):
            arguments = ["run", "--files", "sample", "--manifest", str(self.manifest),
                         "--corpus-root", str(self.root), "--experiment-dll", str(self.experiment),
                         "--output", str(output)]
            with self.subTest(output=output), mock.patch.object(pred.bench, "root_repo", return_value=self.root), \
                    mock.patch.object(pred.bench, "fingerprint") as fingerprint, \
                    mock.patch.object(pred.bench, "RunLock") as lock, mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(pred.main(arguments), 2)
                fingerprint.assert_not_called()
                lock.assert_not_called()
        self.assertEqual(compiler.read_bytes(), b"synthetic compiler")

    def test_core_source_alias_to_sealed_data_rejected_before_build_or_worker_reads(self):
        held = self.root / "held.bin"
        held.write_bytes(b"synthetic held content")
        hardlink_or_skip(held, self.root / "ahps_simd.c")
        compiler = self.root / "gcc.exe"
        compiler.write_bytes(b"synthetic compiler")
        args = SimpleNamespace(experiment_dir=self.root / "experiments" / "new-build",
            manifest=self.manifest, corpus_root=self.root, gcc=compiler,
            gcc_sha256="a" * 64, source_sha256="b" * 64, timeout=10)
        with mock.patch.object(pred.bench, "fingerprint") as fingerprint, \
                mock.patch.object(pred.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                pred.build(args, self.root)
            fingerprint.assert_not_called()
            run.assert_not_called()
        for variant in ("baseline", "no-modrm"):
            arguments = self.worker_args("encode", self.source, self.root / "residual.xz")
            arguments[1] = variant
            if variant == "no-modrm":
                arguments[arguments.index("--dll") + 1] = str(self.experiment)
            with self.subTest(variant=variant), mock.patch.object(Path, "read_bytes") as read, \
                    mock.patch.object(pred, "read_build_metadata") as metadata, \
                    mock.patch.object(pred, "Kernel") as kernel, mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(pred.worker(arguments), 1)
                read.assert_not_called()
                metadata.assert_not_called()
                kernel.assert_not_called()

    def test_coordinator_records_roundtrip_mismatch_as_error(self):
        args = SimpleNamespace(manifest=self.manifest, corpus_root=self.root,
                               experiment_dll=self.experiment, timeout=10, repeats=1)
        def corrupt_phase(command, _timeout):
            Path(command[9]).write_bytes(b"incorrect restored content")
            return {"elapsed_seconds": 1.0, "peak_working_set_bytes": 1024,
                    "worker": {"loaded_abi": 206}}
        with mock.patch.object(pred, "implementation", return_value={"synthetic_test": True}), \
                mock.patch.object(pred.bench, "launch", side_effect=corrupt_phase):
            row = pred.one_run(args, "sample", self.source, "baseline", "exe32", 1,
                               "synthetic-test", Path(sys.executable))
        self.assertEqual(row["status"], "error")
        self.assertFalse(row["roundtrip_verified"])
        self.assertIn("Round-trip SHA-256 mismatch", row["error"])


class NativeBaselineTests(unittest.TestCase):
    @unittest.skipUnless(pred.cpu_supports_native() and (pred.REPO / "ahps_simd.dll").is_file(),
                         "production ABI206 DLL and AVX2 are required")
    def test_production_forward_and_matching_inverse_on_synthetic_bytes(self):
        kernel = pred.Kernel(pred.REPO / "ahps_simd.dll", "baseline")
        samples = [b"", b"\xe8", bytes(range(256)) * 4,
                   bytes.fromhex("89e800000000e9000000000f8504030289e800000000")]
        for transform in pred.TRANSFORMS:
            for sample in samples:
                with self.subTest(transform=transform, length=len(sample)):
                    residual = kernel.lift(sample, transform)
                    self.assertEqual(kernel.lift(residual, transform, inverse=True), sample)

    @unittest.skipUnless(pred.cpu_supports_native() and (pred.REPO / "ahps_simd.dll").is_file(),
                         "production ABI206 DLL and Windows process metrics are required")
    def test_separate_baseline_workers_restore_synthetic_input(self):
        # Exercise the actual isolated CLI/import/loading path without any
        # development corpus or experimental DLL. This tests process isolation
        # and metadata, not a performance claim about the synthetic bytes.
        with tempfile.TemporaryDirectory(prefix="predicate-worker-test-") as temporary:
            root = Path(temporary)
            source, encoded, restored = root / "input.bin", root / "residual.xz", root / "restored.bin"
            source.write_bytes(bytes.fromhex("89e800000000e900000000") * 128)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1,
                "development": [{"id": "sample", "path": "input.bin"}], "sealed": []}), encoding="utf-8")
            dll = pred.REPO / "ahps_simd.dll"
            interpreter = getattr(sys, "_base_executable", sys.executable)
            phases = []
            with pred.bench.RunLock(pred.bench.root_repo() / ".ahps-benchmark.lock"):
                for phase, src, dst in (("encode", source, encoded), ("decode", encoded, restored)):
                    command = [interpreter, "-I", "-B", str(Path(pred.__file__)), "_worker", phase,
                               "baseline", "exe_split", str(src), str(dst), "--dll", str(dll),
                               "--dll-sha256", pred.bench.sha256(dll), "--manifest", str(manifest),
                               "--corpus-root", str(root), "--file-id", "sample"]
                    measured = pred.bench.launch(command, 30)
                    self.assertNotIn("error", measured, measured)
                    self.assertEqual(measured["worker"]["loaded_abi"], 206)
                    self.assertGreater(measured["peak_working_set_bytes"], 0)
                    self.assertGreater(measured["elapsed_seconds"], 0)
                    phases.append(measured)
            self.assertNotEqual(phases[0]["worker"]["pid"], phases[1]["worker"]["pid"])
            self.assertEqual(pred.bench.sha256(restored), pred.bench.sha256(source))


if __name__ == "__main__":
    unittest.main()
