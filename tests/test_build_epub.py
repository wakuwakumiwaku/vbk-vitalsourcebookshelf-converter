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
from scripts.opf_parser import package_relative_path, parse_opf


class BuildEpubTests(unittest.TestCase):
    def _build_synthetic_epub(self, root, chapters):
        out_root = root / "source"
        oebps = out_root / "OEBPS"
        build = root / "build"
        epub_out = root / "book.epub"
        package = ET.Element("package", xmlns="http://www.idpf.org/2007/opf")
        manifest = ET.SubElement(package, "manifest")
        spine = ET.SubElement(package, "spine")
        for index, (rel, href) in enumerate(chapters):
            source = oebps / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                f"<html><body>Chapter {index}</body></html>", encoding="utf-8"
            )
            item_id = f"chapter{index}"
            ET.SubElement(manifest, "item", {
                "id": item_id, "href": href, "media-type": "application/xhtml+xml"
            })
            ET.SubElement(spine, "itemref", idref=item_id)
        ET.ElementTree(package).write(out_root / "opf.xml", encoding="utf-8")
        (out_root / "ncx.xml").write_text(
            '<?xml version="1.0"?><ncx><navMap /></ncx>', encoding="utf-8"
        )
        output = io.StringIO()
        with (
            mock.patch.multiple(
                build_epub, OUT_ROOT=str(out_root), OEBPS=str(oebps), BUILD=str(build)
            ),
            mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(epub_out)]),
            redirect_stdout(output),
        ):
            build_epub.main()
        return epub_out, output.getvalue()

    def test_manifest_hrefs_round_trip_special_filenames(self):
        cases = [
            ("xhtml/space name.xhtml", "xhtml/space%20name.xhtml"),
            ("xhtml/amp&name.xhtml", "xhtml/amp%26name.xhtml"),
            ('xhtml/quotes"and\'name.xhtml', "xhtml/quotes%22and%27name.xhtml"),
            ("xhtml/<chapter>.xhtml", "xhtml/%3Cchapter%3E.xhtml"),
            ("xhtml/chapter#part?.xhtml", "xhtml/chapter%23part%3F.xhtml"),
            ("xhtml/100%done.xhtml", "xhtml/100%25done.xhtml"),
            ("xhtml/literal%20%2F%2e%2e.xhtml", "xhtml/literal%2520%252F%252e%252e.xhtml"),
            ("sections Ü/chapter.xhtml", "sections%20%C3%9C/chapter.xhtml"),
            ("chapter:one.xhtml", "chapter%3Aone.xhtml"),
        ]
        for rel, href in cases:
            with self.subTest(filename=rel), tempfile.TemporaryDirectory() as temp_dir:
                # Publisher order deliberately differs from sorted manifest order.
                chapters = [(rel, href), ("a-first.xhtml", "a-first.xhtml")]
                epub_out, _ = self._build_synthetic_epub(Path(temp_dir), chapters)
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertIsNone(epub.testzip())
                    self.assertEqual(epub.infolist()[0].filename, "mimetype")
                    self.assertEqual(epub.infolist()[0].compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(epub.read("mimetype"), b"application/epub+zip")
                    for index, (filename, _) in enumerate(chapters):
                        self.assertEqual(
                            epub.read(f"OEBPS/{filename}"),
                            f"<html><body>Chapter {index}</body></html>".encode("utf-8"),
                        )
                    opf = epub.read("OEBPS/content.opf")
                    # parse_opf has a permissive HTML fallback: require real XML too.
                    ET.fromstring(opf)
                    manifest, spine = parse_opf(io.BytesIO(opf))
                    self.assertEqual(
                        {item[0] for item in manifest.values()},
                        {href, "a-first.xhtml", "toc.ncx"},
                    )
                    self.assertEqual([item["href"] for item in spine], [href, "a-first.xhtml"])
                    self.assertEqual(
                        [package_relative_path(item["href"]).as_posix() for item in spine],
                        [rel, "a-first.xhtml"],
                    )
                    for item_href, _ in manifest.values():
                        member = "OEBPS/" + package_relative_path(item_href).as_posix()
                        self.assertIn(member, epub.namelist())

    def test_verification_reports_no_missing_members_for_complete_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_out, output = self._build_synthetic_epub(
                Path(temp_dir), [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")]
            )
            with zipfile.ZipFile(epub_out) as epub:
                self.assertIn("OEBPS/xhtml/chapter.xhtml", epub.namelist())
                self.assertIn("OEBPS/toc.ncx", epub.namelist())
                self.assertIn("OEBPS/content.opf", epub.namelist())
            self.assertIn("missing shipped files in zip: 0\n", output)

    def test_verification_detects_omitted_archive_members(self):
        original_write = zipfile.ZipFile.write
        for omitted in ("xhtml/chapter.xhtml", "toc.ncx", "content.opf"):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as temp_dir:
                def write_except(archive, filename, arcname, **kwargs):
                    if Path(arcname).as_posix() != f"OEBPS/{omitted}":
                        return original_write(archive, filename, arcname, **kwargs)

                # Omit a real member on write, not merely from the verification listing.
                with mock.patch.object(zipfile.ZipFile, "write", new=write_except):
                    epub_out, output = self._build_synthetic_epub(
                        Path(temp_dir), [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")]
                    )
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertNotIn(f"OEBPS/{omitted}", epub.namelist())
                self.assertIn(f"missing shipped files in zip: 1\n   {omitted}\n", output)

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
