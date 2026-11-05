import io
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import build_epub


class EpubSourceOverlapTests(unittest.TestCase):
    def test_import_provenance(self):
        if root := os.environ.get("EXPECTED_SOURCE_ROOT"):
            self.assertTrue(Path(build_epub.__file__).resolve().is_relative_to(Path(root).resolve()))

    def fixture(self, root):
        source = root / "source"
        oebps = source / "OEBPS"
        oebps.mkdir(parents=True)
        (oebps / "chapter.xhtml").write_text('<html><body><p>Source text</p></body></html>')
        (source / "opf.xml").write_text(
            '<package><manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>'
        )
        (source / "ncx.xml").write_text('<ncx><navMap/></ncx>')
        return source, oebps

    def test_overlapping_build_tree_is_refused_before_any_cleanup(self):
        for location in ("source", "oebps", "inside-oebps", "source-alias", "parent"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, oebps = self.fixture(root)
                before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
                build = {"source": source, "oebps": oebps,
                         "inside-oebps": oebps / "build", "parent": root}[location] if (
                             location != "source-alias") else root / "alias"
                if location == "source-alias":
                    build.symlink_to(source, target_is_directory=True)
                # Outside every destructive build tree, including the parent case.
                output = root.parent / (root.name + ".epub")
                error = None
                with (mock.patch.multiple(build_epub, OUT_ROOT=str(source), OEBPS=str(oebps),
                                          BUILD=str(build)),
                      mock.patch.object(sys, "argv", ["build_epub", "--out", str(output)]),
                      mock.patch.object(build_epub.shutil, "rmtree",
                                        wraps=build_epub.shutil.rmtree) as cleanup,
                      redirect_stdout(io.StringIO())):
                    try:
                        build_epub.main()
                    except Exception as exc:
                        error = exc
                try:
                    self.assertEqual(cleanup.call_args_list, [], "cleanup admitted a source overlap")
                    self.assertIsInstance(error, ValueError)
                    self.assertIn("source", str(error).lower())
                    self.assertEqual({p: p.read_bytes() for p in before if p.exists()}, before)
                    self.assertFalse(output.exists())
                finally:
                    output.unlink(missing_ok=True)

    def test_disjoint_build_preserves_sources_and_produces_epub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, oebps = self.fixture(root)
            before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
            output = root / "book.epub"
            with (mock.patch.multiple(build_epub, OUT_ROOT=str(source), OEBPS=str(oebps),
                                      BUILD=str(root / "build")),
                  mock.patch.object(sys, "argv", ["build_epub", "--out", str(output)]),
                  redirect_stdout(io.StringIO())):
                build_epub.main()
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("OEBPS/chapter.xhtml", archive.namelist())


if __name__ == "__main__":
    unittest.main()
