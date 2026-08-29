#!/usr/bin/env python3
"""
Sanitize captured reader XHTML for standalone use (EPUB packaging / printing).

Removes reader plumbing that must not run or apply outside the reader:

1. all <script> blocks (VST hooks, MathJax, Poptip) - the content is static
   HTML, no script is needed to display it;
2. print-blocking CSS - the captured page contains a <style media="print">
   rule that hides the whole body and shows "To print, please use the print
   page range feature within the application." (this is why printing the raw
   capture yields a single page with that message);
3. empty reader-injected divs left behind by the cleanup.

Use this only for books you are personally licensed to read.
"""
import re
import sys


SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
EMPTY_SCRIPT_RE = re.compile(r"<script\b[^>]*/\s*>", re.IGNORECASE)
STYLE_BLOCK_RE = re.compile(
    r"<style\b(?P<attributes>[^>]*)>(?P<content>.*?)</style\s*>",
    re.IGNORECASE | re.DOTALL,
)
STYLE_ATTRIBUTE_RE = re.compile(
    r"(?P<name>[^\s=/>]+)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
PRINT_GUARD_RE = re.compile(
    r"To\s+print,\s+please\s+use\s+the\s+print\s+page\s+range\s+feature\s+"
    r"within\s+the\s+application\.",
    re.IGNORECASE,
)


def _remove_blocked_style(match):
    attributes = STYLE_ATTRIBUTE_RE.finditer(match.group("attributes"))
    if any(
        attribute.group("name").lower() == "media"
        and attribute.group("value").strip().lower() == "print"
        for attribute in attributes
    ):
        return ""
    if PRINT_GUARD_RE.search(match.group("content")):
        return ""
    return match.group(0)


def sanitize(html):
    """Return a standalone version of the captured chapter XHTML."""
    # 1. strip all script blocks
    html = EMPTY_SCRIPT_RE.sub("", html)
    html = SCRIPT_BLOCK_RE.sub("", html)
    # 2. inspect each style independently and strip print protection
    html = STYLE_BLOCK_RE.sub(_remove_blocked_style, html)
    # 3. remove empty anchors/divs left over
    html = re.sub(r"<div\b[^>]*>\s*</div\s*>", "", html, flags=re.IGNORECASE)
    return html

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    html = open(src, encoding="utf-8").read()
    out = sanitize(html)
    open(dst, "w", encoding="utf-8").write(out)
    print(f"{src}: {len(html)} -> {dst}: {len(out)}")
