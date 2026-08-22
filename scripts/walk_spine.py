#!/usr/bin/env python3
"""
Walk the ClinicalKey Student reader spine via the licensed session and
capture the rendered HTML for every spine item.

PERSONAL AUTHORIZED ARCHIVING ONLY
----------------------------------
This reads content exactly as the licensed reader renders it (the digital
equivalent of screenshotting every page), for the user's own offline study.
It does NOT:
  - extract decryption keys,
  - remove DRM,
  - modify or decrypt the .vbk package itself.

Use only with content you are personally authorized to read.

How it works
------------
The reader is a React SPA whose innermost iframe (jigsaw.elsevier.com/
books/<ISBN>/epub/OEBPS/xhtml/...) holds the DECRYPTED, RENDERED document.
Fetching the raw XHTML URL returns ciphertext; only the rendered content
frame contains plain text. We therefore navigate the reader to each spine
item via its epubcfi URL and capture document.documentElement.outerHTML
from the content frame.

KEY PITFALL: the content frame's URL changes BEFORE the reader injects the
decrypted body (async). Polling the frame URL alone is not enough - we wait
until the frame has real text or rendered images.

Usage
-----
  python3 walk_spine.py [--start N] [--end N] [--only N]

  --start/--end : resume a partial run by spine index range
  --only N      : capture just one spine item (debugging)

Output: one XHTML file per spine item under OUT_ROOT/OEBPS/xhtml/, plus a
log of what was captured. Skips items whose previous capture is > 5 KB
(assumed complete).
"""
import json
import re
import sys
import os
import time
import base64
import argparse

# --- configuration (adjust for your title) ---------------------------------
BOOK_ID = "9783437057854"                      # ISBN / bookshelf book id
OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # local working directory
OEBPS = os.path.join(OUT_ROOT, "OEBPS")
SPINE_JSON = os.path.join(OUT_ROOT, "spine.json")
STATE_JSON = os.path.join(OUT_ROOT, "capture_state.json")

READER_BASE = f"https://clinicalkeymeded.elsevier.com/reader/books/{BOOK_ID}/epubcfi"
JIGSAW_BASE = f"https://jigsaw.elsevier.com/books/{BOOK_ID}/epub/OEBPS"

# cdp_helper.py lives next to this file; import it directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_helper import CDP, find_reader_tab


def load_spine():
    """Parse the publisher's content.opf into an ordered spine list.

    Returns [{"index", "idref", "href", "media_type"}, ...] in reading order.
    The OPF must have been fetched first (see README section 1).
    """
    opf = open(os.path.join(OUT_ROOT, "opf.xml"), encoding="utf-8").read()
    # manifest: id -> (href, media-type)
    items = {}
    for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"[^>]*\bmedia-type="([^"]+)"', opf):
        items[m.group(1)] = (m.group(2), m.group(3))
    spine = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
    spine_items = []
    for idx, idref in enumerate(spine):
        href, mt = items.get(idref, (None, None))
        if href:
            spine_items.append({
                "index": idx,
                "idref": idref,
                "href": href,
                "media_type": mt,
            })
    return spine_items

def cfi_for(item):
    """Build the reader URL (epubcfi) that opens a given spine item.

    Observed pattern: the numeric segment equals 2*(spine_index+1) and the
    idref is URL-encoded in brackets:
        .../epubcfi/6/6[%3Bvnd.vst.idref%3Dtitle]!/4/2
    The trailing !/4/2 positions the reader at the first page of the item.
    """
    # epubcfi number = 2*(index+1)
    n = 2 * (item["index"] + 1)
    return f"{READER_BASE}/6/{n}[%3Bvnd.vst.idref%3D{item['idref']}]!/4/2"

def find_book_frame(cdp):
    """Locate the innermost content frame (jigsaw.elsevier.com/books/...).

    The reader SPA nests three frames:
        reader tab -> mosaic wrapper -> content frame (the actual book).
    We always operate on the content frame.
    """
    frames = cdp.call("Page.getFrameTree")
    def walk(node):
        f = node["frame"]
        if "jigsaw.elsevier.com/books/" in f.get("url", ""):
            return f
        for ch in node.get("childFrames", []):
            r = walk(ch)
            if r:
                return r
        return None
    return walk(frames["frameTree"])

def wait_for_frame(cdp, expected_href, timeout=40):
    """Wait until the content frame shows the expected xhtml file AND has rendered
    actual content (reader injects decrypted body asynchronously after URL change).

    Why both conditions? The frame URL updates immediately on navigation, but
    the reader decrypts and injects the body afterwards. Capturing too early
    yields a head-only document (broken capture). We poll the DOM until it has
    real text (>= 30 chars) or at least one rendered image inside a section.
    """
    deadline = time.time() + timeout
    fname = os.path.basename(expected_href)

    def find_book_frame():
        frames = cdp.call("Page.getFrameTree")
        def walk(node):
            f = node["frame"]
            if "jigsaw.elsevier.com/books/" in f.get("url", ""):
                return f
            for ch in node.get("childFrames", []):
                r = walk(ch)
                if r:
                    return r
            return None
        return walk(frames["frameTree"])

    while time.time() < deadline:
        bf = find_book_frame()
        if bf and fname in bf.get("url", ""):
            # check that content actually rendered
            try:
                world = cdp.call("Page.createIsolatedWorld", {
                    "frameId": bf["id"], "worldName": "readycheck"
                })
                ctx = world["executionContextId"]
                res = cdp.call("Runtime.evaluate", {
                    "expression": """(() => {
                        const b = document.body;
                        const txt = b ? (b.innerText || '').trim().length : 0;
                        const imgs = b ? b.querySelectorAll('img').length : 0;
                        const hasSection = !!document.querySelector('section, article, p, h1');
                        return {txt, imgs, hasSection};
                    })()""",
                    "contextId": ctx,
                    "returnByValue": True,
                })
                st = res.get("result", {}).get("value", {})
                # Accept if real text OR any image rendered (figure-only pages are short)
                if (st.get("txt", 0) >= 30) or (st.get("imgs", 0) >= 1 and st.get("hasSection")):
                    return bf
            except Exception:
                # frame may have been torn down mid-navigation; retry
                pass
        time.sleep(0.8)
    return None

def get_rendered_html(cdp, book_frame):
    """Capture document.documentElement.outerHTML from the content frame.

    An isolated world is used so the evaluation runs in the frame's own
    context without touching the page's scripts. The captured HTML keeps
    relative asset paths (../images/..., ../styles/...) which the later
    packaging steps rely on.
    """
    world = cdp.call("Page.createIsolatedWorld", {
        "frameId": book_frame["id"], "worldName": "cap"
    })
    ctx = world["executionContextId"]
    res = cdp.call("Runtime.evaluate", {
        "expression": "document.documentElement.outerHTML",
        "contextId": ctx,
        "returnByValue": True,
    })
    return res.get("result", {}).get("value", "")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--only", type=int, default=None)
    args = ap.parse_args()

    spine = load_spine()
    if args.end:
        spine = spine[args.start:args.end]
    elif args.only is not None:
        spine = [s for s in spine if s["index"] == args.only]
    else:
        spine = spine[args.start:]

    print(f"spine total={len(spine)} to process={len(spine)}")
    tab = find_reader_tab()
    if not tab:
        print("NO READER TAB")
        sys.exit(1)
    cdp = CDP(tab["webSocketDebuggerUrl"])

    xhtml_dir = os.path.join(OEBPS, "xhtml")
    os.makedirs(xhtml_dir, exist_ok=True)

    ok, fail = 0, 0
    for item in spine:
        idx = item["index"]
        out_path = os.path.join(xhtml_dir, os.path.basename(item["href"]))
        # Skip only if a previous capture exists AND looks complete (has body content)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 5000:
            print(f"[{idx}] SKIP (exists): {item['href']}")
            ok += 1
            continue
        url = cfi_for(item)
        try:
            cdp.call("Page.navigate", {"url": url})
        except Exception as e:
            print(f"[{idx}] NAV ERROR {item['href']}: {e}")
            fail += 1
            continue
        bf = wait_for_frame(cdp, item["href"])
        if not bf:
            print(f"[{idx}] TIMEOUT waiting frame: {item['href']}")
            fail += 1
            continue
        time.sleep(1.0)  # let rendering settle
        html = get_rendered_html(cdp, bf)
        if len(html) < 500:
            print(f"[{idx}] EMPTY html: {item['href']} ({len(html)})")
            fail += 1
            continue
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        # progress
        txt_len = re.sub(r"<[^>]+>", "", html)
        print(f"[{idx}] OK {item['href']} html={len(html)} text~{len(txt_len)} imgs={html.count('<img')}")
        ok += 1
        time.sleep(0.5)

    print(f"\nDONE ok={ok} fail={fail}")
    cdp.close()

if __name__ == "__main__":
    main()
