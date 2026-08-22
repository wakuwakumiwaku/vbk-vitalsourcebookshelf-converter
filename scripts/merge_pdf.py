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
import re
import sys
import os
import argparse

import pypdf

OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # working dir from build_pdf.py
PDF_DIR = os.path.join(OUT_ROOT, "pdf_parts")
FINAL_PDF = "/mnt/h/WSL/vitalsource/Neuroanatomie_9._Auflage_Trepel_offline.pdf"

def load_spine():
    opf = open(os.path.join(OUT_ROOT, "opf.xml"), encoding="utf-8").read()
    items = {}
    for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"[^>]*\bmedia-type="([^"]+)"', opf):
        items[m.group(1)] = (m.group(2), m.group(3))
    spine = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
    out = []
    for idx, idref in enumerate(spine):
        href, mt = items.get(idref, (None, None))
        if href:
            out.append({"index": idx, "idref": idref, "href": href})
    return out

def build_outline(ncx_path):
    """Parse NCX navPoints into nested bookmark entries with page targets.

    Returns list of (level, label, href) sorted by doc order.
    """
    ncx = open(ncx_path, encoding="utf-8").read()
    # Extract navPoint hierarchy
    points = []
    # find all navPoint blocks with their nesting
    stack = []
    for m in re.finditer(r'<navPoint\b[^>]*>|<navPoint\b[^>]*/>|</navPoint>|<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"/>', ncx, re.S):
        pass
    # simpler: use an XML-ish parse with regex on pairs
    pattern = re.compile(
        r'<navPoint[^>]*>.*?<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"/>',
        re.S)
    flat = []
    for m in pattern.finditer(ncx):
        label = re.sub(r"<[^>]+>", "", m.group(1))
        label = (label.replace("&#x000FC;", "ü").replace("&#x000E4;", "ä")
                 .replace("&#x000F6;", "ö").replace("&#x000DF;", "ß")
                 .replace("&amp;", "&").replace("&#160;", " ").replace("&nbsp;", " ")
                 .replace("&#x000C4;", "Ä").replace("&#x000D6;", "Ö").replace("&#x000DC;", "Ü"))
        flat.append((label.strip(), m.group(2)))
    return flat

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
    ncx = open(os.path.join(OUT_ROOT, "ncx.xml"), encoding="utf-8").read()
    navs = re.findall(
        r'<navPoint\b[^>]*>.*?<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"/>',
        ncx, re.S)
    # depth estimation: count navPoint nesting via position
    # simpler: use playOrder-less approach - the NCX nesting can be derived from
    # the document structure. For pypdf we need parent hierarchy; pypdf supports
    # add_outline_item with parent, so we track the last item at each level.
    # We'll approximate levels by looking at the navPoint tag depth.
    stack = []  # list of (depth, outline_ref)
    pos = 0
    for m in re.finditer(r'<navPoint\b|</navPoint>|<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"/>', ncx, re.S):
        if m.group(0).startswith("<navPoint"):
            stack.append((len(stack), None))
        elif m.group(0) == "</navPoint>":
            if stack:
                stack.pop()
        elif m.group(1) is not None:
            label = re.sub(r"<[^>]+>", "", m.group(1))
            label = (label.replace("&#x000FC;", "ü").replace("&#x000E4;", "ä")
                     .replace("&#x000F6;", "ö").replace("&#x000DF;", "ß")
                     .replace("&amp;", "&").replace("&#160;", " ").replace("&nbsp;", " ")
                     .replace("&#x000C4;", "Ä").replace("&#x000D6;", "Ö").replace("&#x000DC;", "Ü"))
            href = m.group(2)
            base = href.split("#")[0]
            idx = href_index.get(base)
            if idx is not None and idx in offset:
                page_num = offset[idx]
                depth = len(stack) if stack else 0
                # find parent = nearest stack entry below
                parent = None
                for d, ref in stack:
                    if d < depth and ref is not None:
                        parent = ref
                try:
                    ref = writer.add_outline_item(label.strip(), page_num, parent=parent)
                    if stack:
                        stack[-1] = (len(stack) - 1, ref)
                except Exception as e:
                    print(f"outline error for '{label}': {e}")
            else:
                print(f"  no page for navpoint '{label}' -> {href}")

    with open(args.out, "wb") as f:
        writer.write(f)
    sz = os.path.getsize(args.out)
    print(f"\nWROTE {args.out} ({sz/1e6:.1f} MB, {total} pages)")

if __name__ == "__main__":
    main()
