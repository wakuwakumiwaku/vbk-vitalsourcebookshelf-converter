#!/usr/bin/env python3
"""
Minimal Chrome DevTools Protocol (CDP) client.

Used by the other scripts in this repo to drive the licensed ClinicalKey
Student reader tab. Only the pieces needed for this workflow are
implemented:

  - list tabs (find the reader tab by URL pattern)
  - Runtime.evaluate (run JS in a page / frame / isolated world)
  - Page.navigate / Page.getFrameTree / Page.createIsolatedWorld

PITFALL: Chromium rejects WebSocket connections from unknown origins. Either
start the browser with --remote-allow-origins=<origin> or send a browser-like
User-Agent in the handshake (done here) so the connection is accepted.
"""
import json
import websocket
import sys
import time
import urllib.request

CDP_HTTP = "http://127.0.0.1:9224"            # browser's remote-debugging port
READER_TAB_PREFIX = "clinicalkeymeded.elsevier.com/reader/books/9783437057854"

def list_tabs():
    """GET /json -> list of open tabs (pages, iframes, workers...)."""
    with urllib.request.urlopen(CDP_HTTP + "/json") as r:
        return json.load(r)

def find_reader_tab():
    """Return the first page tab whose URL points at the book reader."""
    for t in list_tabs():
        if t.get("type") == "page" and READER_TAB_PREFIX in t.get("url", ""):
            return t
    return None

class CDP:
    """Synchronous request/response wrapper over a DevTools WebSocket."""

    def __init__(self, ws_url):
        # suppress_origin + browser UA: see module docstring (CDP origin check)
        self.ws = websocket.create_connection(
            ws_url, timeout=30,
            suppress_origin=True,
            header={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            },
        )
        self.msg_id = 0
        self.pending = {}

    def call(self, method, params=None):
        """Send a CDP command and block until its matching response arrives."""
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression):
        """Convenience: Runtime.evaluate with returnByValue + awaitPromise."""
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if "exceptionDetails" in res:
            return {"__exception__": res["exceptionDetails"].get("text", "")}
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

if __name__ == "__main__":
    tab = find_reader_tab()
    if not tab:
        print("NO READER TAB FOUND")
        sys.exit(1)
    print("Reader tab:", tab["id"])
    print("URL:", tab["url"])
    cdp = CDP(tab["webSocketDebuggerUrl"])
    title = cdp.eval("document.title")
    print("Title:", title)
    # Look for the main content containers
    info = cdp.eval("""
    (() => {
      const out = {};
      out.bodyChildren = document.body ? document.body.children.length : -1;
      out.iframes = [...document.querySelectorAll('iframe')].map(f => ({src: f.src, id: f.id, cls: f.className}));
      out.hasReader = !!document.querySelector('.vst-reader, .pagination, #reader');
      out.scripts = [...document.scripts].map(s => s.src).filter(s => /reader|epub|vst/i.test(s)).slice(0,10);
      out.textSamples = [];
      // Try to find text containers
      const cands = document.querySelectorAll('p, h1, h2, h3, section, article');
      let n = 0;
      for (const c of cands) {
        if (c.innerText && c.innerText.trim().length > 20) {
          out.textSamples.push(c.innerText.trim().slice(0, 120));
          if (++n >= 5) break;
        }
      }
      return out;
    })()
    """)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    cdp.close()
