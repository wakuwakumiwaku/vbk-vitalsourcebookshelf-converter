#!/usr/bin/env python3
"""
Assemble the captured chapters + mirrored assets into a single searchable PDF
(optional alternative to the EPUB output).

Use this only for books you are personally licensed to read; keep the output
for your own offline study. No DRM removal, no key extraction.

How it works
------------
1. sanitize all captured XHTML into a parallel tree (OEBPS_clean) and inject
   a book-format print CSS (170x240mm, compact typography; see
   print_book.css) so the pagination approximates the print edition;
2. serve the clean tree over localhost (SimpleHTTPServer);
3. launch a headless Chromium with a CDP port;
4. for each spine item: open a tab, navigate to the chapter URL, wait for
   content, call Page.printToPDF;
5. per-spine PDFs land in pdf_parts/; merge_pdf.py combines them with the
   NCX outline.

PITFALL: the captured HTML contains a <style media="print"> rule that hides
all content and prints a "please use the app" message. sanitize_xhtml.py
removes it - do not print the raw capture.
"""
import json
import re
import sys
import os
import time
import shutil
import subprocess
import argparse
import http.server
import socketserver
import threading
import base64
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opf_parser import package_path, parse_opf
from sanitize_xhtml import sanitize

# --- configuration (adjust for your title) ---------------------------------
BOOK_ID = "9783437057854"                      # ISBN / bookshelf book id
OUT_ROOT = "/mnt/h/WSL/vitalsource/book_src"   # working dir from walk_spine.py
OEBPS = os.path.join(OUT_ROOT, "OEBPS")
PDF_DIR = os.path.join(OUT_ROOT, "pdf_parts")
FINAL_PDF = "/mnt/h/WSL/vitalsource/Neuroanatomie_9._Auflage_Trepel_offline.pdf"

CHROME = "/home/hermes/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
CDP_PORT = 9333  # dedicated headless instance for printing

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

def start_server(serve_dir):
    os.chdir(serve_dir)
    handler = lambda *a, **kw: QuietHandler(*a, directory=serve_dir, **kw)
    httpd = socketserver.TCPServer(("127.0.0.1", 8765), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print(f"local server on http://127.0.0.1:8765 serving {serve_dir}")
    return httpd

def launch_chrome():
    subprocess.Popen([
        CHROME,
        "--headless=new",
        "--remote-debugging-port=%d" % CDP_PORT,
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--user-data-dir=/home/hermes/.local/share/print-helper-profile",
        "--window-size=1280,1600",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait for CDP
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as r:
                return json.load(r)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("chrome did not start")

def new_tab(cdp_http):
    req = urllib.request.Request(cdp_http + "/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)

def load_spine():
    _, spine = parse_opf(os.path.join(OUT_ROOT, "opf.xml"))
    return spine

def load_ncx_bookmarks():
    """Parse NCX navPoints into (level, label, href) bookmarks."""
    ncx = open(os.path.join(OUT_ROOT, "ncx.xml"), encoding="utf-8").read()
    marks = []
    for m in re.finditer(
        r'<navPoint[^>]*>.*?<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src="([^"]+)"/>',
        ncx, re.S):
        label = re.sub(r"<[^>]+>", "", m.group(1))
        label = label.replace("&#x000FC;", "ü").replace("&#x000E4;", "ä").replace("&#x000F6;", "ö")
        label = label.replace("&amp;", "&").replace("&#160;", " ").replace("&nbsp;", " ")
        marks.append((label.strip(), m.group(2)))
    return marks

def get_pdf(cdp, tab_id, url, out_path):
    import websocket

    ws_url = None
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json") as r:
        tabs = json.load(r)
    for t in tabs:
        if t.get("id") == tab_id:
            ws_url = t["webSocketDebuggerUrl"]
            break
    c = websocket.create_connection(ws_url, timeout=120, suppress_origin=True,
                                    header={"User-Agent": "Mozilla/5.0 Chrome/149"})
    mid = 0
    def call(method, params=None):
        nonlocal mid
        mid += 1
        m = mid
        c.send(json.dumps({"id": m, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(c.recv())
            if msg.get("id") == m:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
    call("Page.enable")
    call("Page.navigate", {"url": url})
    # wait for load
    time.sleep(1.5)
    for _ in range(60):
        st = call("Page.getLayoutMetrics", {})
        # crude readiness: document has content
        res = call("Runtime.evaluate", {
            "expression": "document.readyState + ':' + (document.body ? document.body.innerText.length : -1)",
            "returnByValue": True})
        v = res.get("result", {}).get("value", "")
        if v.startswith("complete") or int(v.split(":")[1]) > 0:
            break
        time.sleep(0.5)
    time.sleep(0.8)
    res = call("Page.printToPDF", {
        "printBackground": True,
        "preferCSSPageSize": True,
        "displayHeaderFooter": False,
        "marginTop": 0.4, "marginBottom": 0.4,
        "marginLeft": 0.4, "marginRight": 0.4,
    })
    data = res.get("data", "")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(data))
    c.close()
    return os.path.getsize(out_path)


def sanitize_xhtml_tree(clean_dir, css_override):
    """Sanitize every captured XHTML file anywhere in the package tree."""
    for root, _, files in os.walk(clean_dir):
        for filename in files:
            if not filename.endswith(".xhtml"):
                continue
            path = os.path.join(root, filename)
            with open(path, encoding="utf-8") as source:
                html = sanitize(source.read())
            html = html.replace("</head>", f"<style>\n{css_override}\n</style>\n</head>")
            with open(path, "w", encoding="utf-8") as destination:
                destination.write(html)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    args = ap.parse_args()

    spine = load_spine()
    if args.end:
        spine = spine[args.start:args.end]
    else:
        spine = spine[args.start:]

    os.makedirs(PDF_DIR, exist_ok=True)
    # sanitize all captured xhtml into a parallel tree
    clean_dir = os.path.join(OUT_ROOT, "OEBPS_clean")
    shutil.rmtree(clean_dir, ignore_errors=True)
    shutil.copytree(OEBPS, clean_dir)
    with open(os.path.join(OUT_ROOT, "print_book.css"), encoding="utf-8") as source:
        css_override = source.read()
    sanitize_xhtml_tree(clean_dir, css_override)
    print("sanitized tree at", clean_dir)

    httpd = start_server(clean_dir)
    try:
        chrome_info = launch_chrome()
        print("chrome:", chrome_info.get("Browser"))
    except Exception as e:
        print("chrome launch failed:", e)
        httpd.shutdown()
        sys.exit(1)

    ok, fail = 0, 0
    for item in spine:
        src = package_path(clean_dir, item["href"])
        if not os.path.exists(src) or os.path.getsize(src) < 300:
            # fall back to raw tree (uncaptured items are skipped at walk level)
            print(f"[{item['index']}] SKIP missing clean source: {item['href']}")
            fail += 1
            continue
        name = os.path.basename(item["href"]).replace(".xhtml", "")
        out_pdf = os.path.join(PDF_DIR, f"{item['index']:03d}_{name}.pdf")
        if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 2000:
            print(f"[{item['index']}] SKIP pdf exists: {name}")
            ok += 1
            continue
        url = f"http://127.0.0.1:8765/{item['href']}"
        try:
            tab = new_tab(f"http://127.0.0.1:{CDP_PORT}")
            size = get_pdf(cdp=None, tab_id=tab["id"], url=url, out_path=out_pdf)
            print(f"[{item['index']}] PDF {name} {size}b")
            ok += 1
            # close tab
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/close/{tab['id']}", method="PUT")
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
        except Exception as e:
            print(f"[{item['index']}] PDF FAIL {name}: {e}")
            fail += 1

    print(f"\nDONE ok={ok} fail={fail}")
    httpd.shutdown()

if __name__ == "__main__":
    main()
