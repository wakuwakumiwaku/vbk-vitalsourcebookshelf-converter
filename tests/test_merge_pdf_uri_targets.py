import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pypdf import PdfReader, PdfWriter

from scripts import merge_pdf


class PdfUriTargetsTests(unittest.TestCase):
    def test_import_provenance(self):
        if root := os.environ.get("EXPECTED_SOURCE_ROOT"):
            self.assertTrue(Path(merge_pdf.__file__).resolve().is_relative_to(Path(root).resolve()))

    def test_equivalent_local_uris_keep_bookmarks_and_page_targets(self):
        for target in ("xhtml/chapter.xhtml#section", "./xhtml/%63hapter.xhtml#section",
                       "xhtml/../xhtml/chapter.xhtml#section"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                parts = root / "pdf_parts"
                parts.mkdir()
                (root / "opf.xml").write_text(
                    '<package><manifest>'
                    '<item id="a" href="xhtml/first.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="b" href="xhtml/chapter.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'
                )
                (root / "ncx.xml").write_text(
                    '<ncx><navMap><navPoint><navLabel><text>Chapter</text></navLabel>'
                    f'<content src="{target}"/></navPoint></navMap></ncx>'
                )
                for name, widths in (("000_first.pdf", [100, 110]), ("001_chapter.pdf", [200])):
                    writer = PdfWriter()
                    for width in widths:
                        writer.add_blank_page(width=width, height=300)
                    with (parts / name).open("wb") as handle:
                        writer.write(handle)
                output = root / "book.pdf"
                with (mock.patch.multiple(merge_pdf, OUT_ROOT=str(root), PDF_DIR=str(parts)),
                      mock.patch.object(sys, "argv", ["merge_pdf", "--out", str(output)]),
                      redirect_stdout(io.StringIO())):
                    merge_pdf.main()
                reader = PdfReader(output)
                self.assertEqual(len(reader.pages), 3)
                self.assertEqual(len(reader.outline), 1, "equivalent URI lost its bookmark")
                self.assertEqual(reader.outline[0].title, "Chapter")
                self.assertEqual(reader.get_destination_page_number(reader.outline[0]), 2)


if __name__ == "__main__":
    unittest.main()
