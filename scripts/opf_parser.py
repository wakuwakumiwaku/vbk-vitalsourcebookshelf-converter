"""Read OPF manifest and spine structure."""

import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


def _local_name(element):
    return element.tag.rpartition("}")[2]


def _child(parent, name):
    return next((element for element in parent if _local_name(element) == name), None)


class _RenderedPackageParser(HTMLParser):
    """Recover OPF structure from a browser-serialized HTML document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.package = None
        self.manifest = None
        self.spine = None
        self.in_package = False

    def handle_starttag(self, tag, attrs):
        name = tag.rpartition(":")[2]
        attributes = {key: value for key, value in attrs if value is not None}
        if name == "package" and self.package is None:
            self.package = ET.Element("package")
            self.in_package = True
            return
        if not self.in_package or self.package is None:
            return
        if name == "manifest":
            self.manifest = ET.SubElement(self.package, "manifest")
        elif name == "spine":
            self.spine = ET.SubElement(self.package, "spine")
        elif name == "item" and self.manifest is not None:
            ET.SubElement(self.manifest, "item", attributes)
        elif name == "itemref" and self.spine is not None:
            ET.SubElement(self.spine, "itemref", attributes)

    def handle_endtag(self, tag):
        name = tag.rpartition(":")[2]
        if name == "manifest":
            self.manifest = None
        elif name == "spine":
            self.spine = None
        elif name == "package":
            self.in_package = False


def _package_root(path):
    """Load a package root, including manifests saved from a rendered DOM."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        text = Path(path).read_text(encoding="utf-8-sig")
        parser = _RenderedPackageParser()
        parser.feed(text)
        if parser.package is None:
            raise ValueError(f"no OPF package element found in {path}")
        return parser.package

    if _local_name(root) == "package":
        return root
    package = next(
        (element for element in root.iter() if _local_name(element) == "package"),
        None,
    )
    if package is None:
        raise ValueError(f"no OPF package element found in {path}")
    return package


def _validate_href(href):
    """Reject manifest references that are not package-relative file paths."""
    if not href or "\x00" in href or "\\" in href:
        raise ValueError(f"unsafe OPF href: {href!r}")

    parsed = urlsplit(href)
    if (
        parsed.scheme
        or parsed.netloc
        or "?" in href
        or "#" in href
    ):
        raise ValueError(f"unsafe OPF href: {href!r}")

    decoded_path = unquote(parsed.path)
    segments = decoded_path.split("/")
    path = PurePosixPath(decoded_path)
    if (
        not decoded_path
        or "\x00" in decoded_path
        or "\\" in decoded_path
        or path.is_absolute()
        or not path.parts
        or any(segment in {"", ".", ".."} for segment in segments)
        or decoded_path.endswith("/")
    ):
        raise ValueError(f"unsafe OPF href: {href!r}")
    return parsed.path


def package_relative_path(href):
    """Return the decoded package-relative filesystem path for a safe href."""
    return PurePosixPath(unquote(_validate_href(href)))


def package_path(root, href):
    """Resolve a safe manifest href beneath a local package root."""
    relative_path = package_relative_path(href)
    root = Path(root).resolve()
    destination = root.joinpath(*relative_path.parts).resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise ValueError(f"unsafe OPF href: {href!r}") from error
    if destination == root:
        raise ValueError(f"unsafe OPF href: {href!r}")
    return destination


def parse_opf(path):
    """Return a manifest mapping and ordered spine entries from an OPF file."""
    root = _package_root(path)

    manifest = {}
    manifest_element = _child(root, "manifest")
    if manifest_element is not None:
        for item in manifest_element:
            if _local_name(item) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            media_type = item.get("media-type")
            if item_id and media_type:
                manifest[item_id] = (_validate_href(href), media_type)

    spine_element = _child(root, "spine")
    idrefs = []
    if spine_element is not None:
        idrefs = [
            itemref.get("idref")
            for itemref in spine_element
            if _local_name(itemref) == "itemref" and itemref.get("idref")
        ]

    spine = []
    for index, idref in enumerate(idrefs):
        item = manifest.get(idref)
        if item is None:
            continue
        href, media_type = item
        spine.append(
            {
                "index": index,
                "idref": idref,
                "href": href,
                "media_type": media_type,
            }
        )

    return manifest, spine
