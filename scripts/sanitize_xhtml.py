#!/usr/bin/env python3
"""
Sanitize captured reader XHTML for standalone use (EPUB packaging / printing).

Removes reader plumbing that must not run or apply outside the licensed app:

1. all <script> blocks (VST hooks, MathJax, Poptip) - the content is static
   HTML, no script is needed to display it;
2. print-blocking CSS - the captured page contains a <style media="print">
   rule that hides the whole body and shows "To print, please use the print
   page range feature within the application." (this is why printing the raw
   capture yields a single page with that message);
3. reader-injected UI wrappers (vst-ignore / vst-skip classes) that contain
   no real content.

PERSONAL AUTHORIZED ARCHIVING ONLY: use only with content you are
personally authorized to read.
"""
import re
import sys

def sanitize(html):
    """Return a standalone version of the captured chapter XHTML."""
    # 1. strip all script blocks
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<script[^>]*/>", "", html)
    # 2. strip print-media style blocks (print-protection)
    html = re.sub(
        r"<style[^>]*media=[\"']print[\"'][^>]*>.*?</style>",
        "", html, flags=re.S)
    # 3. strip any style containing the print-block content guard
    html = re.sub(
        r"<style[^>]*>.*?display:\s*none\s*!important.*?</style>",
        "", html, flags=re.S)
    # 4. remove vst-ignore / vst-skip wrapper elements that contain no real
    #    content (reader chrome). Keep anything with real text/images.
    def keep_el(m):
        el = m.group(0)
        # keep if it has meaningful content
        text = re.sub(r"<[^>]+>", "", el)
        if len(text.strip()) >= 3 or "<img" in el:
            # unwrap: return inner content
            inner = re.sub(r"^<[^>]+>", "", el)
            inner = re.sub(r"</?[^>]+>$", "", inner)
            return inner
        return ""
    # 5. remove empty anchors/divs left over
    html = re.sub(r"<div[^>]*>\s*</div>", "", html)
    return html

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    html = open(src, encoding="utf-8").read()
    out = sanitize(html)
    open(dst, "w", encoding="utf-8").write(out)
    print(f"{src}: {len(html)} -> {dst}: {len(out)}")
