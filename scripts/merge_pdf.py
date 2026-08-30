#!/usr/bin/env python3
"""
Merge the per-spine PDF parts (from build_pdf.py) into one book PDF with
NCX-based bookmarks.

Use this only for books you are personally licensed to read; keep the output
for your own offline study. No DRM removal, no key extraction.

How it works
------------
1. loads the publisher spine order from content.opf;
2. merges every existing per-spine PDF from pdf_parts/ in spine order,
   recording how many pages each part contributed;
3. parses toc.ncx navPoints (with nesting depth) into an outline;
4. maps each navPoint's href to its spine item -> PDF page offset;
5. writes the merged PDF with pypdf's add_outline_item, preserving the
   publisher's chapter/section hierarchy.
"""
import json
import sys
import os
import argparse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pypdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opf_parser import parse_opf

OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # working dir from build_pdf.py
PDF_DIR = os.path.join(OUT_ROOT, "pdf_parts")
FINAL_PDF = "/mnt/h/WSL/vitalsource/Neuroanatomie_9._Auflage_Trepel_offline.pdf"

def load_spine():
    _, spine = parse_opf(os.path.join(OUT_ROOT, "opf.xml"))
    return spine

def _local_name(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _outline_label(text):
    return text.replace("\xa0", " ").strip()


class _FallbackNcxParser(HTMLParser):
    """Best-effort NCX reader for malformed legacy captures."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.points = []
        self.sequence = 0

    def handle_starttag(self, tag, attrs):
        name = _local_name(tag).lower()
        if name == "navpoint":
            point = {
                "parent": self.stack[-1] if self.stack else None,
                "sequence": self.sequence,
                "label": [],
                "has_text": False,
                "text_depth": 0,
                "href": None,
            }
            self.points.append(point)
            self.stack.append(point)
            self.sequence += 1
        elif name == "text" and self.stack:
            self.stack[-1]["has_text"] = True
            self.stack[-1]["text_depth"] += 1
        elif name == "content" and self.stack:
            attributes = {_local_name(key).lower(): value for key, value in attrs}
            self.stack[-1]["href"] = attributes.get("src")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and self.stack[-1]["text_depth"]:
            self.stack[-1]["label"].append(data)

    def handle_endtag(self, tag):
        name = _local_name(tag).lower()
        if name == "text" and self.stack:
            self.stack[-1]["text_depth"] = max(0, self.stack[-1]["text_depth"] - 1)
        elif name == "navpoint" and self.stack:
            self.stack.pop()

    def close(self):
        super().close()
        self.stack.clear()

    def outline(self):
        outline = []
        for point in self.points:
            href = point["href"]
            if not point["has_text"] or not href:
                continue
            depth = 0
            parent = point["parent"]
            while parent is not None:
                if parent["has_text"] and parent["href"]:
                    depth += 1
                parent = parent["parent"]
            label = _outline_label("".join(point["label"]))
            outline.append((depth, label, href))
        return outline


def build_outline(ncx_path):
    """Parse NCX navPoints into nested bookmark entries with page targets.

    Returns list of (level, label, href) sorted by doc order.
    """
    with open(ncx_path, encoding="utf-8") as source:
        ncx = source.read().replace("&nbsp;", "&#160;")
    try:
        root = ET.fromstring(ncx)
    except ET.ParseError:
        parser = _FallbackNcxParser()
        parser.feed(ncx)
        parser.close()
        return parser.outline()

    def child_named(parent, name):
        return next(
            (child for child in parent if _local_name(child.tag) == name),
            None,
        )

    nav_map = next(
        (element for element in root.iter() if _local_name(element.tag) == "navMap"),
        None,
    )
    if nav_map is None:
        return []

    outline = []

    def add_navpoints(parent, depth):
        for nav_point in parent:
            if _local_name(nav_point.tag) != "navPoint":
                continue

            nav_label = child_named(nav_point, "navLabel")
            content = child_named(nav_point, "content")
            text = child_named(nav_label, "text") if nav_label is not None else None
            href = content.get("src") if content is not None else None
            child_depth = depth
            if text is not None and href:
                label = _outline_label("".join(text.itertext()))
                outline.append((depth, label, href))
                child_depth += 1

            add_navpoints(nav_point, child_depth)

    add_navpoints(nav_map, 0)
    return outline

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=FINAL_PDF)
    args = ap.parse_args()

    spine = load_spine()
    parts = []
    missing = []
    for item in spine:
        name = os.path.basename(item["href"]).replace(".xhtml", "")
        p = os.path.join(PDF_DIR, f"{item['index']:03d}_{name}.pdf")
        if os.path.exists(p) and os.path.getsize(p) > 2000:
            parts.append((item, p))
        else:
            missing.append((item["index"], item["href"]))

    print(f"parts found: {len(parts)}/{len(spine)}")
    if missing:
        print("MISSING:")
        for idx, href in missing:
            print(f"  [{idx}] {href}")

    writer = pypdf.PdfWriter()
    # First pass: add all pages, record page count per part
    part_pages = []
    for item, p in parts:
        r = pypdf.PdfReader(p)
        n = len(r.pages)
        for page in r.pages:
            writer.add_page(page)
        part_pages.append((item, n))
        print(f"  merged [{item['index']}] {os.path.basename(p)} pages={n}")

    total = len(writer.pages)
    print(f"total pages: {total}")

    # Build outline: NCX navpoints -> bookmark pages
    # page offset per spine index
    offset = {}
    cum = 0
    for item, n in part_pages:
        offset[item["index"]] = cum
        cum += n

    # map href -> spine index
    href_index = {}
    for item in spine:
        href_index[item["href"]] = item["index"]
        href_index[item["href"].split("#")[0]] = item["index"]

    # parse NCX navPoints with levels
    parents = []
    for depth, label, href in build_outline(os.path.join(OUT_ROOT, "ncx.xml")):
        parents = parents[:depth]
        if len(parents) < depth:
            parents.extend([None] * (depth - len(parents)))

        base = href.split("#")[0]
        idx = href_index.get(base)
        ref = None
        if idx is not None and idx in offset:
            page_num = offset[idx]
            parent = next(
                (candidate for candidate in reversed(parents) if candidate is not None),
                None,
            )
            try:
                ref = writer.add_outline_item(label, page_num, parent=parent)
            except Exception as e:
                print(f"outline error for '{label}': {e}")
        else:
            print(f"  no page for navpoint '{label}' -> {href}")
        parents.append(ref)

    with open(args.out, "wb") as f:
        writer.write(f)
    sz = os.path.getsize(args.out)
    print(f"\nWROTE {args.out} ({sz/1e6:.1f} MB, {total} pages)")

if __name__ == "__main__":
    main()
