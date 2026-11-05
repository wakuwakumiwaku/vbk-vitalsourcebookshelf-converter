import tempfile
import unittest
from pathlib import Path

from scripts.opf_parser import parse_opf


class DuplicateManifestIdTests(unittest.TestCase):
    def parse(self, manifest, rendered=False):
        package = (
            '<package><manifest>' + manifest + '</manifest><spine>'
            '<itemref idref="chapter"/></spine></package>'
        )
        if rendered:
            package = '<html><head><meta charset=utf-8></head><body>' + package + '</body></html>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content.opf"
            path.write_text(package, encoding="utf-8")
            return parse_opf(path)

    def test_duplicate_ids_cannot_silently_redirect_the_spine(self):
        for rendered in (False, True):
            for second in (
                '<item id="chapter" href="b.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="chapter" href="a.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="chapter" href="b.xhtml"/>',
            ):
                with self.subTest(rendered=rendered, second=second):
                    with self.assertRaisesRegex(ValueError, "[Dd]uplicate.*chapter"):
                        self.parse(
                            '<item id="chapter" href="a.xhtml" '
                            'media-type="application/xhtml+xml"/>' + second,
                            rendered=rendered,
                        )

    def test_distinct_ids_can_reference_the_same_file(self):
        manifest, spine = self.parse(
            '<item id="chapter" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="alias" href="a.xhtml" media-type="application/xhtml+xml"/>'
        )
        self.assertEqual(set(manifest), {"chapter", "alias"})
        self.assertEqual(spine[0]["idref"], "chapter")


if __name__ == "__main__":
    unittest.main()
