import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from scripts import merge_pdf


class PdfPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parts = self.root / "pdf_parts"
        self.parts.mkdir()
        self.output = self.root / "book.pdf"
        self.previous = b"previous complete book"
        self.output.write_bytes(self.previous)
        (self.root / "ncx.xml").write_text("<ncx><navMap/></ncx>", encoding="utf-8")
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_metadata({"/Subject": "synthetic " * 400})
        writer.write(self.parts / "000_a.pdf")
        writer.close()

    def merge(self, output=None):
        with patch.object(merge_pdf, "OUT_ROOT", str(self.root)), patch.object(
            merge_pdf, "PDF_DIR", str(self.parts)
        ), patch.object(merge_pdf, "load_spine", return_value=[{"index": 0, "href": "a.xhtml"}]), patch(
            "sys.argv", ["merge_pdf", "--out", str(output or self.output)]
        ):
            merge_pdf.main()

    def snapshot(self):
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*")}

    def test_partial_write_never_destroys_an_existing_output(self):
        before = self.snapshot()

        def fail(writer, stream, *args, **kwargs):
            stream.write(b"partial PDF")
            raise OSError("synthetic write failure")

        with patch.object(PdfWriter, "write", fail):
            with self.assertRaisesRegex(OSError, "synthetic write failure"):
                self.merge()
        self.assertEqual(self.output.read_bytes(), self.previous)
        self.assertEqual(self.snapshot(), before)
        self.merge()
        self.assertEqual(len(PdfReader(self.output).pages), 1)

    def test_failed_first_write_leaves_no_partial_output(self):
        self.output.unlink()
        before = self.snapshot()

        def fail(writer, stream, *args, **kwargs):
            stream.write(b"partial PDF")
            raise OSError("synthetic write failure")

        with patch.object(PdfWriter, "write", fail):
            with self.assertRaises(OSError):
                self.merge()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_publication_failure_preserves_output_and_cleans_staging(self):
        before = self.snapshot()
        reached = []

        def fail(source, destination):
            reached.append(True)
            raise OSError("synthetic rename failure")

        error = None
        with patch.object(os, "replace", fail):
            try:
                self.merge()
            except OSError as exc:
                error = exc
        self.assertTrue(reached, "publication must pass through atomic replacement")
        self.assertIsInstance(error, OSError)
        self.assertEqual(self.output.read_bytes(), self.previous)
        self.assertEqual(self.snapshot(), before)

    def test_success_follows_output_symlink_and_preserves_target_mode(self):
        self.output.chmod(0o640)
        alias = self.root / "alias.pdf"
        alias.symlink_to(self.output.name)
        self.merge(alias)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(PdfReader(alias).pages), 1)


if __name__ == "__main__":
    unittest.main()
