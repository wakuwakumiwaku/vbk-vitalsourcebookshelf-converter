import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from scripts import merge_pdf


class MergeCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parts = self.root / "pdf_parts"
        self.parts.mkdir()
        self.output = self.root / "book.pdf"
        self.output.write_bytes(b"previous complete book")
        (self.root / "ncx.xml").write_text("<ncx><navMap/></ncx>", encoding="utf-8")
        self.spine = [{"index": 0, "href": "a.xhtml"}, {"index": 1, "href": "b.xhtml"}]

    def part(self, name, *, pages=1, large=True):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=100)
        if large:
            writer.add_metadata({"/Subject": "synthetic " * 400})
        writer.write(self.parts / name)
        writer.close()

    def merge(self, spine=None):
        with patch.object(merge_pdf, "OUT_ROOT", str(self.root)), patch.object(
            merge_pdf, "PDF_DIR", str(self.parts)
        ), patch.object(merge_pdf, "load_spine", return_value=self.spine if spine is None else spine), patch(
            "sys.argv", ["merge_pdf", "--out", str(self.output)]
        ):
            merge_pdf.main()

    def test_missing_part_refuses_to_replace_complete_output(self):
        self.part("000_a.pdf")
        with self.assertRaisesRegex(ValueError, "[Mm]issing.*PDF|PDF.*missing"):
            self.merge()
        self.assertEqual(self.output.read_bytes(), b"previous complete book")

    def test_empty_spine_cannot_publish_an_empty_book(self):
        with self.assertRaisesRegex(ValueError, "[Ee]mpty.*spine|[Nn]o.*spine"):
            self.merge([])
        self.assertEqual(self.output.read_bytes(), b"previous complete book")

    def test_zero_page_part_is_not_a_completed_chapter(self):
        self.part("000_a.pdf", pages=0)
        self.part("001_b.pdf")
        with self.assertRaisesRegex(ValueError, "[Nn]o pages|[Zz]ero.*pages"):
            self.merge()
        self.assertEqual(self.output.read_bytes(), b"previous complete book")

    def test_small_valid_pdf_parts_are_not_discarded_by_a_size_heuristic(self):
        self.part("000_a.pdf", large=False)
        self.part("001_b.pdf", pages=2, large=False)
        self.assertLess((self.parts / "000_a.pdf").stat().st_size, 2000)
        self.merge()
        self.assertEqual(len(PdfReader(self.output).pages), 3)


if __name__ == "__main__":
    unittest.main()
