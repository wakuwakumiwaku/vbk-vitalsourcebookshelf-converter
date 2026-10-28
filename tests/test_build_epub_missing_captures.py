import io
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from scripts import build_epub


class MissingCaptureTests(unittest.TestCase):
    chapters = (
        "xhtml/front.xhtml",
        "sections/Chapter%20%C3%9C.xhtml",
        "xhtml/end.xhtml",
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.oebps = self.source / "OEBPS"
        self.oebps.mkdir(parents=True)
        self.build = self.root / "build"
        self.build.mkdir()
        (self.build / "previous-build.txt").write_bytes(b"keep until input is complete")
        self.output = self.root / "book.epub"
        package = ET.Element("package", xmlns="http://www.idpf.org/2007/opf")
        manifest = ET.SubElement(package, "manifest")
        spine = ET.SubElement(package, "spine")
        for index, href in enumerate(self.chapters):
            ET.SubElement(manifest, "item", {
                "id": f"chapter{index}", "href": href,
                "media-type": "application/xhtml+xml",
            })
            ET.SubElement(spine, "itemref", idref=f"chapter{index}")
        # An optional unmirrored asset must not be confused with a missing capture.
        ET.SubElement(manifest, "item", {
            "id": "optional-css", "href": "styles/optional.css", "media-type": "text/css",
        })
        ET.ElementTree(package).write(self.source / "opf.xml", encoding="utf-8")
        (self.source / "ncx.xml").write_text("<ncx><navMap /></ncx>", encoding="utf-8")

    def _capture(self, index):
        path = self.oebps / unquote(self.chapters[index])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<html><body><p>Chapter {index}</p></body></html>", encoding="utf-8")
        return path

    def _snapshot(self, root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }

    def _run(self):
        output = io.StringIO()
        with (
            mock.patch.multiple(
                build_epub, OUT_ROOT=str(self.source), OEBPS=str(self.oebps),
                BUILD=str(self.build),
            ),
            mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(self.output)]),
            redirect_stdout(output),
        ):
            build_epub.main()
        return output.getvalue()

    def _refused_before_cleanup(self, missing):
        source_before = self._snapshot(self.source)
        build_before = self._snapshot(self.build)
        previous = self.output.read_bytes() if self.output.exists() else None
        error = None
        try:
            self._run()
        except Exception as exc:
            error = exc
        self.assertIsInstance(error, FileNotFoundError, "incomplete capture was not refused explicitly")
        self.assertIn("Missing captured spine files", str(error))
        for href in missing:
            self.assertIn(href, str(error))
        self.assertEqual(self._snapshot(self.source), source_before)
        self.assertEqual(self._snapshot(self.build), build_before)
        if previous is None:
            self.assertFalse(self.output.exists())
        else:
            self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(list(self.root.glob(".epub-*")), [])

    def _assert_complete_epub(self):
        with zipfile.ZipFile(self.output) as epub:
            self.assertIsNone(epub.testzip())
            self.assertEqual(epub.infolist()[0].filename, "mimetype")
            self.assertEqual(epub.infolist()[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(epub.read("mimetype"), b"application/epub+zip")
            for index, href in enumerate(self.chapters):
                self.assertEqual(
                    epub.read("OEBPS/" + unquote(href)),
                    f"<html><body><p>Chapter {index}</p></body></html>".encode("utf-8"),
                )
            ns = {"opf": "http://www.idpf.org/2007/opf"}
            package = ET.fromstring(epub.read("OEBPS/content.opf"))
            manifest = {
                item.get("id"): item.get("href")
                for item in package.findall("opf:manifest/opf:item", ns)
            }
            self.assertEqual(
                [manifest[item.get("idref")] for item in package.findall("opf:spine/opf:itemref", ns)],
                list(self.chapters),
            )
            self.assertNotIn("OEBPS/styles/optional.css", epub.namelist())

    def test_incomplete_first_build_is_rejected_before_cleanup(self):
        self._capture(0)
        self._capture(2)
        self._refused_before_cleanup([self.chapters[1]])

    def test_incomplete_rebuild_preserves_previous_epub_and_build(self):
        for index in range(len(self.chapters)):
            self._capture(index)
        self._run()
        self._assert_complete_epub()
        (self.oebps / unquote(self.chapters[1])).unlink()
        self._refused_before_cleanup([self.chapters[1]])
        self._assert_complete_epub()

    def test_all_missing_captures_are_reported_together(self):
        self._refused_before_cleanup(self.chapters)

    def test_capture_directory_is_rejected_before_cleanup(self):
        for index in (0, 2):
            self._capture(index)
        (self.oebps / unquote(self.chapters[1])).mkdir(parents=True)
        self._refused_before_cleanup([self.chapters[1]])

    def test_retry_after_restoring_capture_builds_complete_epub(self):
        for index in (0, 2):
            self._capture(index)
        self._refused_before_cleanup([self.chapters[1]])
        self._capture(1)
        output = self._run()
        self._assert_complete_epub()
        self.assertIn(f"WROTE {self.output}", output)
        self.assertEqual(list(self.root.glob(".epub-*")), [])

    def test_complete_capture_builds_with_unmirrored_optional_asset(self):
        for index in range(len(self.chapters)):
            self._capture(index)
        source_before = self._snapshot(self.source)
        output = self._run()
        self._assert_complete_epub()
        self.assertEqual(self._snapshot(self.source), source_before)
        self.assertIn("xhtml copied+sanitized: 3\n", output)
        self.assertIn("missing shipped files in zip: 0\n", output)


if __name__ == "__main__":
    unittest.main()
