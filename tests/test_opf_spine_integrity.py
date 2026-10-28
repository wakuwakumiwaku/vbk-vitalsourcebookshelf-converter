"""Synthetic-only regressions for declared OPF spine referential integrity."""
import io
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from scripts import build_epub
from scripts.opf_parser import parse_opf


NS = {"opf": "http://www.idpf.org/2007/opf"}
CHAPTERS = (
    ("front", "z/front.xhtml"),
    ("middle", "sections/Chapter%20%C3%9C.xhtml"),
    ("end", "a/end.xhtml"),
)


def package_text(defect=None, rendered=False, repeated=False):
    package = ET.Element("package", xmlns=NS["opf"])
    manifest = ET.SubElement(package, "manifest")
    spine = ET.SubElement(package, "spine")
    for item_id, href in CHAPTERS:
        attrs = {"media-type": "application/xhtml+xml", "href": href, "id": item_id}
        if defect == "incomplete-manifest" and item_id == "middle":
            del attrs["media-type"]
        ET.SubElement(manifest, "item", attrs)
        ref = {"idref": item_id}
        if item_id == "middle":
            if defect == "unknown":
                ref["idref"] = "not-declared"
            elif defect == "absent":
                ref = {}
            elif defect == "empty":
                ref["idref"] = ""
        if defect == "all-unresolved" or (defect == "unknown-first" and item_id == "front"):
            ref["idref"] = "not-declared"
        ET.SubElement(spine, "itemref", ref)
    ET.SubElement(manifest, "item", {
        "id": "optional-css", "href": "styles/optional.css", "media-type": "text/css",
    })
    if repeated:
        ET.SubElement(spine, "ignored-extension")
        ET.SubElement(spine, "itemref", idref="middle", linear="no")
    text = ET.tostring(package, encoding="unicode")
    if rendered:
        # The processing instruction inside HTML forces the real HTML fallback.
        text = '<!DOCTYPE html><html><body><?xml version="1.0"?>' + text + '</body></html>'
    return text


class SpineParserIntegrityTests(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "opf.xml"
            path.write_text(text, encoding="utf-8")
            before = path.read_bytes()
            try:
                return parse_opf(path)
            finally:
                self.assertEqual(path.read_bytes(), before)

    def _assert_invalid(self, defect, index=1):
        for rendered in (False, True):
            with self.subTest(rendered=rendered):
                with self.assertRaisesRegex(ValueError, rf"OPF spine.*index {index}") as caught:
                    self._parse(package_text(defect, rendered))
                self.assertIn("idref", str(caught.exception))
                if defect in ("unknown", "unknown-first", "all-unresolved"):
                    self.assertIn("not-declared", str(caught.exception))
                elif defect == "incomplete-manifest":
                    self.assertIn("middle", str(caught.exception))

    def test_unknown_spine_idref_is_not_silently_omitted(self):
        self._assert_invalid("unknown")

    def test_absent_spine_idref_is_not_silently_filtered(self):
        self._assert_invalid("absent")

    def test_empty_spine_idref_is_not_silently_filtered(self):
        self._assert_invalid("empty")

    def test_spine_idref_to_incomplete_manifest_item_is_rejected(self):
        self._assert_invalid("incomplete-manifest")

    def test_unknown_first_reference_reports_original_index(self):
        self._assert_invalid("unknown-first", index=0)

    def test_all_unresolved_references_do_not_become_an_empty_spine(self):
        self._assert_invalid("all-unresolved", index=0)

    def test_valid_xml_and_rendered_packages_keep_order_indices_and_repeated_references(self):
        for rendered in (False, True):
            with self.subTest(rendered=rendered):
                manifest, spine = self._parse(package_text(rendered=rendered, repeated=True))
                self.assertEqual([item["idref"] for item in spine], ["front", "middle", "end", "middle"])
                self.assertEqual([item["index"] for item in spine], [0, 1, 2, 3])
                self.assertEqual([item["href"] for item in spine], [href for _, href in CHAPTERS] + [CHAPTERS[1][1]])
                self.assertEqual(manifest["optional-css"], ("styles/optional.css", "text/css"))


class SpineBuilderIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.oebps = self.source / "OEBPS"
        self.oebps.mkdir(parents=True)
        self.build = self.root / "build"
        self.build.mkdir()
        self.output = self.root / "book.epub"
        self.canary = self.root / "unrelated.canary"
        self.canary.write_bytes(b"unrelated synthetic file must not change\x00\xff")
        self.chapter_bytes = {}
        for index, (_, href) in enumerate(CHAPTERS):
            path = self.oebps / unquote(href)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Generated chapter {index}</p></body></html>'.encode()
            path.write_bytes(data)
            self.chapter_bytes["OEBPS/" + unquote(href)] = data
        (self.source / "ncx.xml").write_text("<ncx><navMap /></ncx>", encoding="utf-8")
        self._write_package()
        self._build_canary()

    def _write_package(self, defect=None, rendered=False):
        (self.source / "opf.xml").write_text(package_text(defect, rendered), encoding="utf-8")

    def _build_canary(self):
        (self.build / "previous-build.canary").write_bytes(b"previous generated build")

    def _snapshot(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def _run(self, output):
        with (
            mock.patch.multiple(build_epub, OUT_ROOT=str(self.source), OEBPS=str(self.oebps), BUILD=str(self.build)),
            mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(self.output)]),
            redirect_stdout(output),
        ):
            build_epub.main()

    def _assert_refused_before_cleanup(self):
        before = self._snapshot(self.root)
        output = io.StringIO()
        error = None
        with mock.patch.object(build_epub.shutil, "rmtree", wraps=build_epub.shutil.rmtree) as cleanup:
            try:
                self._run(output)
            except Exception as exc:
                error = exc
        self.assertIsInstance(error, ValueError, "invalid OPF spine was silently truncated and published")
        self.assertRegex(str(error), r"OPF spine.*index [01].*idref")
        self.assertEqual(cleanup.call_args_list, [], "invalid OPF reached destructive build cleanup")
        self.assertNotIn("WROTE", output.getvalue())
        self.assertEqual(self._snapshot(self.root), before)
        self.assertEqual(list(self.root.rglob(".epub-*")), [])

    def _assert_complete_epub(self):
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            first = archive.infolist()[0]
            self.assertEqual(first.filename, "mimetype")
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            ET.fromstring(archive.read("META-INF/container.xml"))
            ET.fromstring(archive.read("OEBPS/toc.ncx"))
            for member, data in self.chapter_bytes.items():
                self.assertEqual(archive.read(member), data)
                ET.fromstring(archive.read(member))
            package = ET.fromstring(archive.read("OEBPS/content.opf"))
            manifest = {item.attrib["id"]: item.attrib["href"] for item in package.findall("opf:manifest/opf:item", NS)}
            refs = package.findall("opf:spine/opf:itemref", NS)
            self.assertEqual([manifest[ref.attrib["idref"]] for ref in refs], [href for _, href in CHAPTERS])
            for href in manifest.values():
                self.assertIn("OEBPS/" + unquote(href), archive.namelist())
            self.assertNotIn("OEBPS/styles/optional.css", archive.namelist())

    def test_invalid_first_build_is_rejected_without_output_or_cleanup(self):
        for rendered in (False, True):
            for defect in ("unknown", "absent", "empty", "incomplete-manifest", "all-unresolved"):
                with self.subTest(rendered=rendered, defect=defect):
                    # OLD may publish; remove only this fixture's previous result.
                    self.output.unlink(missing_ok=True)
                    self._build_canary()
                    self._write_package(defect, rendered)
                    self._assert_refused_before_cleanup()
                    self.assertFalse(self.output.exists())

    def test_invalid_rebuild_preserves_previous_epub_build_source_and_canary(self):
        for rendered in (False, True):
            for defect in ("unknown", "absent", "empty", "incomplete-manifest", "all-unresolved"):
                with self.subTest(rendered=rendered, defect=defect):
                    self._write_package()
                    self._run(io.StringIO())
                    self._assert_complete_epub()
                    self._build_canary()
                    self._write_package(defect, rendered)
                    self._assert_refused_before_cleanup()
                    self._assert_complete_epub()

    def test_repaired_opf_retry_packages_every_chapter_in_original_order(self):
        for rendered in (False, True):
            with self.subTest(rendered=rendered):
                self._write_package("unknown", rendered)
                self._assert_refused_before_cleanup()
                self._write_package(rendered=rendered)
                source_before = self._snapshot(self.source)
                canary_before = self.canary.read_bytes()
                output = io.StringIO()
                self._run(output)
                self._assert_complete_epub()
                self.assertEqual(self._snapshot(self.source), source_before)
                self.assertEqual(self.canary.read_bytes(), canary_before)
                self.assertIn(f"WROTE {self.output}", output.getvalue())
                self.assertEqual(list(self.root.rglob(".epub-*")), [])

    def test_valid_complete_package_remains_supported_with_optional_unmirrored_asset(self):
        before = self._snapshot(self.source)
        self._run(io.StringIO())
        self._assert_complete_epub()
        self.assertEqual(self._snapshot(self.source), before)
        self.assertEqual(self.canary.read_bytes(), b"unrelated synthetic file must not change\x00\xff")


if __name__ == "__main__":
    unittest.main()
