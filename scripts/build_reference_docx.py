#!/usr/bin/env python3
"""
Build ``templates/reference.docx`` -- the pandoc reference document used for
docx exports -- from an Innolitics-exported Word document.

The exported Word documents in Dropbox carry the styles, section numbering and
header/footer layout that document control expects.  This script strips one of
them down to an empty shell that pandoc can use with ``--reference-doc``:

* the body of ``word/document.xml`` is emptied, keeping only the ``sectPr``
  (page size, margins, and the header/footer references);
* every header and footer is replaced with a jinja-templated copy of the
  document's own header/footer, so ``scripts/fill_docx_header.py`` can fill in
  the title, ID, revision and effective date after pandoc has run;
* body images, body hyperlinks and document properties are dropped, leaving
  only the Antigen Plus logo used by the header.

``numbering.xml``, ``theme1.xml`` and ``fontTable.xml`` are copied through
untouched -- that is where the heading numbering ("1.", "5.4.1.") and the fonts
come from.  ``styles.xml`` keeps every style the Word document defines and
gains the ones pandoc expects but the Word document never defined (``Compact``,
``FirstParagraph``, ``FootnoteText``, ``Figure`` and friends), taken from
pandoc's own default reference document; without them the paragraphs pandoc
tags with those styles are unstyled, and bulleted lists lose their bullets.

Usage:

    python scripts/build_reference_docx.py SOURCE.docx [-o templates/reference.docx]

The source document used to build the committed template was
``/Business Dropbox for Antigen Plus/FDA/SOPs/
DES-001-06_Architectural_Design_Procedure_Rev_B_redline.docx``.
"""

import argparse
import io
import re
import subprocess
import sys
import zipfile
from xml.dom import minidom

# Text of the source document's header/footer runs, and the jinja placeholder
# that replaces it.  Keys are matched against the concatenated text of a run of
# consecutive <w:r> elements (Word splits a single logical string across
# several runs), so the whole group collapses into one run holding the
# placeholder -- jinja cannot render a tag that is split across runs.
HEADER_REPLACEMENTS = {
    "DES-001-06 Architectural Design Procedure": "{{ page['title'] }}",
    "DES-001-06": "{{ page['Id'] }}",
    "Rev B": "Rev {{ revision }}",
    "Effective Date: 09/18/2025": "Effective Date: {{ date }}",
}

FOOTER_REPLACEMENTS = {
    "DES-001-06 Architectural Design Procedure Rev B": (
        "{{ page['title'] }} Rev {{ revision }}"
    ),
}

# The Antigen Plus logo; the source document's first-page header still carries
# Innolitics' own logo, which we do not want on Antigen Plus documents.
LOGO_IMAGE = "word/media/image4.png"

HEADER_PARTS = ["word/header1.xml", "word/header2.xml", "word/header3.xml"]
FOOTER_PARTS = ["word/footer1.xml", "word/footer2.xml", "word/footer3.xml"]

TEMPLATED_HEADER_SOURCE = "word/header2.xml"
TEMPLATED_FOOTER_SOURCE = "word/footer2.xml"

# The namespace a .rels part is written in, and -- a different URI -- the
# relationship type of an image.  Word refuses to display an image whose
# relationship carries the package namespace here; LibreOffice renders it
# anyway, so this is not a difference a headless render will catch.
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
IMAGE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
)

HEADER_RELS = (
    '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
    f'<Relationships xmlns="{RELS_NS}">'
    f'<Relationship Id="rId1" Type="{IMAGE_REL_TYPE}" Target="media/image4.png" />'
    "</Relationships>"
)

NEUTRAL_CORE_PROPERTIES = (
    '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
    '<cp:coreProperties'
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:dcmitype="http://purl.org/dc/dcmitype/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<cp:revision>0</cp:revision>"
    "</cp:coreProperties>"
)


# Styles from pandoc's default reference document that we deliberately leave
# out: the exported PDFs show hyperlinks in the body text colour, which is what
# Word does when no Hyperlink character style is defined.
EXCLUDED_PANDOC_STYLES = {"Hyperlink"}

# Pandoc tags the items of a "tight" list -- which is what Notion's bullet
# lists become -- with its Compact style, halving the paragraph spacing.  The
# exported PDFs space list items like ordinary body text, so the style is kept
# for the numbering it carries but its spacing override is dropped.
UNSPACED_PANDOC_STYLES = {"Compact"}


# Tracked-change markup, accepted before the header is templated: the source
# document is a redline, and its header carries the Rev A -> Rev B edits.
DELETED_TAGS = ("w:del", "w:moveFrom", "w:rPrChange", "w:pPrChange", "w:tblPrChange")
UNWRAPPED_TAGS = ("w:ins", "w:moveTo")
DISCARDED_TAGS = ("w:proofErr", "w:bookmarkStart", "w:bookmarkEnd")


def accept_revisions(document):
    """Accept the source document's tracked changes, in place."""
    for tag in DELETED_TAGS + DISCARDED_TAGS:
        for element in list(document.getElementsByTagName(tag)):
            element.parentNode.removeChild(element)
    for tag in UNWRAPPED_TAGS:
        # Insertions nest, so keep unwrapping until none are left.
        while elements := document.getElementsByTagName(tag):
            element = elements[0]
            parent = element.parentNode
            for child in list(element.childNodes):
                parent.insertBefore(child, element)
            parent.removeChild(element)


def _text_of(run):
    return "".join(
        node.firstChild.nodeValue if node.firstChild else ""
        for node in run.getElementsByTagName("w:t")
    )


def _is_plain_text_run(run):
    """A run we may merge: it carries text and no field, tab or drawing."""
    if not run.getElementsByTagName("w:t"):
        return False
    for tag in ("w:fldChar", "w:instrText", "w:drawing", "w:tab", "w:br"):
        if run.getElementsByTagName(tag):
            return False
    return True


def _run_groups(paragraph):
    """Group the paragraph's direct <w:r> children into runs of plain text."""
    groups, current = [], []
    for child in paragraph.childNodes:
        if child.nodeType != child.ELEMENT_NODE:
            continue
        if child.tagName != "w:r":
            # Paragraph properties and the like sit between runs without
            # interrupting the text they surround.
            continue
        if _is_plain_text_run(child):
            current.append(child)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _collapse(document, group, replacement):
    """Replace a group of runs with a single run holding ``replacement``."""
    first = group[0]
    for text_node in list(first.getElementsByTagName("w:t"))[1:]:
        text_node.parentNode.removeChild(text_node)
    text_node = first.getElementsByTagName("w:t")[0]
    text_node.setAttribute("xml:space", "preserve")
    while text_node.firstChild:
        text_node.removeChild(text_node.firstChild)
    text_node.appendChild(document.createTextNode(replacement))
    for run in group[1:]:
        run.parentNode.removeChild(run)


def templatize(xml_bytes, replacements):
    document = minidom.parseString(xml_bytes)
    accept_revisions(document)
    replaced = set()
    for paragraph in document.getElementsByTagName("w:p"):
        for group in _run_groups(paragraph):
            text = "".join(_text_of(run) for run in group)
            replacement = replacements.get(text.strip())
            if replacement is not None:
                _collapse(document, group, replacement)
                replaced.add(text.strip())
    missing = set(replacements) - replaced
    if missing:
        raise SystemExit(
            "Did not find the expected header/footer text in the source "
            f"document: {sorted(missing)}"
        )
    return document.toxml(encoding="utf-8")


def empty_body(xml_bytes):
    """Keep the document shell and its sectPr; drop all body content."""
    text = xml_bytes.decode("utf-8")
    section_properties = re.search(r"<w:sectPr\b.*?</w:sectPr>", text, re.S)
    if section_properties is None:
        raise SystemExit("The source document has no sectPr")
    opening = text[: text.index("<w:body>") + len("<w:body>")]
    return (opening + section_properties.group() + "</w:body></w:document>").encode(
        "utf-8"
    )


def strip_body_relationships(xml_bytes):
    """Drop image and hyperlink relationships, which belonged to the body."""
    text = xml_bytes.decode("utf-8")
    kept = [
        relationship
        for relationship in re.findall(r"<Relationship\b[^>]*/>", text)
        if "/image" not in relationship and "/hyperlink" not in relationship
    ]
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
        f'<Relationships xmlns="{RELS_NS}">' + "".join(kept) + "</Relationships>"
    ).encode("utf-8")


def pandoc_default_styles():
    """The styles.xml from pandoc's own default reference document."""
    default_reference = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        capture_output=True,
    ).stdout
    with zipfile.ZipFile(io.BytesIO(default_reference)) as reference:
        return reference.read("word/styles.xml")


def merge_missing_styles(styles_xml, additional_styles_xml):
    """Add styles that the Word document doesn't define; never override one."""
    styles = minidom.parseString(styles_xml)
    additional = minidom.parseString(additional_styles_xml)
    defined = {
        element.getAttribute("w:styleId")
        for element in styles.getElementsByTagName("w:style")
    }
    root = styles.documentElement
    for element in additional.getElementsByTagName("w:style"):
        style_id = element.getAttribute("w:styleId")
        if style_id in defined or style_id in EXCLUDED_PANDOC_STYLES:
            continue
        imported = styles.importNode(element, True)
        if style_id in UNSPACED_PANDOC_STYLES:
            for spacing in list(imported.getElementsByTagName("w:spacing")):
                spacing.parentNode.removeChild(spacing)
        root.appendChild(imported)
    return styles.toxml(encoding="utf-8")


def build(source_path, output_path):
    with zipfile.ZipFile(source_path) as source:
        parts = {name: source.read(name) for name in source.namelist()}

    header = templatize(parts[TEMPLATED_HEADER_SOURCE], HEADER_REPLACEMENTS)
    footer = templatize(parts[TEMPLATED_FOOTER_SOURCE], FOOTER_REPLACEMENTS)

    parts["word/document.xml"] = empty_body(parts["word/document.xml"])
    parts["word/_rels/document.xml.rels"] = strip_body_relationships(
        parts["word/_rels/document.xml.rels"]
    )
    parts["docProps/core.xml"] = NEUTRAL_CORE_PROPERTIES.encode("utf-8")
    parts["word/styles.xml"] = merge_missing_styles(
        parts["word/styles.xml"], pandoc_default_styles()
    )

    # Every page (first, odd, even) gets the same templated header and footer,
    # matching the exported PDFs.
    for name in HEADER_PARTS:
        parts[name] = header
        parts[f"word/_rels/{name.split('/')[-1]}.rels"] = HEADER_RELS.encode("utf-8")
    for name in FOOTER_PARTS:
        parts[name] = footer
        parts.pop(f"word/_rels/{name.split('/')[-1]}.rels", None)

    for name in list(parts):
        if name.startswith("word/media/") and name != LOGO_IMAGE:
            del parts[name]

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in parts.items():
            output.writestr(name, data)
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="An Innolitics-exported .docx to derive from")
    parser.add_argument(
        "--output",
        "-o",
        default="templates/reference.docx",
        help="Where to write the reference document (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    print(f"Wrote {build(args.source, args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
