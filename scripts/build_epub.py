#!/usr/bin/env python3
"""
Assemble a clean EPUB3 from the captured, sanitized book sources.

Use this only for books you are personally licensed to read; keep the output
for your own offline study. No DRM removal, no key extraction.

What it does
------------
1. copies + sanitizes every captured spine XHTML (sanitize_xhtml.py);
2. copies every local asset referenced by the XHTML (images/CSS/fonts),
   preserving the relative ../images/... structure - byte-identical copies,
   images are NOT resized or re-encoded;
3. writes META-INF/container.xml and the uncompressed first-entry mimetype;
4. rebuilds content.opf: metadata + a manifest limited to shipped files +
   the spine in the PUBLISHER's reading order (parsed from the original OPF);
5. reuses the publisher's toc.ncx for bookmarks;
6. zips everything with mimetype stored first (EPUB spec requirement).

Verification: reports entry count, mimetype placement/content, and checks
that every manifest item is resolvable inside the zip.
"""
import re
import os
import sys
import shutil
import zipfile
import argparse
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opf_parser import package_path, package_relative_path, parse_opf
from sanitize_xhtml import sanitize

# --- configuration (adjust for your title) ---------------------------------
OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # working dir from walk_spine.py
OEBPS = os.path.join(OUT_ROOT, "OEBPS")
BUILD = "/tmp/epub_build"                      # staging dir
EPUB_OUT = "/mnt/h/WSL/vitalsource/Neuroanatomie_9._Auflage_Trepel_offline.epub"

def load_spine():
    """Parse the publisher's content.opf for the ordered spine."""
    _, spine = parse_opf(os.path.join(OUT_ROOT, "opf.xml"))
    return spine

def collect_assets(xhtml_files):
    """Collect all local asset paths referenced by the captured XHTML.
    xhtml_files are in OEBPS/xhtml; refs like ../images/... resolve to OEBPS.
    Absolute/data:/anchor refs are ignored."""
    assets = set()
    chapter_sources = set()
    pat = re.compile(r'(?:src|href)="([^"#]+)"')
    for xf in xhtml_files:
        # xf is in the BUILD tree; find the matching source in OEBPS
        rel = os.path.relpath(xf, os.path.join(BUILD, "OEBPS"))
        src_xf = os.path.join(OEBPS, rel)
        chapter_sources.add(os.path.normpath(src_xf))
        if not os.path.exists(src_xf):
            continue
        try:
            with open(src_xf, encoding="utf-8") as source:
                html = source.read()
        except Exception:
            continue
        base = os.path.dirname(src_xf)
        for m in pat.finditer(html):
            ref = m.group(1)
            if ref.startswith(("http://", "https://", "data:", "#", "mailto:")):
                continue
            p = os.path.normpath(os.path.join(base, ref))
            if os.path.exists(p) and os.path.commonpath([p, OEBPS]) == OEBPS:
                assets.add(p)
    # Chapter links must not copy raw captures over the sanitized spine files.
    return assets - chapter_sources

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=EPUB_OUT)
    args = ap.parse_args()

    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(os.path.join(BUILD, "META-INF"), exist_ok=True)
    os.makedirs(os.path.join(BUILD, "OEBPS"), exist_ok=True)

    spine = load_spine()
    print(f"spine items: {len(spine)}")

    # 1. copy + sanitize all xhtml
    build_oebps = os.path.join(BUILD, "OEBPS")
    xhtml_files = []
    for item in spine:
        href = item["href"]
        sp = str(package_path(OEBPS, href))
        if not os.path.exists(sp):
            print(f"  WARN missing captured xhtml: {href}")
            continue
        with open(sp, encoding="utf-8") as source:
            html = source.read()
        html = sanitize(html)
        # EPUB xhtml must not contain absolute jigsaw URLs or vst chrome
        dp = str(package_path(build_oebps, href))
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        with open(dp, "w", encoding="utf-8") as f:
            f.write(html)
        xhtml_files.append(dp)
    print(f"xhtml copied+sanitized: {len(xhtml_files)}")

    # 2. copy referenced assets preserving relative structure
    assets = collect_assets(xhtml_files)
    for a in sorted(assets):
        rel = os.path.relpath(a, OEBPS)
        dst = os.path.join(BUILD, "OEBPS", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(a, dst)
    print(f"assets copied: {len(assets)}")

    # 3. write container.xml + mimetype
    with open(os.path.join(BUILD, "META-INF", "container.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                '  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                '  </rootfiles>\n'
                '</container>\n')
    with open(os.path.join(BUILD, "mimetype"), "w", encoding="utf-8") as f:
        f.write("application/epub+zip")

    # 4. build content.opf with a manifest of all files we actually ship
    shipped = []
    for root, _, files in os.walk(os.path.join(BUILD, "OEBPS")):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, os.path.join(BUILD, "OEBPS"))
            shipped.append(rel.replace(os.sep, "/"))
    shipped.sort()

    def media_type(rel):
        if rel.endswith(".xhtml"):
            return "application/xhtml+xml"
        if rel.endswith(".jpg") or rel.endswith(".jpeg"):
            return "image/jpeg"
        if rel.endswith(".png"):
            return "image/png"
        if rel.endswith(".gif"):
            return "image/gif"
        if rel.endswith(".svg"):
            return "image/svg+xml"
        if rel.endswith(".css"):
            return "text/css"
        if rel.endswith(".woff"):
            return "font/woff"
        if rel.endswith(".woff2"):
            return "font/woff2"
        if rel.endswith(".otf"):
            return "application/vnd.ms-opentype"
        if rel.endswith(".ttf"):
            return "font/ttf"
        if rel.endswith(".js"):
            return "application/javascript"
        if rel.endswith(".json"):
            return "application/json"
        return "application/octet-stream"

    manifest_lines = []
    for i, rel in enumerate(shipped):
        mid = f"id{i:04d}"
        # Encode filesystem names as URI paths; quotes and XML delimiters are escaped too.
        href = quote(rel, safe="/")
        manifest_lines.append(
            f'    <item id="{mid}" href="{href}" media-type="{media_type(rel)}"/>')

    # spine: publisher order, only for items we have
    spine_lines = []
    for item in spine:
        # find the manifest id for this file
        rel = package_relative_path(item["href"]).as_posix()
        if rel in shipped:
            mid = f"id{shipped.index(rel):04d}"
            spine_lines.append(f'    <itemref idref="{mid}"/>')

    # ncx
    ncx_rel = "toc.ncx"
    manifest_lines.append(f'    <item id="ncx" href="{ncx_rel}" media-type="application/x-dtbncx+xml"/>')

    opf_out = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="de">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:isbn:9783437057854</dc:identifier>
    <dc:title>Neuroanatomie - Struktur und Funktion</dc:title>
    <dc:creator>Martin Trepel</dc:creator>
    <dc:language>de</dc:language>
    <dc:publisher>Elsevier / Urban &amp; Fischer</dc:publisher>
    <dc:date>2025</dc:date>
    <meta property="dcterms:modified">2026-08-19T00:00:00Z</meta>
  </metadata>
  <manifest>
{chr(10).join(manifest_lines)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_lines)}
  </spine>
</package>
"""
    with open(os.path.join(BUILD, "OEBPS", "content.opf"), "w", encoding="utf-8") as f:
        f.write(opf_out)

    # 5. copy NCX (with navPoints) — keep as-is
    with open(os.path.join(OUT_ROOT, "ncx.xml"), encoding="utf-8") as source:
        ncx = source.read()
    with open(os.path.join(BUILD, "OEBPS", "toc.ncx"), "w", encoding="utf-8") as f:
        f.write(ncx)

    # 6. zip as EPUB (mimetype first, STORED)
    if os.path.exists(args.out):
        os.remove(args.out)
    with zipfile.ZipFile(args.out, "w") as z:
        z.write(os.path.join(BUILD, "mimetype"), "mimetype", compress_type=zipfile.ZIP_STORED)
        for root, _, files in os.walk(BUILD):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, BUILD)
                if rel == "mimetype":
                    continue
                z.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)
    print(f"\nWROTE {args.out}")

    # verification
    with zipfile.ZipFile(args.out) as z:
        names = z.namelist()
        print("entries:", len(names))
        print("mimetype first:", names[0] == "mimetype")
        mi = z.read("mimetype")
        print("mimetype content:", mi)
        missing = []
        # OPF-relative filenames live under OEBPS in the archive. Include files
        # generated after the shipped-file inventory as well.
        for rel in [*shipped, ncx_rel, "content.opf"]:
            if f"OEBPS/{rel}" not in names:
                missing.append(rel)
        print("missing shipped files in zip:", len(missing))
        for mref in missing[:10]:
            print("  ", mref)

if __name__ == "__main__":
    main()
