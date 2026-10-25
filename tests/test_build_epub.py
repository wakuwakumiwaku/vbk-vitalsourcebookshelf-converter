import io
import gc
import sys
import tempfile
import unittest
import warnings
import zipfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import build_epub
from scripts.opf_parser import package_relative_path, parse_opf


class BuildEpubTests(unittest.TestCase):
    def _build_synthetic_epub(self, root, chapters, chapter_html=None, manifest_assets=None):
        out_root = root / "source"
        oebps = out_root / "OEBPS"
        build = root / "build"
        epub_out = root / "book.epub"
        package = ET.Element("package", xmlns="http://www.idpf.org/2007/opf")
        manifest = ET.SubElement(package, "manifest")
        spine = ET.SubElement(package, "spine")
        for index, (rel, href) in enumerate(chapters):
            source = oebps / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                (chapter_html or {}).get(rel, f"<html><body>Chapter {index}</body></html>"),
                encoding="utf-8",
            )
            item_id = f"chapter{index}"
            ET.SubElement(manifest, "item", {
                "id": item_id, "href": href, "media-type": "application/xhtml+xml"
            })
            ET.SubElement(spine, "itemref", idref=item_id)
        for index, (rel, (href, media_type, data)) in enumerate((manifest_assets or {}).items()):
            if data is not None:
                source = oebps / rel
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
            ET.SubElement(manifest, "item", {
                "id": f"asset{index}", "href": href, "media-type": media_type
            })
        ET.ElementTree(package).write(out_root / "opf.xml", encoding="utf-8")
        (out_root / "ncx.xml").write_text(
            '<?xml version="1.0"?><ncx><navMap /></ncx>', encoding="utf-8"
        )
        output = io.StringIO()
        with (
            mock.patch.multiple(
                build_epub, OUT_ROOT=str(out_root), OEBPS=str(oebps), BUILD=str(build)
            ),
            mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(epub_out)]),
            redirect_stdout(output),
        ):
            build_epub.main()
        return epub_out, output.getvalue()

    def _previous_epub(self, root):
        target, _ = self._build_synthetic_epub(
            root, [("xhtml/previous.xhtml", "xhtml/previous.xhtml")]
        )
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("OEBPS/xhtml/previous.xhtml", archive.namelist())
        return target, target.read_bytes()

    def test_existing_epub_survives_archive_write_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target, previous = self._previous_epub(root)
            original_write = zipfile.ZipFile.write

            def fail_after_mimetype(archive, filename, arcname, **kwargs):
                if arcname != "mimetype":
                    raise OSError("simulated archive write failure")
                return original_write(archive, filename, arcname, **kwargs)

            with mock.patch.object(zipfile.ZipFile, "write", new=fail_after_mimetype):
                with self.assertRaisesRegex(OSError, "simulated archive write failure"):
                    self._build_synthetic_epub(root, [("chapter.xhtml", "chapter.xhtml")])

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(root.glob(".epub-*")), [])

    def test_failed_first_build_leaves_no_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_write = zipfile.ZipFile.write

            def fail_after_mimetype(archive, filename, arcname, **kwargs):
                if arcname != "mimetype":
                    raise OSError("simulated archive write failure")
                return original_write(archive, filename, arcname, **kwargs)

            with mock.patch.object(zipfile.ZipFile, "write", new=fail_after_mimetype):
                with self.assertRaisesRegex(OSError, "simulated archive write failure"):
                    self._build_synthetic_epub(root, [("chapter.xhtml", "chapter.xhtml")])

            self.assertFalse((root / "book.epub").exists())
            self.assertEqual(list(root.glob(".epub-*")), [])

    def test_existing_epub_survives_archive_verification_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target, previous = self._previous_epub(root)
            original_zipfile = zipfile.ZipFile

            def fail_verification(filename, mode="r", *args, **kwargs):
                if mode == "r":
                    raise zipfile.BadZipFile("simulated archive verification failure")
                self.assertEqual(mode, "w")
                return original_zipfile(filename, "w", *args, **kwargs)

            with mock.patch.object(zipfile, "ZipFile", side_effect=fail_verification):
                with self.assertRaisesRegex(zipfile.BadZipFile, "simulated archive verification failure"):
                    self._build_synthetic_epub(root, [("chapter.xhtml", "chapter.xhtml")])

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(root.glob(".epub-*")), [])

    def test_existing_epub_survives_atomic_replacement_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target, previous = self._previous_epub(root)
            with mock.patch.object(build_epub.os, "replace", side_effect=OSError("simulated replacement failure")):
                with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                    self._build_synthetic_epub(root, [("chapter.xhtml", "chapter.xhtml")])

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(root.glob(".epub-*")), [])

    def test_successful_rebuild_replaces_previous_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target, _ = self._previous_epub(root)
            result, output = self._build_synthetic_epub(
                root, [("chapter.xhtml", "chapter.xhtml")]
            )
            self.assertEqual(result, target)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.infolist()[0].filename, "mimetype")
                self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertIn("OEBPS/chapter.xhtml", archive.namelist())
                self.assertNotIn("OEBPS/xhtml/previous.xhtml", archive.namelist())
            self.assertIn(f"WROTE {target}", output)
            self.assertEqual(list(root.glob(".epub-*")), [])

    def test_output_inside_build_is_rejected_before_cleanup(self):
        for location in ("direct", "directory-alias", "file-alias", "link-to-outside"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                previous_target, previous = self._previous_epub(root)
                build = root / "build"
                target = build / "book.epub"
                target.write_bytes(previous)
                output = target
                if location == "directory-alias":
                    alias = root / "build-alias"
                    alias.symlink_to(build, target_is_directory=True)
                    output = alias / "book.epub"
                elif location == "file-alias":
                    output = root / "output-alias.epub"
                    output.symlink_to(target)
                elif location == "link-to-outside":
                    target.unlink()
                    target.symlink_to(previous_target)
                staged_chapter = build / "OEBPS/xhtml/previous.xhtml"
                chapter_before = staged_chapter.read_bytes()

                with (
                    mock.patch.multiple(
                        build_epub, OUT_ROOT=str(root / "source"),
                        OEBPS=str(root / "source/OEBPS"), BUILD=str(build),
                    ),
                    mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(output)]),
                    self.assertRaisesRegex(ValueError, "outside the build directory"),
                ):
                    build_epub.main()

                self.assertEqual(target.read_bytes(), previous)
                self.assertEqual(previous_target.read_bytes(), previous)
                self.assertEqual(staged_chapter.read_bytes(), chapter_before)
                self.assertEqual(list(root.glob(".epub-*")), [])

    def test_outward_build_aliases_are_rejected_before_cleanup(self):
        for location in (
            "directory-outward", "directory-chain", "external-directory",
            "external-file", "external-directory-chain", "external-file-chain",
            "parent-alias-outward",
        ):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                previous_target, previous = self._previous_epub(root)
                build = root / "build"
                outside = root / "outside"
                outside.mkdir()
                external_epub = outside / "book.epub"
                external_epub.write_bytes(previous)
                redirect = build / "redirect"
                # A relative outward target must retain the same dependency.
                redirect.symlink_to("../outside", target_is_directory=True)
                links = [redirect]
                output = redirect / "book.epub"
                if location == "directory-chain":
                    middle = root / "outside-alias"
                    middle.symlink_to(outside, target_is_directory=True)
                    redirect.unlink()
                    redirect.symlink_to(middle, target_is_directory=True)
                    links.append(middle)
                elif location.startswith("external-"):
                    is_directory = "directory" in location
                    target = redirect if is_directory else redirect / "book.epub"
                    if location.endswith("-chain"):
                        middle = root / "middle-alias"
                        middle.symlink_to(target.relative_to(root), target_is_directory=is_directory)
                        links.append(middle)
                        target = middle
                    alias = root / "output-alias"
                    alias.symlink_to(target, target_is_directory=is_directory)
                    links.append(alias)
                    output = alias / "book.epub" if is_directory else alias
                elif location == "parent-alias-outward":
                    alias = root / "build-alias"
                    alias.symlink_to(build, target_is_directory=True)
                    links.append(alias)
                    output = alias / "redirect/book.epub"
                link_targets = {link: link.readlink() for link in links}
                staged_chapter = build / "OEBPS/xhtml/previous.xhtml"
                source_chapter = root / "source/OEBPS/xhtml/previous.xhtml"
                chapter_before = staged_chapter.read_bytes()
                source_before = source_chapter.read_bytes()
                self.assertEqual(output.read_bytes(), previous)
                error = None

                with (
                    mock.patch.multiple(
                        build_epub, OUT_ROOT=str(root / "source"),
                        OEBPS=str(root / "source/OEBPS"), BUILD=str(build),
                    ),
                    mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(output)]),
                    mock.patch.object(build_epub.shutil, "rmtree", wraps=build_epub.shutil.rmtree) as cleanup,
                    redirect_stdout(io.StringIO()),
                ):
                    try:
                        build_epub.main()
                    except Exception as exc:
                        error = exc

                # These assertions run even when the builder raises a late error.
                with self.subTest(check="pre-cleanup-refusal"):
                    self.assertEqual(cleanup.call_args_list, [], "output admission ran destructive cleanup")
                with self.subTest(check="intentional-error"):
                    self.assertIsInstance(error, ValueError)
                    self.assertRegex(str(error), "outside the build directory")
                for link, target in link_targets.items():
                    with self.subTest(check="preserved-link", link=link.name):
                        self.assertTrue(link.is_symlink(), f"deleted or replaced link: {link}")
                        self.assertEqual(link.readlink(), target)
                with self.subTest(check="preserved-staged-chapter"):
                    self.assertTrue(staged_chapter.is_file())
                    self.assertEqual(staged_chapter.read_bytes(), chapter_before)
                self.assertEqual(source_chapter.read_bytes(), source_before)
                self.assertEqual(previous_target.read_bytes(), previous)
                self.assertEqual(external_epub.read_bytes(), previous)
                self.assertEqual(list(root.rglob(".epub-*")), [])

    def test_output_aliases_independent_of_build_remain_supported(self):
        for location in ("directory", "directory-chain", "file-chain"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                _, previous = self._previous_epub(root)
                outside = root / "outside"
                outside.mkdir()
                external_epub = outside / "book.epub"
                external_epub.write_bytes(previous)
                is_directory = location != "file-chain"
                target = outside if is_directory else external_epub
                if location.endswith("-chain"):
                    middle = root / "middle-alias"
                    middle.symlink_to(target.relative_to(root), target_is_directory=is_directory)
                    target = middle
                alias = root / "output-alias"
                alias.symlink_to(target, target_is_directory=is_directory)
                output = alias / "book.epub" if is_directory else alias
                (root / "source/OEBPS/xhtml/previous.xhtml").write_text(
                    "<html><body>Rebuilt chapter</body></html>", encoding="utf-8"
                )
                with (
                    mock.patch.multiple(
                        build_epub, OUT_ROOT=str(root / "source"),
                        OEBPS=str(root / "source/OEBPS"), BUILD=str(root / "build"),
                    ),
                    mock.patch.object(sys, "argv", ["build_epub.py", "--out", str(output)]),
                    redirect_stdout(io.StringIO()),
                ):
                    build_epub.main()
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(
                        archive.read("OEBPS/xhtml/previous.xhtml"),
                        b"<html><body>Rebuilt chapter</body></html>",
                    )
                if not is_directory:
                    # Publishing replaces the output link, not its unrelated target.
                    self.assertFalse(output.is_symlink())
                    self.assertEqual(external_epub.read_bytes(), previous)
                self.assertEqual(list(root.rglob(".epub-*")), [])

    def test_chapter_links_do_not_restore_unsanitized_captures(self):
        for link_href in (
            "one.xhtml", "two.xhtml", "two.xhtml#section", "two.xhtml?v=1", "%74wo.xhtml"
        ):
            with self.subTest(link_href=link_href), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                stylesheet = root / "source" / "OEBPS" / "styles" / "book.css"
                stylesheet.parent.mkdir(parents=True)
                css = b"p { color: navy; }\n"
                stylesheet.write_bytes(css)
                chapter_html = {
                    "xhtml/one.xhtml": (
                        '<html><head><link rel="stylesheet" href="../styles/book.css" />'
                        '<script src="reader.js"></script></head><body>'
                        f'<p>Chapter one</p><a href="{link_href}">Read chapter</a>'
                        '</body></html>'
                    ),
                    "xhtml/two.xhtml": (
                        '<html><head><script>window.readerHook = true;</script></head>'
                        '<body><p>Chapter two</p></body></html>'
                    ),
                }
                epub_out, output = self._build_synthetic_epub(
                    root, [(rel, rel) for rel in chapter_html], chapter_html=chapter_html
                )
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertIsNone(epub.testzip())
                    self.assertEqual(epub.read("OEBPS/styles/book.css"), css)
                    for rel, original in chapter_html.items():
                        chapter = ET.fromstring(epub.read(f"OEBPS/{rel}"))
                        self.assertEqual(chapter.findall(".//script"), [])
                        self.assertEqual(
                            chapter.findtext(".//p"), ET.fromstring(original).findtext(".//p")
                        )
                        self.assertEqual(
                            (root / "source" / "OEBPS" / rel).read_text(encoding="utf-8"),
                            original,
                        )
                    first = ET.fromstring(epub.read("OEBPS/xhtml/one.xhtml"))
                    self.assertEqual(
                        [link.get("href") for link in first.findall(".//a")], [link_href]
                    )
                self.assertIn("assets copied: 1\n", output)

    def test_copies_assets_referenced_by_uri(self):
        cases = [
            ("images/figure.svg", "../images/figure.svg", "src"),
            ("images/figure one.svg", "../images/figure%20one.svg", "src"),
            ("images/figure.svg", "../images/figure.svg?v=1", "src"),
            ("images/figure.svg", "../images/figure.svg#view", "src"),
            ("images/figure&one.svg", "../images/figure&amp;one.svg", "src"),
            ("images/literal%20.svg", "../images/literal%2520.svg", "src"),
            ("images/figure#1?.svg", "../images/figure%231%3F.svg", "src"),
            ("images/Figur Ü.svg", "../images/Figur%20%C3%9C.svg", "src"),
            ("styles/book style.css", "../styles/book%20style.css?v=1#sheet", "href"),
        ]
        for filename, ref, attribute in cases:
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                asset = root / "source" / "OEBPS" / filename
                asset.parent.mkdir(parents=True)
                data = (
                    b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1" /></svg>'
                    if attribute == "src" else b"p { color: navy; }\n"
                )
                asset.write_bytes(data)
                element = (
                    f'<img src="{ref}" alt="Figure" />' if attribute == "src"
                    else f'<link rel="stylesheet" href="{ref}" />'
                )
                chapter = f"<html><body>{element}</body></html>"
                epub_out, output = self._build_synthetic_epub(
                    root, [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")],
                    chapter_html={"xhtml/chapter.xhtml": chapter},
                )
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertIsNone(epub.testzip())
                    self.assertIn(f"OEBPS/{filename}", epub.namelist())
                    self.assertEqual(epub.read(f"OEBPS/{filename}"), data)
                    self.assertEqual(epub.read("OEBPS/xhtml/chapter.xhtml"), chapter.encode())
                    opf = epub.read("OEBPS/content.opf")
                    ET.fromstring(opf)
                    manifest, _ = parse_opf(io.BytesIO(opf))
                    members = {
                        "OEBPS/" + package_relative_path(href).as_posix()
                        for href, _ in manifest.values()
                    }
                    self.assertIn(f"OEBPS/{filename}", members)
                    self.assertTrue(members.issubset(epub.namelist()))
                self.assertEqual(asset.read_bytes(), data)
                self.assertIn("assets copied: 1\n", output)

    def test_packages_assets_referenced_only_by_stylesheets(self):
        for font_name, font_type in (
            ("symbols.woff2", "font/woff2"),
            ("symbols.woff", "application/font-woff"),
            ("symbols.otf", "application/vnd.ms-opentype"),
            ("symbols.ttf", "font/ttf"),
            ("symbols.ttf", "application/font-sfnt"),
            ("symbols.otf", "application/font-sfnt"),
        ):
            with self.subTest(font_type=font_type), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                resources = {
                    "styles/book.css": (
                        "styles/book.css", "text/css",
                        b'@import url("nested/theme.css");\n',
                    ),
                    "styles/nested/theme.css": (
                        "styles/nested/theme.css", "text/css",
                        (
                            '@import "../book.css";\n'
                            '@font-face { font-family: symbols; '
                            f'src: url("../../fonts/{font_name}"); }}\n'
                            '.diagram { background-image: url("../../images/figure%20one.svg"); }\n'
                        ).encode(),
                    ),
                    f"fonts/{font_name}": (
                        f"fonts/{font_name}", font_type, b"synthetic font bytes\x00\xff",
                    ),
                    "images/figure one.svg": (
                        "images/figure%20one.svg", "image/svg+xml",
                        b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1" /></svg>',
                    ),
                }
                chapter = (
                    '<html><head><link rel="stylesheet" href="../styles/book.css" />'
                    '</head><body><p class="diagram">Chapter text</p></body></html>'
                )
                epub_out, output = self._build_synthetic_epub(
                    root, [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")],
                    chapter_html={"xhtml/chapter.xhtml": chapter}, manifest_assets=resources,
                )
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertIsNone(epub.testzip())
                    expected_members = {f"OEBPS/{rel}" for rel in resources}
                    self.assertTrue(
                        expected_members.issubset(epub.namelist()),
                        f"Missing stylesheet dependencies: {expected_members - set(epub.namelist())}",
                    )
                    opf = epub.read("OEBPS/content.opf")
                    ET.fromstring(opf)
                    manifest, spine = parse_opf(io.BytesIO(opf))
                    manifest_members = {
                        "OEBPS/" + package_relative_path(href).as_posix()
                        for href, _ in manifest.values()
                    }
                    self.assertTrue(expected_members.issubset(manifest_members))
                    self.assertTrue(manifest_members.issubset(epub.namelist()))
                    self.assertEqual([item["href"] for item in spine], ["xhtml/chapter.xhtml"])
                    for rel, (_, _, data) in resources.items():
                        self.assertEqual(epub.read(f"OEBPS/{rel}"), data)
                        self.assertEqual((root / "source" / "OEBPS" / rel).read_bytes(), data)
                self.assertIn("assets copied: 4\n", output)

    def test_manifest_assets_do_not_restore_scripts_or_nonspine_xhtml(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chapter = (
                '<html><head><script>readerHook()</script></head>'
                '<body><p>Keep chapter text</p></body></html>'
            )
            resources = {
                "reader.js": ("reader.js", "application/javascript", b"readerHook()"),
                "extra.xhtml": (
                    "extra.xhtml", "application/xhtml+xml",
                    b"<html><body><script>readerHook()</script></body></html>",
                ),
                "missing.css": ("missing.css", "text/css", None),
                "missing.svg": ("missing.svg", "image/svg+xml", None),
                "missing.woff2": ("missing.woff2", "font/woff2", None),
            }
            epub_out, output = self._build_synthetic_epub(
                Path(temp_dir), [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")],
                chapter_html={"xhtml/chapter.xhtml": chapter}, manifest_assets=resources,
            )
            with zipfile.ZipFile(epub_out) as epub:
                self.assertIsNone(epub.testzip())
                self.assertTrue({f"OEBPS/{rel}" for rel in resources}.isdisjoint(epub.namelist()))
                cleaned = ET.fromstring(epub.read("OEBPS/xhtml/chapter.xhtml"))
                self.assertEqual(cleaned.findall(".//script"), [])
                self.assertEqual(cleaned.findtext(".//p"), "Keep chapter text")
            self.assertIn("assets copied: 0\n", output)

    def test_ignores_nonlocal_and_pathless_asset_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oebps = root / "source" / "OEBPS"
            asset = oebps / "images" / "figure.svg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"local image")
            (oebps.parent / "outside.svg").write_bytes(b"outside package")
            refs = [
                "https://example.org/images/figure.svg",
                "//example.org/images/figure.svg",
                asset.as_uri(),
                asset.as_posix(),
                "../../outside.svg",
                "%2e%2e/%2e%2e/outside.svg",
                "#figure",
                "?v=1",
                "data:image/svg+xml;base64,PHN2Zy8+",
                "https://[invalid",
            ]
            chapter = '<html><body>' + ''.join(
                f'<img src="{ref}" alt="Ignored" />' for ref in refs
            ) + '</body></html>'
            epub_out, output = self._build_synthetic_epub(
                root, [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")],
                chapter_html={"xhtml/chapter.xhtml": chapter},
            )
            with zipfile.ZipFile(epub_out) as epub:
                self.assertFalse(any(name.endswith(".svg") for name in epub.namelist()))
            self.assertIn("assets copied: 0\n", output)

    def test_manifest_hrefs_round_trip_special_filenames(self):
        cases = [
            ("xhtml/space name.xhtml", "xhtml/space%20name.xhtml"),
            ("xhtml/amp&name.xhtml", "xhtml/amp%26name.xhtml"),
            ('xhtml/quotes"and\'name.xhtml', "xhtml/quotes%22and%27name.xhtml"),
            ("xhtml/<chapter>.xhtml", "xhtml/%3Cchapter%3E.xhtml"),
            ("xhtml/chapter#part?.xhtml", "xhtml/chapter%23part%3F.xhtml"),
            ("xhtml/100%done.xhtml", "xhtml/100%25done.xhtml"),
            ("xhtml/literal%20%2F%2e%2e.xhtml", "xhtml/literal%2520%252F%252e%252e.xhtml"),
            ("sections Ü/chapter.xhtml", "sections%20%C3%9C/chapter.xhtml"),
            ("chapter:one.xhtml", "chapter%3Aone.xhtml"),
        ]
        for rel, href in cases:
            with self.subTest(filename=rel), tempfile.TemporaryDirectory() as temp_dir:
                # Publisher order deliberately differs from sorted manifest order.
                chapters = [(rel, href), ("a-first.xhtml", "a-first.xhtml")]
                epub_out, _ = self._build_synthetic_epub(Path(temp_dir), chapters)
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertIsNone(epub.testzip())
                    self.assertEqual(epub.infolist()[0].filename, "mimetype")
                    self.assertEqual(epub.infolist()[0].compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(epub.read("mimetype"), b"application/epub+zip")
                    for index, (filename, _) in enumerate(chapters):
                        self.assertEqual(
                            epub.read(f"OEBPS/{filename}"),
                            f"<html><body>Chapter {index}</body></html>".encode("utf-8"),
                        )
                    opf = epub.read("OEBPS/content.opf")
                    # parse_opf has a permissive HTML fallback: require real XML too.
                    ET.fromstring(opf)
                    manifest, spine = parse_opf(io.BytesIO(opf))
                    self.assertEqual(
                        {item[0] for item in manifest.values()},
                        {href, "a-first.xhtml", "toc.ncx"},
                    )
                    self.assertEqual([item["href"] for item in spine], [href, "a-first.xhtml"])
                    self.assertEqual(
                        [package_relative_path(item["href"]).as_posix() for item in spine],
                        [rel, "a-first.xhtml"],
                    )
                    for item_href, _ in manifest.values():
                        member = "OEBPS/" + package_relative_path(item_href).as_posix()
                        self.assertIn(member, epub.namelist())

    def test_verification_reports_no_missing_members_for_complete_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_out, output = self._build_synthetic_epub(
                Path(temp_dir), [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")]
            )
            with zipfile.ZipFile(epub_out) as epub:
                self.assertIn("OEBPS/xhtml/chapter.xhtml", epub.namelist())
                self.assertIn("OEBPS/toc.ncx", epub.namelist())
                self.assertIn("OEBPS/content.opf", epub.namelist())
            self.assertIn("missing shipped files in zip: 0\n", output)

    def test_verification_detects_omitted_archive_members(self):
        original_write = zipfile.ZipFile.write
        for omitted in ("xhtml/chapter.xhtml", "toc.ncx", "content.opf"):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as temp_dir:
                def write_except(archive, filename, arcname, **kwargs):
                    if Path(arcname).as_posix() != f"OEBPS/{omitted}":
                        return original_write(archive, filename, arcname, **kwargs)

                # Omit a real member on write, not merely from the verification listing.
                with mock.patch.object(zipfile.ZipFile, "write", new=write_except):
                    epub_out, output = self._build_synthetic_epub(
                        Path(temp_dir), [("xhtml/chapter.xhtml", "xhtml/chapter.xhtml")]
                    )
                with zipfile.ZipFile(epub_out) as epub:
                    self.assertNotIn(f"OEBPS/{omitted}", epub.namelist())
                self.assertIn(f"missing shipped files in zip: 1\n   {omitted}\n", output)

    def test_preserves_nested_spine_paths_with_duplicate_basenames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_root = root / "source"
            oebps = out_root / "OEBPS"
            build = root / "build"
            epub_out = root / "book.epub"

            front = oebps / "sections" / "front" / "chapter.xhtml"
            main = oebps / "sections" / "main" / "chapter.xhtml"
            front.parent.mkdir(parents=True)
            main.parent.mkdir(parents=True)
            front.write_text("<html><body>Front matter</body></html>", encoding="utf-8")
            main.write_text("<html><body>Main chapter</body></html>", encoding="utf-8")

            (out_root / "opf.xml").write_text(
                """\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="front" href="sections/front/chapter.xhtml" media-type="application/xhtml+xml" />
    <item id="main" href="sections/main/chapter.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="front" />
    <itemref idref="main" />
  </spine>
</package>
""",
                encoding="utf-8",
            )
            (out_root / "ncx.xml").write_text(
                '<?xml version="1.0"?><ncx><navMap /></ncx>',
                encoding="utf-8",
            )

            with warnings.catch_warnings(record=True) as captured_warnings:
                warnings.simplefilter("always", ResourceWarning)
                with (
                    mock.patch.multiple(
                        build_epub,
                        OUT_ROOT=str(out_root),
                        OEBPS=str(oebps),
                        BUILD=str(build),
                    ),
                    mock.patch.object(
                        sys, "argv", ["build_epub.py", "--out", str(epub_out)]
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    build_epub.main()
                gc.collect()

            self.assertFalse(
                [warning for warning in captured_warnings if warning.category is ResourceWarning]
            )

            with zipfile.ZipFile(epub_out) as epub:
                names = epub.namelist()
                self.assertIn("OEBPS/sections/front/chapter.xhtml", names)
                self.assertIn("OEBPS/sections/main/chapter.xhtml", names)
                self.assertEqual(
                    epub.read("OEBPS/sections/front/chapter.xhtml"),
                    b"<html><body>Front matter</body></html>",
                )
                self.assertEqual(
                    epub.read("OEBPS/sections/main/chapter.xhtml"),
                    b"<html><body>Main chapter</body></html>",
                )

                package = ET.fromstring(epub.read("OEBPS/content.opf"))

            manifest_hrefs = {
                item.get("href")
                for item in package.iter()
                if item.tag.rpartition("}")[2] == "item"
            }
            manifest_ids = {
                item.get("id"): item.get("href")
                for item in package.iter()
                if item.tag.rpartition("}")[2] == "item"
            }
            spine_hrefs = [
                manifest_ids[itemref.get("idref")]
                for itemref in package.iter()
                if itemref.tag.rpartition("}")[2] == "itemref"
            ]
            self.assertIn("sections/front/chapter.xhtml", manifest_hrefs)
            self.assertIn("sections/main/chapter.xhtml", manifest_hrefs)
            self.assertEqual(
                spine_hrefs,
                ["sections/front/chapter.xhtml", "sections/main/chapter.xhtml"],
            )


if __name__ == "__main__":
    unittest.main()
