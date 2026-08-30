import io
import gc
import sys
import tempfile
import unittest
import warnings
import zipfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import build_epub


class BuildEpubTests(unittest.TestCase):
    def test_preserves_nested_spine_paths_with_duplicate_basenames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_root = root / "source"
            oebps = out_root / "OEBPS"
            build = root / "build"
            epub_out = root / "book.epub"

            front = oebps / "sections" / "front" / "chapter.xhtml"
            main = oebps / "sections" / "main" / "chapter.xhtml"
            front.parent.mkdir(parents=True)
            main.parent.mkdir(parents=True)
            front.write_text("<html><body>Front matter</body></html>", encoding="utf-8")
            main.write_text("<html><body>Main chapter</body></html>", encoding="utf-8")

            (out_root / "opf.xml").write_text(
                """\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="front" href="sections/front/chapter.xhtml" media-type="application/xhtml+xml" />
    <item id="main" href="sections/main/chapter.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="front" />
    <itemref idref="main" />
  </spine>
</package>
""",
                encoding="utf-8",
            )
            (out_root / "ncx.xml").write_text(
                '<?xml version="1.0"?><ncx><navMap /></ncx>',
                encoding="utf-8",
            )

            with warnings.catch_warnings(record=True) as captured_warnings:
                warnings.simplefilter("always", ResourceWarning)
                with (
                    mock.patch.multiple(
                        build_epub,
                        OUT_ROOT=str(out_root),
                        OEBPS=str(oebps),
                        BUILD=str(build),
                    ),
                    mock.patch.object(
                        sys, "argv", ["build_epub.py", "--out", str(epub_out)]
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    build_epub.main()
                gc.collect()

            self.assertFalse(
                [warning for warning in captured_warnings if warning.category is ResourceWarning]
            )

            with zipfile.ZipFile(epub_out) as epub:
                names = epub.namelist()
                self.assertIn("OEBPS/sections/front/chapter.xhtml", names)
                self.assertIn("OEBPS/sections/main/chapter.xhtml", names)
                self.assertEqual(
                    epub.read("OEBPS/sections/front/chapter.xhtml"),
                    b"<html><body>Front matter</body></html>",
                )
                self.assertEqual(
                    epub.read("OEBPS/sections/main/chapter.xhtml"),
                    b"<html><body>Main chapter</body></html>",
                )

                package = ET.fromstring(epub.read("OEBPS/content.opf"))

            manifest_hrefs = {
                item.get("href")
                for item in package.iter()
                if item.tag.rpartition("}")[2] == "item"
            }
            manifest_ids = {
                item.get("id"): item.get("href")
                for item in package.iter()
                if item.tag.rpartition("}")[2] == "item"
            }
            spine_hrefs = [
                manifest_ids[itemref.get("idref")]
                for itemref in package.iter()
                if itemref.tag.rpartition("}")[2] == "itemref"
            ]
            self.assertIn("sections/front/chapter.xhtml", manifest_hrefs)
            self.assertIn("sections/main/chapter.xhtml", manifest_hrefs)
            self.assertEqual(
                spine_hrefs,
                ["sections/front/chapter.xhtml", "sections/main/chapter.xhtml"],
            )


if __name__ == "__main__":
    unittest.main()
