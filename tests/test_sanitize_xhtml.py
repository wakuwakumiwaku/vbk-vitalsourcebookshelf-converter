import unittest

from scripts.sanitize_xhtml import sanitize


class SanitizeXhtmlTests(unittest.TestCase):
    def test_removes_only_individually_blocked_style_elements(self):
        html = """\
<head>
  <style id="safe-before">.chapter { color: black; }</style>
  <STYLE type="text/css" data-reader="true" MEDIA = 'PRINT'>body { display: none; }</sTyLe>
  <style media="print" id="print-first">.message { display: block; }</style>
  <style id="safe-middle">.note { display: none; }</style>
  <style data-media="print" id="safe-data-attribute">.caption { color: navy; }</style>
  <style title="safe media='print' marker" id="safe-attribute-value">.caption { color: teal; }</style>
  <style id="safe-hidden-utility">.visually-hidden { display: none !important; }</style>
  <style id="safe-string-literal">.note::before { content: "display: none !important"; }</style>
  <Style>
    body &gt; * { DISPLAY : none !IMPORTANT; }
    body:before {
      content: "To print, please use the print page range feature within the application.";
    }
  </STYLE>
  <style id="safe-after">.figure { display: block; }</style>
</head>
"""

        result = sanitize(html)

        self.assertIn('id="safe-before"', result)
        self.assertIn('id="safe-middle"', result)
        self.assertIn('id="safe-data-attribute"', result)
        self.assertIn('id="safe-attribute-value"', result)
        self.assertIn('id="safe-hidden-utility"', result)
        self.assertIn('id="safe-string-literal"', result)
        self.assertIn('id="safe-after"', result)
        self.assertNotIn("data-reader", result)
        self.assertNotIn("print-first", result)
        self.assertNotIn("!IMPORTANT", result)

    def test_removes_mixed_case_script_elements(self):
        html = """\
<body>
  <ScRiPt type="text/javascript">window.readerHook = true;</sCrIpT>
  <SCRIPT src="reader.js" />
  <p>Keep this chapter text.</p>
</body>
"""

        result = sanitize(html)

        self.assertNotIn("readerHook", result)
        self.assertNotIn("reader.js", result)
        self.assertIn("<p>Keep this chapter text.</p>", result)

    def test_removes_mixed_case_empty_divs(self):
        result = sanitize('<body><DIV class="reader">  </dIv><p>Keep.</p></body>')

        self.assertNotIn("reader", result)
        self.assertIn("<p>Keep.</p>", result)


if __name__ == "__main__":
    unittest.main()
