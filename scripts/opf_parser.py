"""Read OPF manifest and spine structure."""

import xml.etree.ElementTree as ET


def _local_name(element):
    return element.tag.rpartition("}")[2]


def _child(parent, name):
    return next((element for element in parent if _local_name(element) == name), None)


def parse_opf(path):
    """Return a manifest mapping and ordered spine entries from an OPF file."""
    root = ET.parse(path).getroot()

    manifest = {}
    manifest_element = _child(root, "manifest")
    if manifest_element is not None:
        for item in manifest_element:
            if _local_name(item) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            media_type = item.get("media-type")
            if item_id and href and media_type:
                manifest[item_id] = (href, media_type)

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
