import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import build_epub, build_pdf, walk_spine


class WalkSpinePackagingTests(unittest.TestCase):
    def test_duplicate_basenames_flow_from_capture_to_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_root = root / "source"
            oebps = out_root / "OEBPS"
            build = root / "build"
            epub_out = root / "book.epub"

            hrefs = [
                "xhtml/front/chapter.xhtml",
                "xhtml/main/chapter.xhtml",
                "xhtml/Chapter%20%C3%9C.xhtml",
            ]
            with mock.patch.object(walk_spine, "OEBPS", str(oebps)):
                destinations = [walk_spine.capture_path(href) for href in hrefs]
            for destination, text in zip(
                destinations, ["Front matter", "Main chapter", "Encoded chapter"]
            ):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(f"<html><body>{text}</body></html>", encoding="utf-8")

            self.assertNotEqual(destinations[0], destinations[1])
            self.assertTrue(
                walk_spine.frame_matches_href(
                    "https://jigsaw.elsevier.com/books/id/epub/OEBPS/xhtml/front/chapter.xhtml",
                    hrefs[0],
                )
            )
            self.assertFalse(
                walk_spine.frame_matches_href(
                    "https://jigsaw.elsevier.com/books/id/epub/OEBPS/xhtml/main/chapter.xhtml",
                    hrefs[0],
                )
            )
            self.assertTrue(
                walk_spine.frame_matches_href(
                    "https://jigsaw.elsevier.com/books/id/epub/OEBPS/xhtml/Chapter%20%C3%9C.xhtml",
                    "xhtml/Chapter%20%C3%9C.xhtml",
                )
            )

            (out_root / "opf.xml").write_text(
                """\
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="front" href="xhtml/front/chapter.xhtml" media-type="application/xhtml+xml" />
    <item id="main" href="xhtml/main/chapter.xhtml" media-type="application/xhtml+xml" />
    <item id="encoded" href="xhtml/Chapter%20%C3%9C.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="front" /><itemref idref="main" /><itemref idref="encoded" />
  </spine>
</package>
""",
                encoding="utf-8",
            )
            (out_root / "ncx.xml").write_text("<ncx><navMap /></ncx>", encoding="utf-8")

            with (
                mock.patch.multiple(
                    build_epub,
                    OUT_ROOT=str(out_root),
                    OEBPS=str(oebps),
                    BUILD=str(build),
                ),
                mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(epub_out)]),
                redirect_stdout(io.StringIO()),
            ):
                build_epub.main()

            with zipfile.ZipFile(epub_out) as epub:
                self.assertEqual(
                    epub.read("OEBPS/xhtml/front/chapter.xhtml"),
                    b"<html><body>Front matter</body></html>",
                )
                self.assertEqual(
                    epub.read("OEBPS/xhtml/main/chapter.xhtml"),
                    b"<html><body>Main chapter</body></html>",
                )
                self.assertEqual(
                    epub.read("OEBPS/xhtml/Chapter Ü.xhtml"),
                    b"<html><body>Encoded chapter</body></html>",
                )

    def test_pdf_sanitization_recurses_into_nested_spine_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clean_dir = Path(temp_dir)
            chapter = clean_dir / "xhtml" / "front" / "chapter.xhtml"
            chapter.parent.mkdir(parents=True)
            chapter.write_text(
                "<html><head></head><body><script>remove()</script><p>Keep</p></body></html>",
                encoding="utf-8",
            )

            build_pdf.sanitize_xhtml_tree(str(clean_dir), "@page { size: A4; }")

            result = chapter.read_text(encoding="utf-8")
            self.assertNotIn("<script", result)
            self.assertIn("@page { size: A4; }", result)
            self.assertIn("<p>Keep</p>", result)


if __name__ == "__main__":
    unittest.main()
