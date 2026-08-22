#!/usr/bin/env python3
"""
Mirror all book assets (images, CSS, fonts) from the licensed session into
the local OEBPS tree.

PERSONAL AUTHORIZED ARCHIVING ONLY
----------------------------------
Assets are fetched through the user's licensed reader session (same-origin
fetch inside the content frame) and saved byte-identical - no resizing, no
re-encoding. Use only with content you are personally authorized to read.

Why fetch through the session? The images are served by jigsaw.elsevier.com
to the logged-in reader; the session cookies make the requests authorized.
Downloading them outside the session may 403.

Resilience: while walk_spine.py navigates the reader (changing frames), the
isolated-world execution context can go stale ("Cannot find context with
specified id"). On any error we re-create the context and retry once.
"""
import json
import re
import sys
import os
import time
import base64
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_helper import CDP, find_reader_tab

# --- configuration (adjust for your title) ---------------------------------
BOOK_ID = "9783437057854"                      # ISBN / bookshelf book id
OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # local working directory
OEBPS = os.path.join(OUT_ROOT, "OEBPS")
JIGSAW_BASE = f"https://jigsaw.elsevier.com/books/{BOOK_ID}/epub/OEBPS"

def load_manifest():
    """Parse content.opf: id -> (href, media-type) for every manifest item."""
    opf = open(os.path.join(OUT_ROOT, "opf.xml"), encoding="utf-8").read()
    items = {}
    for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"[^>]*\bmedia-type="([^"]+)"', opf):
        items[m.group(1)] = (m.group(2), m.group(3))
    return items

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="image/jpeg,image/png,text/css,application/font-woff,application/vnd.ms-opentype,font/woff,font/woff2,image/gif,image/svg+xml")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    wanted = set(t.strip() for t in args.types.split(","))

    items = load_manifest()
    targets = [(href, mt) for idref, (href, mt) in items.items() if mt in wanted]
    targets.sort()
    print(f"manifest assets to mirror: {len(targets)}")

    tab = find_reader_tab()
    cdp = CDP(tab["webSocketDebuggerUrl"])

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

    def fresh_ctx():
        # retry until a content frame exists (reader recreates frames on navigation)
        deadline = time.time() + 60
        while time.time() < deadline:
            bf = find_book_frame()
            if bf:
                try:
                    world = cdp.call("Page.createIsolatedWorld", {"frameId": bf["id"], "worldName": f"mirror_{int(time.time()*1000)}"})
                    return world["executionContextId"]
                except Exception:
                    pass
            time.sleep(1.0)
        raise RuntimeError("no content frame available")

    ctx = fresh_ctx()

    ok, skip, fail = 0, 0, 0
    for href, mt in targets:
        local = os.path.join(OEBPS, href.replace("/", os.sep))
        if os.path.exists(local) and os.path.getsize(local) > 100:
            skip += 1
            continue
        url = f"{JIGSAW_BASE}/{href}"
        expr = """
        (async () => {
          const url = %s;
          const r = await fetch(url, {credentials: 'include'});
          if (!r.ok) return {status: r.status, error: 'http'};
          const buf = await (await r.blob()).arrayBuffer();
          const bytes = new Uint8Array(buf);
          let bin = '';
          const chunk = 0x8000;
          for (let i = 0; i < bytes.length; i += chunk) {
            bin += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
          }
          return {status: r.status, ct: r.headers.get('content-type'), size: bytes.length, b64: btoa(bin)};
        })()
        """ % json.dumps(url)
        try:
            res = cdp.call("Runtime.evaluate", {
                "expression": expr, "contextId": ctx,
                "returnByValue": True, "awaitPromise": True,
            })
            val = res.get("result", {}).get("value", {})
            if val.get("status") != 200 or not val.get("b64"):
                print(f"FAIL {href} status={val.get('status')} err={val.get('error')}")
                fail += 1
                continue
            os.makedirs(os.path.dirname(local), exist_ok=True)
            raw = base64.b64decode(val["b64"])
            with open(local, "wb") as f:
                f.write(raw)
            print(f"OK {href} {len(raw)}b ({val.get('ct','')})")
            ok += 1
        except Exception as e:
            # frame may have navigated (spine walker) -> refresh context and retry once
            try:
                ctx = fresh_ctx()
                res = cdp.call("Runtime.evaluate", {
                    "expression": expr, "contextId": ctx,
                    "returnByValue": True, "awaitPromise": True,
                })
                val = res.get("result", {}).get("value", {})
                if val.get("status") == 200 and val.get("b64"):
                    os.makedirs(os.path.dirname(local), exist_ok=True)
                    raw = base64.b64decode(val["b64"])
                    with open(local, "wb") as f:
                        f.write(raw)
                    print(f"OK(retry) {href} {len(raw)}b")
                    ok += 1
                    time.sleep(0.2)
                    continue
            except Exception:
                pass
            print(f"ERR {href}: {e}")
            fail += 1
        if args.limit and ok >= args.limit:
            break
        time.sleep(0.2)

    print(f"\nDONE ok={ok} skip={skip} fail={fail}")
    cdp.close()

if __name__ == "__main__":
    main()
