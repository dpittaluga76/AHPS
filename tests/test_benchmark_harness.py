"""Synthetic-only checks for holdout protection and trustworthy benchmark records."""

import io
import json
import lzma
import os
from dataclasses import asdict, make_dataclass
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import benchmark_ahps as bench
import ahps
from tests.test_support import hardlink_or_skip


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ahps-harness-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "manifest.json"
        self.source = self.root / "synthetic.bin"
        self.source.write_bytes((b"synthetic harness validation\x00" * 100) + bytes(range(256)))
        self.output = self.root / "results.jsonl"
        self.write_manifest([{"id": "sample", "path": self.source.name}])

    def write_manifest(self, development, sealed=None):
        self.manifest.write_text(json.dumps({"schema_version": 1, "development": development,
                                             "sealed": sealed or []}), encoding="utf-8")

    def argv(self, *extra):
        return ["--manifest", str(self.manifest), "--corpus-root", str(self.root),
                "--files", "sample", "--repeats", "1", "--preset", "0", "--codecs", "xz",
                "--output", str(self.output), *extra]

    def protected_fixtures(self):
        unselected, sealed = self.root / "unselected.bin", self.root / "custom-held.bin"
        unselected.write_bytes(b"unselected synthetic fixture")
        sealed.write_bytes(b"sealed synthetic fixture")
        self.write_manifest([{"id": "sample", "path": self.source.name},
                             {"id": "unselected", "path": unselected.name}],
                            [{"id": "custom-held", "path": str(sealed)}])
        return unselected, sealed

    def assert_worker_guard(self, phase, source, destination, expected_error, extra=()):
        arguments = [phase, "xz", str(source), str(destination), "--manifest", str(self.manifest),
                     "--corpus-root", str(self.root), "--file-id", "sample", "--preset", "0", "--block-size", "4096", *extra]
        capture = io.StringIO()
        with mock.patch("sys.stdout", capture), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected content read")) as read, \
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected content write")) as write:
            self.assertEqual(bench.main(["_worker", *arguments]), 1)
            read.assert_not_called()
            write.assert_not_called()
        self.assertIn(expected_error, json.loads(capture.getvalue())["error"])

    def test_entire_selection_is_guarded_before_any_source_is_read(self):
        cases = [
            ({"id": "bad", "path": "ooffice"}, []),
            ({"id": "bad", "path": "nested/XML.bin"}, []),
            ({"id": "bad", "path": "xml "}, []),
            ({"id": "bad", "path": "osdb"}, []),
            ({"id": "bad", "path": "renamed.bin", "split": "holdout"}, []),
            ({"id": "bad", "path": "renamed.bin"}, [{"id": "sealed-id", "path": "renamed.bin"}]),
            ({"id": "bad", "path": "renamed.bin"}, ["bad"]),
            ({"id": "bad", "path": "renamed.bin"}, ["renamed.bin"]),
        ]
        for entry, sealed in cases:
            with self.subTest(entry=entry):
                self.write_manifest([{"id": "sample", "path": self.source.name}, entry], sealed)
                with mock.patch.object(bench, "root_repo", return_value=self.root), \
                        mock.patch.object(bench, "fingerprint", side_effect=AssertionError("data read before guard")):
                    self.assertEqual(bench.main(self.argv("--files", "sample", "bad")), 2)
                self.assertFalse(self.output.exists())

    def test_explicit_paths_and_unique_ids_are_required(self):
        for path in ("*.bin", "../outside.bin", "sample:stream", str(self.source)):
            self.write_manifest([{"id": "sample", "path": path}])
            with self.subTest(path=path), self.assertRaises(ValueError):
                bench.select_inputs(self.manifest, self.root, ["sample"])
        self.write_manifest([{"id": "sample", "path": self.source.name}] * 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            bench.select_inputs(self.manifest, self.root, ["sample"])

    def test_output_cannot_be_a_source_or_sealed_file(self):
        original_hash = bench.sha256(self.source)
        for output in (self.source, self.manifest, self.root / "xml"):
            with self.subTest(output=output), mock.patch.object(bench, "root_repo", return_value=self.root):
                self.assertEqual(bench.main(self.argv("--output", str(output))), 2)
        self.assertEqual(bench.sha256(self.source), original_hash)

    def test_output_protects_all_manifest_paths_and_hardlink_aliases(self):
        unselected, sealed = self.protected_fixtures()
        protected = [self.source, unselected, sealed, self.manifest]
        aliases = []
        for index, path in enumerate(protected):
            alias = self.root / f"alias-{index}.bin"
            hardlink_or_skip(path, alias)
            aliases.append(alias)
        originals = {path: path.read_bytes() for path in protected}
        for output in [*protected, *aliases]:
            with self.subTest(output=output), mock.patch.object(bench, "root_repo", return_value=self.root), \
                    mock.patch.object(bench, "fingerprint") as fingerprint:
                self.assertEqual(bench.main(self.argv("--output", str(output))), 2)
                fingerprint.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in protected}, originals)

    def test_selected_hardlink_to_custom_sealed_input_is_rejected(self):
        _, sealed = self.protected_fixtures()
        alias = self.root / "input-alias.bin"
        hardlink_or_skip(sealed, alias)
        self.write_manifest([{"id": "sample", "path": self.source.name}, {"id": "alias", "path": alias.name}],
                            [{"id": "held", "path": str(sealed)}])
        with mock.patch.object(Path, "read_bytes") as read:
            with self.assertRaisesRegex(ValueError, "Sealed input"):
                bench.select_inputs(self.manifest, self.root, ["sample", "alias"])
            read.assert_not_called()

    def test_direct_worker_cannot_overwrite_manifest_or_any_data_path(self):
        unselected, sealed = self.protected_fixtures()
        archive = self.root / "worker-input.xz"
        archive.write_bytes(lzma.compress(b"synthetic restored content"))
        protected = [self.source, self.manifest, unselected, sealed]
        alias = self.root / "worker-manifest-alias.bin"
        hardlink_or_skip(self.manifest, alias)
        originals = {path: path.read_bytes() for path in protected}
        for phase, source in (("encode", self.source), ("decode", archive)):
            for destination in [*protected, alias]:
                with self.subTest(phase=phase, destination=destination):
                    self.assert_worker_guard(phase, source, destination, "Output must not overwrite")
        self.assertEqual({path: path.read_bytes() for path in protected}, originals)

    def test_direct_worker_cannot_read_custom_sealed_input_or_alias(self):
        _, sealed = self.protected_fixtures()
        alias = self.root / "worker-sealed-alias.bin"
        hardlink_or_skip(sealed, alias)
        for source in (sealed, alias):
            with self.subTest(source=source):
                self.assert_worker_guard("decode", source, self.root / "restored.bin", "protected manifest/data path")

    def test_runtime_artifacts_and_aliases_are_protected_before_writes(self):
        artifacts = [self.root / name for name in ("ahps.py", "ahps_simd.c", "ahps_simd.dll", "7z.exe", "7z.dll")]
        for artifact in artifacts:
            artifact.write_bytes(b"synthetic implementation fixture")
        aliases = [self.root / "code-alias.bin", self.root / "dll-alias.bin"]
        hardlink_or_skip(artifacts[0], aliases[0])
        hardlink_or_skip(artifacts[2], aliases[1])
        sevenz = self.root / "7z.exe"
        extra = self.root / "experiment-build.json"
        with mock.patch.object(bench, "REPO", self.root), mock.patch.object(bench, "root_repo", return_value=self.root):
            runtime = bench.protected_runtime_paths(sevenz, extra=(extra,))
            self.assertIn(extra.resolve(), runtime)
            self.assertIn(Path(bench.__file__).resolve(), runtime)
            self.assertIn(Path(sys.executable).resolve(), runtime)
            self.assertIn(Path(sys.modules["_lzma"].__file__).resolve(), runtime)
            for destination in [*runtime, *aliases]:
                if destination == extra.resolve():
                    # Callers such as the predicate driver explicitly add their artifacts.
                    with self.assertRaisesRegex(ValueError, "Output must not overwrite"):
                        bench.guard_destination(destination, runtime)
                    continue
                with self.subTest(destination=destination), \
                        mock.patch.object(bench, "one_run", side_effect=AssertionError("run started before guard")) as run:
                    self.assertEqual(bench.main(self.argv("--output", str(destination), "--sevenz", str(sevenz))), 2)
                    run.assert_not_called()
                self.assert_worker_guard("encode", self.source, destination, "Output must not overwrite",
                                         extra=("--sevenz", str(sevenz)))
        self.assertTrue(all(path.read_bytes() == b"synthetic implementation fixture" for path in artifacts))

    def test_implementation_aliases_cannot_read_sealed_data(self):
        held = self.root / "custom-held.bin"
        held.write_bytes(b"synthetic sealed fixture")
        self.write_manifest([{"id": "sample", "path": self.source.name}],
                            [{"id": "held", "path": str(held)}])
        args = SimpleNamespace(manifest=self.manifest, corpus_root=self.root, preset=0, block_size=4096,
                               sevenz=bench.SEVEN_ZIP, timeout=10, repeats=1, features="{}")
        for index, name in enumerate(("ahps.py", "ahps_simd.c", "ahps_simd.dll")):
            fake_repo = self.root / f"implementation-{index}"
            fake_repo.mkdir()
            hardlink_or_skip(held, fake_repo / name)
            with self.subTest(artifact=name), mock.patch.object(bench, "REPO", fake_repo), \
                    mock.patch.object(bench, "fingerprint") as fingerprint, mock.patch.object(bench, "identities") as identities:
                row = bench.one_run(args, "sample", self.source, "ahps", 1, "unit-test", Path(sys.executable))
                self.assertEqual(row["status"], "error")
                self.assertIn("Implementation artifact aliases protected data", row["error"])
                fingerprint.assert_not_called()
                identities.assert_not_called()
                # xz does not read those AHPS-only implementation artifacts.
                bench.guard_implementation_inputs("xz", Path(sys.executable), bench.SEVEN_ZIP,
                                                  bench.protected_manifest_paths(self.manifest, self.root))
                capture = io.StringIO()
                arguments = ["_worker", "encode", "ahps", str(self.source), str(self.output),
                             "--manifest", str(self.manifest), "--corpus-root", str(self.root),
                             "--file-id", "sample", "--preset", "0", "--block-size", "4096"]
                with mock.patch("sys.stdout", capture), mock.patch.object(Path, "read_bytes") as read, \
                        mock.patch.object(Path, "write_bytes") as write:
                    self.assertEqual(bench.main(arguments), 1)
                    read.assert_not_called()
                    write.assert_not_called()
                self.assertIn("Implementation artifact aliases protected data", json.loads(capture.getvalue())["error"])

    def test_bcj2_must_be_on_the_file_entry_not_just_the_archive(self):
        listing = "Path = archive.7z\nMethod = BCJ2 LZMA2\n\n----------\nPath = input.bin\nSize = 42\nMethod = LZMA2\n"
        with self.assertRaisesRegex(ValueError, "actually use BCJ2"):
            bench.verify_bcj2_listing(listing, 42)
        listing = listing.replace("Method = LZMA2", "Method = BCJ2 LZMA2:26")
        self.assertIn("BCJ2", bench.verify_bcj2_listing(listing, 42)["Method"])
        with self.assertRaisesRegex(ValueError, "expected input"):
            bench.verify_bcj2_listing(listing, 41)

    def test_feature_values_are_strict_and_missing_keys_stay_enabled(self):
        defaults = {"exe32": True, "exe_split": True, "delta_split": True, "per_chunk": True}
        self.assertEqual(bench.parse_features("{}"), defaults)
        expected = {**defaults, "exe_split": False, "per_chunk": False}
        effective = bench.parse_features('{"per_chunk":false,"exe_split":false}')
        self.assertEqual(effective, expected)
        self.assertEqual(bench.canonical_features(effective),
                         '{"delta_split":true,"exe32":true,"exe_split":false,"per_chunk":false}')
        for invalid in ("not-json", "null", "[]", "true", "1", '{"unknown":true}',
                        '{"exe32":1}', '{"exe_split":"false"}', '{"delta_split":null}'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                bench.parse_features(invalid)

    def test_invalid_or_misleading_features_fail_before_input_access(self):
        for value in ("[]", '{"unknown":true}', '{"exe32":0}', '{"exe32":false}'):
            with self.subTest(features=value), mock.patch.object(bench, "root_repo") as root, \
                    mock.patch.object(bench, "select_inputs") as select, mock.patch("sys.stderr", io.StringIO()):
                # The final case is valid JSON but --codecs xz cannot honor it.
                self.assertEqual(bench.main(self.argv("--features", value)), 2)
                root.assert_not_called()
                select.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_worker_feature_validation_precedes_source_access(self):
        for codec, value in (("ahps", '{"exe32":1}'), ("xz", '{"exe32":false}')):
            arguments = ["_worker", "encode", codec, str(self.source), str(self.output),
                         "--manifest", str(self.manifest), "--corpus-root", str(self.root),
                         "--file-id", "sample", "--preset", "0", "--block-size", "4096", "--features", value]
            capture = io.StringIO()
            with self.subTest(codec=codec), mock.patch("sys.stdout", capture), \
                    mock.patch.object(bench, "select_inputs") as select, \
                    mock.patch.object(Path, "read_bytes") as read, mock.patch.object(Path, "write_bytes") as write:
                self.assertEqual(bench.main(arguments), 1)
                select.assert_not_called()
                read.assert_not_called()
                write.assert_not_called()
            self.assertEqual(json.loads(capture.getvalue())["status"], "error")

    def test_ahps_worker_forwards_plan_features_and_records_report(self):
        features_type = make_dataclass("PlanFeatures", [(name, bool, True) for name in bench.FEATURE_NAMES])
        search_type = make_dataclass("SearchMethod", [(name, object, value) for name, value in bench.SEARCH_DEFAULTS.items()])
        effective = bench.parse_features('{"exe32":false,"delta_split":false}')
        search = bench.parse_search('{"chunk_trial_preset":1,"exe32_chunk_rival":false}')
        library = SimpleNamespace(ahps_abi_version=lambda: 206, _name=str(self.root / "fake.dll"))

        def compress(data, **kwargs):
            self.assertEqual(data, self.source.read_bytes())
            self.assertIsInstance(kwargs["features"], features_type)
            self.assertEqual(asdict(kwargs["features"]), effective)
            self.assertIsInstance(kwargs["search"], search_type)
            self.assertEqual(asdict(kwargs["search"]), search)
            return b"synthetic compressed output", SimpleNamespace(programs={"RAW": (1, len(data))},
                                                                    features=kwargs["features"], search=kwargs["search"])

        fake = SimpleNamespace(PlanFeatures=features_type, SearchMethod=search_type, SIMD_BRIDGE=SimpleNamespace(lib=library),
                               MAGIC_HEADER=b"AHPS\x05", EXPECTED_ABI_VERSION=206,
                               ahps_compress_bytes=mock.Mock(side_effect=compress))
        arguments = ["_worker", "encode", "ahps", str(self.source), str(self.output),
                     "--manifest", str(self.manifest), "--corpus-root", str(self.root),
                     "--file-id", "sample", "--preset", "0", "--block-size", "4096", "--features",
                     '{"exe32":false,"delta_split":false}', '--search', bench.canonical_search(search)]
        capture = io.StringIO()
        with mock.patch.dict(sys.modules, {"ahps": fake}), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch("sys.stdout", capture), mock.patch.object(bench, "process_peak", return_value=4096):
            self.assertEqual(bench.main(arguments), 0)
        result = json.loads(capture.getvalue())
        self.assertEqual(result["features"], effective)
        self.assertEqual(result["report_features"], effective)
        self.assertEqual(result["search"], search)
        self.assertEqual(result["report_search"], search)
        self.assertEqual(self.output.read_bytes(), b"synthetic compressed output")
        fake.ahps_compress_bytes.assert_called_once()

        # A core that silently ignores disabled flags must not produce a success.
        fake.ahps_compress_bytes.side_effect = lambda data, **kwargs: (
            b"ignored flags", SimpleNamespace(programs={}, features=features_type()))
        capture = io.StringIO()
        with mock.patch.dict(sys.modules, {"ahps": fake}), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch("sys.stdout", capture), mock.patch.object(Path, "write_bytes") as write:
            self.assertEqual(bench.main(arguments), 1)
            write.assert_not_called()
        self.assertIn("report.features does not match", json.loads(capture.getvalue())["error"])

        # Forwarding the option is insufficient if the core ignores it.
        fake.ahps_compress_bytes.side_effect = lambda data, **kwargs: (
            b"ignored search", SimpleNamespace(programs={}, features=kwargs["features"], search=search_type()))
        capture = io.StringIO()
        with mock.patch.dict(sys.modules, {"ahps": fake}), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch("sys.stdout", capture), mock.patch.object(Path, "write_bytes") as write:
            self.assertEqual(bench.main(arguments), 1)
            write.assert_not_called()
        self.assertIn("report.search does not match", json.loads(capture.getvalue())["error"])

    def test_feature_forwarding_and_row_provenance_are_ahps_only(self):
        args = SimpleNamespace(manifest=self.manifest, corpus_root=self.root, preset=0, block_size=4096,
                               sevenz=bench.SEVEN_ZIP, timeout=10, repeats=1,
                               features='{"exe_split":false,"per_chunk":false}',
                               search='{"chunk_trial_preset":1,"exe32_chunk_rival":false}')
        effective = bench.parse_features(args.features)
        search = bench.parse_search(args.search)
        original = self.source.read_bytes()
        for codec in ("ahps", "xz"):
            commands = []

            def successful_phase(command, *_args, **_kwargs):
                commands.append(command)
                phase = command[5]
                Path(command[8]).write_bytes(b"compressed" if phase == "encode" else original)
                result = {"elapsed_seconds": 1.0, "peak_working_set_bytes": 4096}
                if codec == "ahps":
                    result["worker"] = {"report_features": effective, "report_search": search}
                return result

            with self.subTest(codec=codec), mock.patch.object(bench, "identities", return_value={"synthetic_test": True}), \
                    mock.patch.object(bench, "launch", side_effect=successful_phase):
                row = bench.one_run(args, "sample", self.source, codec, 1, "unit-test", Path(sys.executable))
            self.assertEqual(row["status"], "ok")
            if codec == "ahps":
                self.assertEqual(row["settings"]["features"], effective)
                self.assertEqual(row["report_features"], effective)
                self.assertEqual(row["settings"]["search"], search)
                self.assertEqual(row["report_search"], search)
                for command in commands:
                    self.assertEqual(command[command.index("--features") + 1], bench.canonical_features(effective))
                    self.assertEqual(command[command.index("--search") + 1], bench.canonical_search(search))
            else:
                self.assertNotIn("features", row["settings"])
                self.assertNotIn("report_features", row)
                self.assertTrue(all("--features" not in command for command in commands))
                self.assertNotIn("search", row["settings"])
                self.assertNotIn("report_search", row)
                self.assertTrue(all("--search" not in command for command in commands))

    def test_search_validation_and_non_ahps_rejection_precede_input_access(self):
        self.assertEqual(bench.parse_search("{}"), bench.SEARCH_DEFAULTS)
        self.assertEqual(bench.parse_search('{"chunk_trial_preset":0}'),
                         {"chunk_trial_preset": 0, "exe32_chunk_rival": True})
        invalid = ("[]", "null", "false", "bad-json", '{"unknown":1}',
                   '{"chunk_trial_preset":true}', '{"chunk_trial_preset":1.0}',
                   '{"chunk_trial_preset":-1}', '{"chunk_trial_preset":10}',
                   '{"exe32_chunk_rival":null}', '{"exe32_chunk_rival":1}')
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.parse_search(value)
        # The last value is valid, but XZ cannot implement an AHPS search method.
        for value in (*invalid, '{"chunk_trial_preset":1}'):
            with self.subTest(value=value), mock.patch.object(bench, "root_repo") as root, \
                    mock.patch.object(bench, "select_inputs") as select, mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(bench.main(self.argv("--search", value)), 2)
                root.assert_not_called()
                select.assert_not_called()
        for codec, value in (("ahps", '{"chunk_trial_preset":true}'), ("xz", '{"chunk_trial_preset":1}')):
            argv = ["_worker", "encode", codec, str(self.source), str(self.output),
                    "--manifest", str(self.manifest), "--corpus-root", str(self.root),
                    "--file-id", "sample", "--preset", "0", "--block-size", "4096", "--search", value]
            with mock.patch("sys.stdout", io.StringIO()), mock.patch.object(bench, "select_inputs") as select:
                self.assertEqual(bench.main(argv), 1)
                select.assert_not_called()

    def test_bad_cross_batch_schedule_is_rejected_before_input_access(self):
        invalid = (("--repeat-indices", "1"),
                   ("--comparison-id", "cohort", "--repeat-indices", "0"),
                   ("--comparison-id", "cohort", "--repeat-indices", "4"),
                   ("--comparison-id", "cohort", "--repeat-indices", "1", "1"),
                   ("--comparison-id", "cohort", "--order-start", "0"),
                   ("--comparison-id", "bad id"), ("--order-start", "2"))
        for extra in invalid:
            with self.subTest(extra=extra), mock.patch.object(bench, "root_repo") as root, \
                    mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(bench.main(self.argv("--repeats", "3", *extra)), 2)
                root.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows process peak/lock integration")
    def test_repetitions_keep_global_identity_across_bounded_invocations(self):
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            for index, order in ((1, 1), (2, 4), (3, 5)):
                self.assertEqual(bench.main(self.argv("--repeats", "3", "--repeat-indices", str(index),
                                                     "--comparison-id", "paired-cohort", "--order-start", str(order))), 0)
        rows = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual([r["repeat"] for r in rows], [1, 2, 3])
        self.assertEqual([r["execution_order"] for r in rows], [1, 4, 5])
        self.assertEqual({r["comparison_id"] for r in rows}, {"paired-cohort"})
        self.assertEqual({r["settings"]["requested_repeats"] for r in rows}, {3})
        self.assertEqual(len({r["batch_id"] for r in rows}), 3)
        self.assertEqual(len({r["run_id"] for r in rows}), 3)
        self.assertTrue(all(r["status"] == "ok" and r["roundtrip_verified"] for r in rows))

    @unittest.skipUnless(os.name == "nt" and ahps.SIMD_BRIDGE.lib is not None,
                         "requires Windows metrics and the optional native AHPS kernel")
    def test_real_ahps_search_option_is_confirmed_and_roundtrips(self):
        search = {"chunk_trial_preset": 0, "exe32_chunk_rival": False}
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            self.assertEqual(bench.main(self.argv("--codecs", "ahps", "--search", json.dumps(search))), 0)
        row = json.loads(self.output.read_text())
        self.assertTrue(row["roundtrip_verified"])
        self.assertEqual(row["settings"]["search"], search)
        self.assertEqual(row["report_search"], search)
        self.assertEqual(row["phases"]["encode"]["worker"]["report_search"], search)

    @unittest.skipUnless(os.name == "nt" and ahps.SIMD_BRIDGE.lib is not None,
                         "requires Windows metrics and the optional native AHPS kernel")
    def test_file_decoder_is_recorded_and_roundtrips(self):
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            self.assertEqual(bench.main(self.argv("--codecs", "ahps", "--decode-api", "file")), 0)
        row = json.loads(self.output.read_text())
        self.assertTrue(row["roundtrip_verified"])
        self.assertEqual(row["settings"]["decode_api"], "file")
        self.assertIn("file", row["phases"]["decode"]["argv"])

    @unittest.skipUnless(os.name == "nt" and ahps.SIMD_BRIDGE.lib is not None,
                         "requires Windows metrics and the optional native AHPS kernel")
    def test_bytes_decoder_rows_keep_their_historical_settings(self):
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            self.assertEqual(bench.main(self.argv("--codecs", "ahps")), 0)
        self.assertNotIn("decode_api", json.loads(self.output.read_text())["settings"])

    def test_roundtrip_mismatch_cannot_be_a_success(self):
        args = type("Args", (), {"manifest": self.manifest, "corpus_root": self.root,
                                 "preset": 0, "block_size": 4096, "sevenz": bench.SEVEN_ZIP,
                                 "timeout": 10, "repeats": 1, "features": "{}"})()

        def corrupt_output(command, *_args, **_kwargs):
            Path(command[8]).write_bytes(b"incorrect restored content")
            return {"elapsed_seconds": 1.0, "peak_working_set_bytes": 1}

        with mock.patch.object(bench, "identities", return_value={"synthetic_test": True}), \
                mock.patch.object(bench, "launch", side_effect=corrupt_output):
            row = bench.one_run(args, "sample", self.source, "xz", 1, "unit-test", Path(sys.executable))
        self.assertEqual(row["status"], "error")
        self.assertFalse(row["roundtrip_verified"])
        self.assertIn("Round-trip SHA-256 mismatch", row["error"])

    @unittest.skipUnless(os.name == "nt", "Windows process peak/lock integration")
    def test_kernel_peak_survives_real_child_exit(self):
        executable = getattr(sys, "_base_executable", sys.executable)
        process = subprocess.Popen([executable, "-I", "-B", "-c", "x = bytearray(32 * 1024 * 1024)"])
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertGreaterEqual(bench.process_peak(int(process._handle)), 32 * 1024 * 1024)

    @unittest.skipUnless(os.name == "nt", "Windows process peak/lock integration")
    def test_lock_excludes_a_second_run_and_releases(self):
        path = self.root / "benchmark.lock"
        with bench.RunLock(path):
            with self.assertRaisesRegex(RuntimeError, "Another benchmark"):
                with bench.RunLock(path):
                    self.fail("second run acquired lock")
        with bench.RunLock(path):
            pass

    @unittest.skipUnless(os.name == "nt", "Windows process peak/lock integration")
    def test_fresh_phases_hashes_metrics_and_append_without_resume(self):
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            self.assertEqual(bench.main(self.argv()), 0)
            self.assertEqual(bench.main(self.argv()), 0)
        rows = [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["batch_id"], rows[1]["batch_id"])
        for row in rows:
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["settings"]["requested_repeats"], 1)
            self.assertEqual(row["settings"]["phase_timeout_seconds"], 1800)
            self.assertNotIn("block_size", row["settings"])
            self.assertTrue(row["roundtrip_verified"])
            self.assertEqual(row["restored_sha256"], row["source"]["sha256"])
            self.assertEqual(row["source"]["sha256"], bench.sha256(self.source))
            self.assertNotEqual(row["phases"]["encode"]["worker"]["pid"], row["phases"]["decode"]["worker"]["pid"])
            self.assertTrue(all(artifact["sha256"] for artifact in row["implementation"]["artifacts"]))
            for phase in row["phases"].values():
                self.assertGreater(phase["peak_working_set_bytes"], 0)
                self.assertGreater(phase["elapsed_seconds"], 0)
                expected_speed = self.source.stat().st_size / (1024 * 1024) / phase["elapsed_seconds"]
                self.assertAlmostEqual(phase["uncompressed_mib_per_second"], expected_speed)
                self.assertAlmostEqual(phase["peak_working_set_over_uncompressed"],
                                       phase["peak_working_set_bytes"] / self.source.stat().st_size)

    @unittest.skipUnless(os.name == "nt", "Windows process peak/lock integration")
    def test_errors_are_recorded_without_hiding_other_results(self):
        with mock.patch.object(bench, "root_repo", return_value=self.root):
            code = bench.main(self.argv("--codecs", "7z-bcj2", "xz", "--sevenz", str(self.root / "missing.exe")))
        self.assertEqual(code, 1)
        rows = [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in rows], ["error", "ok"])
        for row in rows:
            self.assertEqual(row["settings"]["requested_repeats"], 1)
            self.assertEqual(row["settings"]["phase_timeout_seconds"], 1800)
        self.assertIn("error", rows[0])
        self.assertTrue(rows[1]["roundtrip_verified"])


if __name__ == "__main__":
    unittest.main()
