import tempfile
import unittest
from pathlib import Path

from scripts.merge_pdf import build_outline


class BuildOutlineTests(unittest.TestCase):
    def test_parses_namespaced_nested_navpoints_and_xml_entities(self):
        ncx = """\
<?xml version="1.0" encoding="utf-8"?>
<ncx:ncx xmlns:ncx="http://www.daisy.org/z3986/2005/ncx/">
  <ncx:navMap>
    <ncx:navPoint id="part">
      <ncx:navLabel><ncx:text>Part &amp; One</ncx:text></ncx:navLabel>
      <ncx:content playOrder="1" src="text/part.xhtml#start" />
      <ncx:navPoint id="section">
        <ncx:navLabel><ncx:text>Überblick</ncx:text></ncx:navLabel>
        <ncx:content src="text/section.xhtml" playOrder="2" />
      </ncx:navPoint>
    </ncx:navPoint>
  </ncx:navMap>
</ncx:ncx>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "toc.ncx")
            path.write_text(ncx, encoding="utf-8")

            outline = build_outline(path)

        self.assertEqual(
            outline,
            [
                (0, "Part & One", "text/part.xhtml#start"),
                (1, "Überblick", "text/section.xhtml"),
            ],
        )

    def test_normalizes_legacy_nonbreaking_spaces(self):
        ncx = """\
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint><navLabel><text>A&nbsp;B</text></navLabel><content src="a.xhtml" /></navPoint>
    <navPoint><navLabel><text>C&#160;D</text></navLabel><content src="b.xhtml" /></navPoint>
  </navMap>
</ncx>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "toc.ncx")
            path.write_text(ncx, encoding="utf-8")

            outline = build_outline(path)

        self.assertEqual(outline, [(0, "A B", "a.xhtml"), (0, "C D", "b.xhtml")])

    def test_skips_incomplete_entry_but_keeps_its_children(self):
        ncx = """\
<ncx><navMap><navPoint>
  <navLabel><text>Missing target</text></navLabel><content src="" />
  <navPoint><navLabel><text>Child</text></navLabel><content src="child.xhtml" /></navPoint>
</navPoint></navMap></ncx>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "toc.ncx")
            path.write_text(ncx, encoding="utf-8")

            outline = build_outline(path)

        self.assertEqual(outline, [(0, "Child", "child.xhtml")])

    def test_promotes_children_of_omitted_navpoints_to_nearest_valid_parent(self):
        ncx = """\
<ncx><navMap><navPoint>
  <navLabel><text>A</text></navLabel><content src="a.xhtml" />
  <navPoint>
    <navLabel><text>B</text></navLabel><content src="b.xhtml" />
    <navPoint><navLabel><text>C</text></navLabel><content src="c.xhtml" /></navPoint>
  </navPoint>
  <navPoint>
    <navLabel><text>Omitted</text></navLabel><content src="" />
    <navPoint><navLabel><text>D</text></navLabel><content src="d.xhtml" /></navPoint>
  </navPoint>
</navPoint></navMap></ncx>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "toc.ncx")
            path.write_text(ncx, encoding="utf-8")

            outline = build_outline(path)

        self.assertEqual(
            outline,
            [
                (0, "A", "a.xhtml"),
                (1, "B", "b.xhtml"),
                (2, "C", "c.xhtml"),
                (1, "D", "d.xhtml"),
            ],
        )

    def test_recovers_entries_from_malformed_legacy_ncx(self):
        ncx = """\
<ncx><navMap><navPoint><navLabel><text>Entry</text></navLabel>
<content src="a.xhtml"></navPoint></navMap></ncx>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "toc.ncx")
            path.write_text(ncx, encoding="utf-8")

            outline = build_outline(path)

        self.assertEqual(outline, [(0, "Entry", "a.xhtml")])


if __name__ == "__main__":
    unittest.main()
