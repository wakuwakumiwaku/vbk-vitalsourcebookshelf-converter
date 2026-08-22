# vbk-vitalsourcebookshelf-converter

Converts ebooks from the ClinicalKey Student / VitalSource Bookshelf web
reader into a clean, offline EPUB (and optionally a PDF).

If you have a book in your ClinicalKey Student library, you can read it in
the browser — but the underlying `.vbk` file is a proprietary, license-bound
format that no generic reader can open. This project captures what the
official web reader actually displays (text and figures) and repackages it
into a standard EPUB3 you can open anywhere, offline.

The result is the digital equivalent of screenshotting every page and
rebuilding a document — except the text layer stays real text and the images
stay at full resolution.

> Use this only for books you are personally licensed to read (e.g. through
> your institution's ClinicalKey Student access). Keep the output for your
> own study use. The `.vbk` file itself is never modified or decrypted.

---

## What you get

- A complete **EPUB3** (reflowable — the reading app decides pagination)
- All chapters in the publisher's reading order
- All figures at original resolution (no resizing, no re-encoding)
- A clickable table of contents (from the publisher's NCX)
- Optionally, a print-approximating **PDF** (170×240 mm, compact typography)

---

## What you need

- Python 3.10+
  - `pip install websocket-client pypdf pillow`
- A Chromium/Chrome browser (any recent build)
- A logged-in ClinicalKey Student session with the book in your library

---

## Step-by-step

### 1. Start the browser with remote debugging

```bash
chromium --remote-debugging-port=9224 \
         --user-data-dir=~/.local/share/clinicalkey-profile \
         --remote-allow-origins=http://127.0.0.1:9224 \
         "https://www.clinicalkey.com/student/content/toc/<TOC-ID>"
```

Log in to ClinicalKey Student and open the book. The reader tab URL looks
like this:

```
https://clinicalkeymeded.elsevier.com/reader/books/<ISBN>/epubcfi/6/2[...]!/4/2
```

### 2. Configure the scripts

Open each script and set the two values at the top:

```python
BOOK_ID   = "<ISBN>"          # the book's ISBN / bookshelf id
OUT_ROOT  = "<your work dir>" # where the captured files go
```

### 3. Fetch the publisher's manifest

The book's spine (reading order) and table of contents are served to your
logged-in session. From the reader's content frame, fetch these two files
and save them into `OUT_ROOT`:

```
https://jigsaw.elsevier.com/books/<ISBN>/epub/OEBPS/content.opf   -> opf.xml
https://jigsaw.elsevier.com/books/<ISBN>/epub/OEBPS/toc.ncx       -> ncx.xml
```

(Any same-origin fetch from the content frame works — the session cookies
make it authorized.)

### 4. Walk the spine

```bash
python3 walk_spine.py
```

This navigates the reader through every chapter and saves the rendered
XHTML. It is resumable: run it again to pick up where it left off.

### 5. Mirror the assets

```bash
python3 mirror_assets.py
```

Downloads all images, CSS, and fonts used by the book — byte-identical
copies, full resolution.

### 6. Build the EPUB

```bash
python3 build_epub.py
```

Produces `Neuroanatomie_9._Auflage_Trepel_offline.epub` (or whatever you
set as `EPUB_OUT`). Open it in any EPUB reader — Calibre, KOReader, Apple
Books, Tolino, the VitalSource Bookshelf app itself, etc.

### 7. (Optional) Build a PDF instead / as well

```bash
python3 build_pdf.py
python3 merge_pdf.py
```

Prints each chapter with headless Chromium and merges them into one PDF with
bookmarks.

---

## Scripts

| Script | What it does |
|---|---|
| `cdp_helper.py` | Minimal Chrome DevTools Protocol client used by the others |
| `walk_spine.py` | Steps through every chapter in the reader, saves the rendered XHTML |
| `mirror_assets.py` | Downloads all images / CSS / fonts at original quality |
| `sanitize_xhtml.py` | Cleans reader-specific markup out of the captured chapters |
| `build_epub.py` | Packages everything into an EPUB3 |
| `build_pdf.py` | Prints chapters to PDF via Chromium |
| `merge_pdf.py` | Merges the PDF parts and adds the table of contents |

---

## How it works

The reader is a web app whose innermost frame contains the decrypted,
rendered book. Fetching the raw chapter URLs directly returns ciphertext —
only the rendered frame holds the actual text. So the scripts:

1. navigate the reader to each chapter (spine item) via its `epubcfi` URL,
2. read the rendered DOM (text + image references),
3. download the images through the logged-in session,
4. strip reader-specific scripts and styles,
5. package the result as a standard EPUB.

Two things to know if something looks off:

- **Wait for content, not just the URL.** The reader swaps the frame URL
  immediately, then injects the text a moment later. The scripts poll until
  real content is present — this is why `walk_spine.py` takes a few seconds
  per chapter.
- **Page counts differ from the print edition.** The EPUB is reflowable: it
  has no fixed pages, so the reading app decides. The printed page count
  only exists in the typeset edition.

---

## Notes

- The captured HTML contains a hidden print-blocking style ("use the
  application to print"). `sanitize_xhtml.py` removes it — if you ever print
  a raw capture, this is why you get a single page with that message.
- The publisher's `toc.ncx` is reused as-is, so the EPUB's table of contents
  matches the book.
- All scripts are resumable and skip work that is already done, so
  interrupted runs can simply be restarted.
