import tempfile
import unittest
from pathlib import Path

from scripts.opf_parser import package_path, parse_opf


class ParseOpfTests(unittest.TestCase):
    def test_namespaced_opf_allows_reordered_attributes_and_missing_idrefs(self):
        opf = """\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item media-type="application/xhtml+xml" href="xhtml/one.xhtml" id="one" />
    <item href="xhtml/two.xhtml" id="two" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref linear="yes" idref="one" />
    <itemref />
    <itemref idref="missing" />
    <itemref properties="page-spread-right" idref="two" />
  </spine>
</package>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "content.opf")
            path.write_text(opf, encoding="utf-8")

            manifest, spine = parse_opf(path)

        self.assertEqual(
            manifest,
            {
                "one": ("xhtml/one.xhtml", "application/xhtml+xml"),
                "two": ("xhtml/two.xhtml", "application/xhtml+xml"),
            },
        )
        self.assertEqual(
            spine,
            [
                {
                    "index": 0,
                    "idref": "one",
                    "href": "xhtml/one.xhtml",
                    "media_type": "application/xhtml+xml",
                },
                {
                    "index": 2,
                    "idref": "two",
                    "href": "xhtml/two.xhtml",
                    "media_type": "application/xhtml+xml",
                },
            ],
        )

    def test_parses_package_saved_inside_rendered_html(self):
        captured = """\
<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<?xml version="1.0" encoding="utf-8"?>
<html><body>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter" href="xhtml/chapter.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine><itemref idref="chapter" /></spine>
</package>
</body></html>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "opf.xml")
            path.write_text(captured, encoding="utf-8")

            manifest, spine = parse_opf(path)

        self.assertEqual(
            manifest,
            {"chapter": ("xhtml/chapter.xhtml", "application/xhtml+xml")},
        )
        self.assertEqual([item["idref"] for item in spine], ["chapter"])

    def test_rejects_manifest_hrefs_outside_the_package(self):
        unsafe_hrefs = [
            "",
            "../../outside.css",
            "/tmp/outside.css",
            "https://example.com/outside.css",
            "//example.com/outside.css",
            "styles/%2e%2e/%2e%2e/outside.css",
            "styles\\..\\outside.css",
            "styles/%2e%2e%5coutside.css",
            "styles/book.css?download=1",
            "styles/book.css#fragment",
            "styles/book.css?",
            "styles/book.css#",
            ".",
            "./",
            "styles/",
            "styles/.",
            "styles/%2e",
            "styles//book.css",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "content.opf")
            for href in unsafe_hrefs:
                with self.subTest(href=href):
                    path.write_text(
                        f"""\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="unsafe" href="{href}" media-type="text/css" />
  </manifest>
  <spine />
</package>
""",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "unsafe OPF href"):
                        parse_opf(path)

    def test_package_path_stays_beneath_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir, "OEBPS")
            root.mkdir()

            self.assertEqual(
                package_path(root, "styles/book.css"),
                root / "styles" / "book.css",
            )

            outside = Path(temp_dir, "outside")
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe OPF href"):
                package_path(root, "linked/book.css")


if __name__ == "__main__":
    unittest.main()
