# ClinicalKey / VitalSource Reader → Offline EPUB & PDF

A documented, reproducible workflow for **authorized personal archiving** of
ebooks you are licensed to read (e.g. via your institution's ClinicalKey
Student / VitalSource Bookshelf access).

The approach is the digital equivalent of **screenshotting every page and
rebuilding a document**: the licensed reader renders the book on screen, and
this pipeline captures exactly what it renders — the text layer and the
full-resolution figures — then packages it into a standards-compliant EPUB
(and, optionally, a print-approximating PDF).

**No DRM removal. No key extraction. No decryption of the `.vbk` package.**
The `.vbk` itself is never modified or opened beyond what the official
reader does.

> ⚠️ **Scope**: Use this only for content you are personally authorized to
> read (valid license / institutional access). Keep the output for your own
> study use; do not share or redistribute the resulting files.

---

## Why this exists

A `.vbk` (VitalSource) file is a proprietary, license-bound package:

```
<header format="epubbook" version="3" ...>
  <filemap ... f="aes3"/>          <-- content encrypted (AES, 4096-byte chunks)
  <metadata ... drm="1" .../>
</header>
```

The decryption keys live in the reader app's private storage (tied to your
account + device). Generic readers cannot open it. But the **official web
reader renders the decrypted content in a browser** — that rendered output is
what this pipeline captures.

**Key insight**: fetching the raw EPUB XHTML files directly returns an
encrypted blob (`<div id="page-content">…ciphertext…</div>`), while the
*rendered DOM* inside the reader's content frame contains the complete plain
text and full-resolution images. We read the rendered DOM — the same bytes
the licensed reader displays.

---

## Pipeline overview

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Walk the spine (reading order)                          │
│    licensed reader tab (CDP) → construct epubcfi URLs       │
│    → capture rendered DOM of all 293 spine items            │
├─────────────────────────────────────────────────────────────┤
│ 2. Mirror assets                                           │
│    images / CSS / fonts served to the licensed session      │
│    (byte-identical copies, no resizing)                     │
├─────────────────────────────────────────────────────────────┤
│ 3. Sanitize                                                 │
│    strip reader chrome (scripts, print-block CSS)           │
│    → standalone XHTML chapters                              │
├─────────────────────────────────────────────────────────────┤
│ 4. Package                                                  │
│    EPUB3: publisher spine order + NCX bookmarks             │
│    PDF (optional): Chromium printToPDF, book format         │
└─────────────────────────────────────────────────────────────┘
```

### Requirements

- Python 3.10+ with `websocket-client`, `pypdf` (PDF only), `PIL` (verification)
- A Chromium/Chrome binary with remote debugging enabled
- A logged-in ClinicalKey Student (or VitalSource Bookshelf web) session
  with the title in your library

---

## How it works, step by step

### 0. Find the licensed reader session

Launch your normal browser automation with a dedicated profile, log in to
ClinicalKey Student, and open the book. The reader runs as a React SPA with
an iframe mosaic (Elsevier "jigsaw" stack):

```
https://clinicalkeymeded.elsevier.com/reader/books/<ISBN>/epubcfi/6/2[...]!/4/2
  └─ iframe: jigsaw.elsevier.com/mosaic/wrapper.html
       └─ iframe: jigsaw.elsevier.com/books/<ISBN>/epub/OEBPS/xhtml/<file>.xhtml
```

The innermost frame is the **content frame**. Its rendered DOM is the book.

Start the browser with remote debugging (adjust path to your Chromium):

```bash
chromium --remote-debugging-port=9224 \
         --user-data-dir=~/.local/share/clinicalkey-profile \
         --remote-allow-origins=http://127.0.0.1:9224 \
         https://www.clinicalkey.com/student/content/toc/<TOC-ID>
```

### 1. Fetch the manifest (spine + TOC)

The publisher's EPUB manifest is served to the session (same-origin fetch
from the content frame):

```
https://jigsaw.elsevier.com/books/<ISBN>/epub/OEBPS/content.opf
https://jigsaw.elsevier.com/books/<ISBN>/epub/OEBPS/toc.ncx
```

- `content.opf` → `<spine>` = reading order (293 items for this title),
  `<manifest>` = all resources
- `toc.ncx` → bookmarks (316 navPoints for this title)

### 2. Walk the spine

Each spine item is addressed by an `epubcfi` URL in the *reader tab*:

```
epubcfi number = 2 × (spine_index + 1)
https://clinicalkeymeded.elsevier.com/reader/books/<ISBN>/epubcfi/6/<N>[;vnd.vst.idref=<idref>]!/4/2
```

`walk_spine.py` navigates the reader tab to each URL, waits until the content
frame shows the expected file **and** has actually rendered content (the
reader injects the decrypted body asynchronously after the URL changes —
polling the frame URL alone is not enough), then saves
`document.documentElement.outerHTML`.

Result: one sanitizable XHTML per spine item, with text at the same positions
as the reader shows it and images referenced by their original relative paths
(`../images/f01-01-….jpg`).

### 3. Mirror assets

`mirror_assets.py` walks the OPF manifest and fetches every image/CSS/font
through the licensed session (same-origin fetch in the content frame,
base64 transfer). Files are saved **byte-identical** — no resizing, no
re-encoding. This is what keeps the figures at full resolution in the output.

### 4. Sanitize

The captured XHTML contains reader plumbing that must not run outside the
licensed app:

- `<script>` blocks (VST hooks, MathJax, Poptip) — content is static HTML
- print-blocking CSS: a `<style media="print">` rule that hides the whole
  body and shows *"To print, please use the print page range feature within
  the application."* — this is why a naive print of the captured file yields
  one page with that message
- reader-injected wrapper classes

`sanitize_xhtml.py` strips these, producing standalone chapter XHTML.

### 5. Package

**EPUB3** (`build_epub.py`):
- `mimetype` first, uncompressed (`application/epub+zip`)
- `META-INF/container.xml`
- `OEBPS/content.opf` — manifest rebuilt from files actually shipped,
  spine order taken from the publisher's OPF (identical order)
- `OEBPS/toc.ncx` — the publisher's navPoints
- XHTML + assets, ZIP_DEFLATED

**PDF** (`build_pdf.py` + `merge_pdf.py`, optional):
- serve the sanitized tree over localhost, print each spine item with
  headless Chromium `Page.printToPDF`
- book format CSS (`170×240 mm`, compact typography) approximates the print
  layout; EPUB remains the faithful reflowable copy
- `merge_pdf.py` merges per-spine PDFs and adds the NCX outline

---

## Verification

- **Images**: byte-identical with publisher originals (SHA-256 sample),
  original dimensions preserved
- **Flow**: spine order in the output equals the publisher OPF spine order
- **Structure**: every manifest item resolvable inside the container,
  `mimetype` first & stored, `container.xml` present
- **Content**: rendered smoke test — chapter opens with N/N images loaded
  and full text present

---

## Scripts

| Script | Purpose |
|---|---|
| `cdp_helper.py` | Minimal Chrome DevTools Protocol client (WebSocket + `Runtime.evaluate`) |
| `walk_spine.py` | Navigate the reader through all spine items, capture rendered XHTML |
| `mirror_assets.py` | Download all images/CSS/fonts through the licensed session |
| `sanitize_xhtml.py` | Strip reader chrome → standalone XHTML |
| `build_epub.py` | Assemble EPUB3 container |
| `build_pdf.py` | Print spine items to PDF via Chromium (book format) |
| `merge_pdf.py` | Merge per-spine PDFs + add NCX bookmarks |

---

## Notes / pitfalls

- **Direct URL fetch ≠ rendered DOM.** `fetch(xhtml_url)` returns ciphertext;
  only the rendered content frame has plain text. Always capture from the DOM.
- **Wait for content, not just the URL.** The content frame URL changes
  before the body is injected. Poll for `innerText` length / rendered images.
- **CDP origin check.** Chromium rejects WebSocket connections unless the
  browser is started with `--remote-allow-origins` (or a browser-like
  User-Agent is sent in the handshake).
- **Print-blocking CSS.** The captured HTML contains a `media="print"` rule
  that replaces all content with an "use the app" message — remove it before
  any printToPDF step.
- **Reader pagination ≠ print pagination.** The EPUB is reflowable; the
  printed page count (e.g. 442) only exists in the typeset edition. EPUB
  output sidesteps this entirely — the reader app determines pagination.
