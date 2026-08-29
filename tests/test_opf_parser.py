import tempfile
import unittest
from pathlib import Path

from scripts.opf_parser import parse_opf


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


if __name__ == "__main__":
    unittest.main()
