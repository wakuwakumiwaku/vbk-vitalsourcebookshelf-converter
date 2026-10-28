"""Synthetic regression coverage for fragment targets in captured chapters."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from scripts import build_epub, build_pdf, sanitize_xhtml


class _Targets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for name, value in attrs if name in {"id", "xml:id"})


def _targets(html):
    parser = _Targets()
    parser.feed(html)
    parser.close()
    return parser.ids


class NavigationTargetTests(unittest.TestCase):
    def test_keeps_empty_div_targets_without_rewriting_markup(self):
        elements = [
            '<div id="section"></div>',
            "<div class='reader' id = 'section'> \n </div>",
            '<DIV ID="section">\t</dIv>',
            '<div xml:id="section"></div>',
            '<div id=section></div>',
            '<div id=""></div>',
            '<div data-id="unrelated" id="section"></div>',
            '<div title="See > section" id="section"></div>',
            '<div id="section" title="See > section"></div>',
            '<div title="not an id=\'fake\'" id="section"></div>',
            '<div id="section" title="not an id=\'fake\'"></div>',
            '<div id="überblick"></div>',
        ]
        for element in elements:
            with self.subTest(element=element):
                html = f"<body>{element}<p>Chapter text.</p></body>"
                self.assertEqual(sanitize_xhtml.sanitize(html), html)

    def test_keeps_targets_inside_nested_containers(self):
        html = '<body><div class="section"><div id="target"> </div></div></body>'
        self.assertEqual(sanitize_xhtml.sanitize(html), html)

    def test_still_removes_untargeted_empty_placeholders(self):
        elements = [
            '<div></div>',
            '<DIV class="reader"> \n </dIv>',
            '<div data-id="not-a-target"></div>',
            '<div title="id=\'not-a-target\'"></div>',
            '<div title="not-a-target" data-id="id"></div>',
            '<div title="See > section"></div>',
        ]
        for element in elements:
            with self.subTest(element=element):
                self.assertEqual(sanitize_xhtml.sanitize(element), "")

    def test_target_retention_does_not_restore_reader_plumbing(self):
        html = (
            '<html><head><script>readerHook()</script>'
            '<style media="print">body { display: none; }</style>'
            '<style>.chapter { color: navy; }</style></head><body>'
            '<div class="reader"></div><div id="target"></div>'
            '<p>Chapter text.</p></body></html>'
        )
        result = sanitize_xhtml.sanitize(html)
        self.assertIn("target", _targets(result))
        self.assertNotIn("readerHook", result)
        self.assertNotIn("display: none", result)
        self.assertNotIn('class="reader"', result)
        self.assertIn('<style>.chapter { color: navy; }</style>', result)
        self.assertEqual(sanitize_xhtml.sanitize(result), result)

    def test_epub_links_and_ncx_still_resolve_after_sanitization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "capture"
            oebps = source / "OEBPS"
            (oebps / "text").mkdir(parents=True)
            chapters = {
                "text/one.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                    '<script>readerHook()</script></head><body>'
                    '<div id="start"></div><p>Chapter one.</p>'
                    '<a href="#start">Start</a>'
                    '<a href="two.xhtml#detail">Next section</a></body></html>'
                ),
                "text/two.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                    '<style media="print">body { display: none; }</style></head>'
                    '<body><div id="detail"> \n </div><p>Chapter two.</p>'
                    '<div class="reader"></div></body></html>'
                ),
            }
            package = ET.Element("package", xmlns="http://www.idpf.org/2007/opf")
            manifest = ET.SubElement(package, "manifest")
            spine = ET.SubElement(package, "spine")
            for index, (href, html) in enumerate(chapters.items()):
                (oebps / href).write_text(html, encoding="utf-8")
                ET.SubElement(manifest, "item", {
                    "id": f"chapter{index}", "href": href,
                    "media-type": "application/xhtml+xml",
                })
                ET.SubElement(spine, "itemref", idref=f"chapter{index}")
            ET.ElementTree(package).write(source / "opf.xml", encoding="utf-8")
            ncx = (
                '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
                '<navMap><navPoint id="one"><navLabel><text>One</text></navLabel>'
                '<content src="text/one.xhtml#start"/></navPoint>'
                '<navPoint id="two"><navLabel><text>Two</text></navLabel>'
                '<content src="text/two.xhtml#detail"/></navPoint></navMap></ncx>'
            ).encode("utf-8")
            (source / "ncx.xml").write_bytes(ncx)
            before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
            output = root / "book.epub"
            with (
                mock.patch.multiple(
                    build_epub, OUT_ROOT=str(source), OEBPS=str(oebps),
                    BUILD=str(root / "build"),
                ),
                mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(output)]),
                redirect_stdout(io.StringIO()),
            ):
                build_epub.main()

            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.infolist()[0].filename, "mimetype")
                self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertEqual(archive.read("OEBPS/toc.ncx"), ncx)
                ns = {"x": "http://www.w3.org/1999/xhtml", "n": "http://www.daisy.org/z3986/2005/ncx/"}
                for content in ET.fromstring(ncx).findall(".//n:content", ns):
                    target = urlsplit(content.attrib["src"])
                    html = archive.read("OEBPS/" + target.path).decode("utf-8")
                    with self.subTest(ncx_target=target.geturl()):
                        self.assertIn(target.fragment, _targets(html))
                for href in chapters:
                    html = archive.read("OEBPS/" + href).decode("utf-8")
                    tree = ET.fromstring(html)
                    self.assertEqual(tree.findall(".//x:script", ns), [])
                    self.assertNotIn('class="reader"', html)
                    self.assertNotIn("display: none", html)
                    for link in tree.findall(".//x:a", ns):
                        target = urlsplit(link.attrib["href"])
                        target_path = (Path(href).parent / target.path).as_posix() if target.path else href
                        target_html = archive.read("OEBPS/" + target_path).decode("utf-8")
                        with self.subTest(chapter=href, link=target.geturl()):
                            self.assertIn(target.fragment, _targets(target_html))
            self.assertEqual(
                {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()},
                before,
            )

    def test_pdf_tree_preparation_preserves_navigation_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = root / "text" / "chapter.xhtml"
            chapter.parent.mkdir()
            chapter.write_text(
                '<html><head><script>readerHook()</script></head><body>'
                '<div id="section"></div><a href="#section">Section</a>'
                '<div class="reader"></div></body></html>', encoding="utf-8",
            )
            css = "p { color: navy; }"
            build_pdf.sanitize_xhtml_tree(str(root), css)
            html = chapter.read_text(encoding="utf-8")
            self.assertIn("section", _targets(html))
            self.assertIn('href="#section"', html)
            self.assertIn(css, html)
            self.assertNotIn("readerHook", html)
            self.assertNotIn('class="reader"', html)

    def test_standalone_cli_preserves_navigation_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "chapter.xhtml"
            destination = root / "clean.xhtml"
            html = '<html><body><div id="section"></div><p>Chapter.</p></body></html>'
            source.write_text(html, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(Path(sanitize_xhtml.__file__).resolve()), str(source), str(destination)],
                cwd=root, env=os.environ.copy(), capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), html)
            self.assertEqual(source.read_text(encoding="utf-8"), html)

    def test_standalone_cli_missing_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "clean.xhtml"
            result = subprocess.run(
                [sys.executable, "-B", str(Path(sanitize_xhtml.__file__).resolve()), str(root / "missing.xhtml"), str(destination)],
                cwd=root, env=os.environ.copy(), capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FileNotFoundError", result.stderr)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
