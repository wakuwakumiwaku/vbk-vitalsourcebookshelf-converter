import io
import os
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import build_epub


class EpubMediaTypeTests(unittest.TestCase):
    def test_import_provenance(self):
        if root := os.environ.get("EXPECTED_SOURCE_ROOT"):
            self.assertTrue(Path(build_epub.__file__).resolve().is_relative_to(Path(root).resolve()))

    def test_original_media_types_survive_nonstandard_filename_extensions(self):
        for chapter, figure in (("chapter.xhtml", "figure.svg"),
                                ("chapter.html", "figure.dat"),
                                ("chapter.XHTML", "figure.dat"),
                                ("chapter%20one.bin", "figure.dat")):
            with self.subTest(chapter=chapter), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source"
                oebps = source / "OEBPS"
                oebps.mkdir(parents=True)
                from urllib.parse import unquote
                (oebps / unquote(chapter)).write_text('<html><body><p>Text</p></body></html>')
                (oebps / figure).write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')
                (oebps / "unlisted.css").write_text("p { color: blue; }")
                # The unlisted reference still needs the existing extension fallback.
                with (oebps / unquote(chapter)).open("w") as handle:
                    handle.write('<html><head><link href="unlisted.css"/></head><body>Text</body></html>')
                (source / "opf.xml").write_text(
                    '<package><manifest>'
                    f'<item id="chapter" href="{chapter}" media-type="application/xhtml+xml"/>'
                    f'<item id="figure" href="{figure}" media-type="image/svg+xml"/>'
                    '</manifest><spine><itemref idref="chapter"/></spine></package>'
                )
                (source / "ncx.xml").write_text('<ncx><navMap/></ncx>')
                output = root / "book.epub"
                with (mock.patch.multiple(build_epub, OUT_ROOT=str(source), OEBPS=str(oebps),
                                          BUILD=str(root / "build")),
                      mock.patch.object(sys, "argv", ["build_epub", "--out", str(output)]),
                      redirect_stdout(io.StringIO())):
                    build_epub.main()
                with zipfile.ZipFile(output) as archive:
                    package = ET.fromstring(archive.read("OEBPS/content.opf"))
                    items = {item.get("href"): item.get("media-type")
                             for item in package.findall("{*}manifest/{*}item")}
                    self.assertEqual(items[chapter], "application/xhtml+xml")
                    self.assertEqual(items[figure], "image/svg+xml")
                    self.assertEqual(items["unlisted.css"], "text/css")
                    self.assertEqual(archive.read(f"OEBPS/{figure}"), b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')


if __name__ == "__main__":
    unittest.main()
