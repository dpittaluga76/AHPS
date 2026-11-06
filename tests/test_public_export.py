"""Public exports copy only reviewed files and never overwrite a tree."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.export_public import export_public


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "source"
        self.source.mkdir()
        self.names = ["PUBLIC_FILES.txt", "README.md", "ahps.py"]
        (self.source / "PUBLIC_FILES.txt").write_text("\n".join(self.names) + "\n")
        (self.source / "README.md").write_text("# Example source\n")
        (self.source / "ahps.py").write_text("print('synthetic fixture')\n")

    def test_only_selected_files_are_exported_and_hashed(self):
        (self.source / ".git").mkdir()
        (self.source / ".git" / "private-object").write_bytes(b"not public")
        (self.source / "private.csv").write_bytes(b"not public")
        destination, archive = self.folder / "public", self.folder / "source.zip"
        records = export_public(self.source, destination, archive)
        self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()},
                         set(self.names) | {"PUBLIC_RELEASE.json"})
        manifest = json.loads((destination / "PUBLIC_RELEASE.json").read_text())
        self.assertEqual(manifest["files"], records)
        for row in records:
            content = (destination / row["path"]).read_bytes()
            self.assertEqual(row["bytes"], len(content))
            self.assertEqual(row["sha256"], hashlib.sha256(content).hexdigest())
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), {"public/" + p for p in self.names + ["PUBLIC_RELEASE.json"]})

    def test_existing_destinations_are_preserved(self):
        destination, archive = self.folder / "public", self.folder / "source.zip"
        destination.mkdir()
        marker = destination / "existing.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_public(self.source, destination, archive)
        self.assertEqual(marker.read_text(), "keep")
        archive.write_bytes(b"keep zip")
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_public(self.source, self.folder / "new", archive)
        self.assertEqual(archive.read_bytes(), b"keep zip")
        self.assertFalse((self.folder / "new").exists())


if __name__ == "__main__":
    unittest.main()
