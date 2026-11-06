"""Synthetic checks for the public export boundary and redacted diagnostics."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import check_public


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tree(self, files):
        names = sorted({"PUBLIC_FILES.txt", *files})
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        (self.root / "PUBLIC_FILES.txt").write_text("# Public sources\n" + "\n".join(names) + "\n", encoding="utf-8")
        return names

    def test_valid_tree_and_only_explicit_files_are_read(self):
        names = self.tree({"README.md": "[Code](module.py)\n", "module.py": "value = 1\n"})
        (self.root / "private").mkdir()
        (self.root / "private" / "unselected.txt").write_bytes(b"\0private")
        with mock.patch.object(Path, "rglob", side_effect=AssertionError("recursive scan")), \
                mock.patch.object(Path, "glob", side_effect=AssertionError("glob scan")):
            self.assertEqual(check_public.read_public_files(self.root), names)
            self.assertEqual(check_public.validate_public_tree(self.root), [])

    def test_malformed_lists_are_rejected_without_echoing_the_path(self):
        cases = ["../private.py", "/private.py", "C:/private.py", "nested/../../private.py",
                 "nested\\private.py", "*.py", " .gitignore", "source.py ",
                 ".git/config", "datasets/source.py", "logs/source.py", "secret.json",
                 "native.dll", "tests/fixtures/v3/dll_c.ahps", "folder./source.py"]
        for invalid in cases:
            with self.subTest(path=invalid):
                (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n" + invalid + "\n", encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    check_public.read_public_files(self.root)
                self.assertNotIn(invalid, str(caught.exception))

    def test_unsorted_duplicate_case_collisions_and_missing_self_are_rejected(self):
        for content in ("z.py\nPUBLIC_FILES.txt\n", "PUBLIC_FILES.txt\na.py\na.py\n",
                        "PUBLIC_FILES.txt\nTest.py\ntest.py\n", "module.py\n", "# empty\n"):
            with self.subTest(content=content):
                (self.root / "PUBLIC_FILES.txt").write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    check_public.read_public_files(self.root)

    def test_secret_findings_include_location_and_never_value(self):
        secret = "ghp_" + "A" * 36
        password = "sample-" + "private-value"
        home = "C:" + "\\Users\\" + "private-person\\work"
        self.tree({"module.py": 'token = "' + secret + '"\npassword = "' + password + '"\npath = "' + home + '"\n'})
        issues = check_public.validate_public_tree(self.root)
        self.assertTrue(any("module.py:1: possible GitHub token" == issue for issue in issues))
        self.assertTrue(any("module.py:2: possible secret assignment" == issue for issue in issues))
        self.assertTrue(any("module.py:3: possible private home path" == issue for issue in issues))
        rendered = "\n".join(issues)
        for value in (secret, password, home, "private-person"):
            self.assertNotIn(value, rendered)

    def test_missing_oversized_and_binary_text_files_are_rejected(self):
        self.tree({"large.py": "x" * (check_public.MAX_FILE_BYTES + 1), "binary.py": b"\0payload", "missing.py": ""})
        (self.root / "missing.py").unlink()
        issues = check_public.validate_public_tree(self.root)
        self.assertTrue(any("large.py:" in issue and "2 MiB" in issue for issue in issues))
        self.assertTrue(any("binary.py:" in issue and "binary" in issue for issue in issues))
        self.assertTrue(any("missing.py:" in issue and "missing" in issue for issue in issues))

    def test_symlink_is_rejected_before_target_is_read(self):
        self.tree({"linked.py": ""})
        linked = self.root / "linked.py"
        original_lstat = Path.lstat

        def symbolic_info(path):
            if path == linked:
                return mock.Mock(st_mode=0o120777, st_file_attributes=0)
            return original_lstat(path)

        with mock.patch.object(Path, "lstat", symbolic_info):
            issues = check_public.validate_public_tree(self.root)
        self.assertTrue(any("linked.py:" in issue and "symlinks" in issue for issue in issues))

    def test_markdown_links_ignore_code_and_accept_selected_targets(self):
        self.tree({
            "README.md": "[Guide](docs/guide.md#usage)\n[Web](https://example.com/x)\n"
                         "[Mail](mailto:public@example.com)\n[Local](#section)\n"
                         "```md\n[Private](missing.md)\n```\n`[Example](missing.md)`\n",
            "docs/guide.md": "[Back](../README.md)\n[Reference][home]\n[home]: ../README.md\n",
        })
        self.assertEqual(check_public.validate_public_tree(self.root), [])

    def test_missing_traversal_and_encoded_traversal_links_are_redacted(self):
        self.tree({"README.md": "[Missing](reports/private.md)\n[Outside](../private.md)\n"
                                "[Encoded](%2e%2e/private.md)\n[Ref][hidden]\n[hidden]: absent.md\n"})
        issues = check_public.validate_public_tree(self.root)
        self.assertEqual(len(issues), 4)
        self.assertTrue(all("Markdown link target is outside PUBLIC_FILES.txt" in issue for issue in issues))
        self.assertNotIn("private.md", "\n".join(issues))

    def test_exact_json_and_synthetic_fixture_exceptions(self):
        self.tree({"examples/corpus_manifest.json": "{}", "tests/fixtures/v5/default-features.json": "{}",
                   "tests/fixtures/v3/tiny_c.ahps": b"\0fixture", "tests/fixtures/v3/tiny.sha256": "0" * 64})
        self.assertEqual(check_public.validate_public_tree(self.root), [])

    def test_cli_exit_status_and_secret_redaction(self):
        self.tree({"README.md": "Ready.\n"})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_public.main(["--tree", str(self.root)]), 0)
        (self.root / "README.md").write_text("[Link](missing.md)\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(check_public.main(["--tree", str(self.root)]), 1)
        self.assertIn("README.md:1:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
