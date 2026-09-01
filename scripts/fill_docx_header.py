#!/usr/bin/env python3
"""
Finish a docx exported by n2y by filling in its Word header and footer.

n2y hands pandoc ``templates/reference.docx``, which supplies the styles,
section numbering and the header/footer layout that document control expects.
The reference document's headers and footers hold jinja placeholders
(``{{ page['title'] }}``, ``{{ page['Id'] }}``, ``{{ revision }}``,
``{{ date }}``) which pandoc copies through verbatim.  This script renders them
against the page's Notion properties, which it reads from the YAML front matter
of the markdown export of the same page.

The result is written under the document-control filename, for example
``QMS-018_Notion_Documentation_Procedure_Internal_Use_Only_Rev_A.docx``.

Usage:

    python scripts/fill_docx_header.py export/QMS-018-unfilled.docx
    python scripts/fill_docx_header.py export/QMS-018-unfilled.docx --date 09/18/2025
"""

import argparse
import datetime
import os
import sys
import zipfile
from xml.dom import minidom

import jinja2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from n2y.utils import sanitize_filename  # noqa: E402

UNFILLED_SUFFIX = "-unfilled"

TEMPLATED_PARTS = [f"word/{kind}{n}.xml" for kind in ("header", "footer") for n in (1, 2, 3)]

# The Document Approval table's geometry, in twips.
#
# The table's signature cells are empty -- the Notion back-matter template emits
# a bare <td> for them -- so no renderer can size that column to its contents,
# and every one of them leaves it too narrow to hold a signature.  These numbers
# are the geometry of the signed documents, measured from the QMS-018 Rev A and
# DES-001-06 Rev B PDFs with scripts/measure_table_geometry.py; the two agree to
# within a twip, which is why this table can carry fixed widths when the other
# back-matter tables cannot.
APPROVAL_COLUMN_WIDTHS = (936, 4067, 4357)
APPROVAL_ROW_HEIGHT = 1370

# The table is found by its header row rather than by position, because a
# document's reviewers and approvers vary, and only the first two headers are
# matched: a Date column added beside the signature (21 CFR 11.50 asks for the
# date of signing) would leave these two in place.
APPROVAL_HEADERS = ("activity", "name")


def default_properties_path(docx_path):
    """
    The markdown export of the same page, alongside the .docx.

    n2y writes the .docx under an "-unfilled" name so that the half-finished
    document can't be mistaken for the deliverable; the markdown export it
    pairs with keeps the plain name.
    """
    stem = os.path.splitext(docx_path)[0]
    if stem.endswith(UNFILLED_SUFFIX) and not os.path.exists(stem + ".md"):
        stem = stem[: -len(UNFILLED_SUFFIX)]
    return stem + ".md"


def read_front_matter(path):
    """Read the YAML front matter from a markdown file exported by n2y."""
    with open(path, encoding="utf-8") as markdown_file:
        if markdown_file.readline().rstrip("\n") != "---":
            raise SystemExit(f"{path} does not start with YAML front matter")
        lines = []
        for line in markdown_file:
            if line.rstrip("\n") == "---":
                return yaml.safe_load("".join(lines)) or {}
            lines.append(line)
    raise SystemExit(f"The YAML front matter in {path} is not terminated")


def build_context(properties, date, title=None, revision=None):
    """
    Build the jinja context for the header and footer.

    The Notion ``Name`` carries the document ID as a prefix ("QMS-018 Notion
    Documentation Procedure"), but the header shows the ID in its own cell, so
    the prefix is stripped from the title -- matching the signed PDFs.
    """
    page = dict(properties)
    for key in ("Id", "Name"):
        if key not in page:
            raise SystemExit(f"The page properties have no '{key}'; cannot fill header")
        page[key] = str(page[key]).strip()

    if title is None:
        title = page["Name"]
        prefix = page["Id"] + " "
        if title.startswith(prefix):
            title = title[len(prefix):]
    page["title"] = title

    if revision is None:
        if "Revision" not in properties:
            raise SystemExit(
                "The page properties have no 'Revision'; pass --revision"
            )
        revision = str(properties["Revision"]).strip()

    return {"page": page, "revision": revision, "date": date}


def render_templated_parts(parts, context):
    environment = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
    for name in TEMPLATED_PARTS:
        if name not in parts:
            continue
        template = environment.from_string(parts[name].decode("utf-8"))
        parts[name] = template.render(**context).encode("utf-8")


def output_filename(context):
    page, revision = context["page"], context["revision"]
    return sanitize_filename(f"{page['Id']} {page['title']} Rev {revision}.docx")


def _children(node, tag):
    """The node's direct children with the given tag; not its descendants."""
    return [
        child
        for child in node.childNodes
        if child.nodeType == child.ELEMENT_NODE and child.tagName == tag
    ]


def _cell_text(cell):
    return "".join(
        node.firstChild.nodeValue if node.firstChild else ""
        for node in cell.getElementsByTagName("w:t")
    ).strip()


def _is_approval_table(table):
    rows = _children(table, "w:tr")
    if not rows:
        return False
    headers = [_cell_text(cell).lower() for cell in _children(rows[0], "w:tc")]
    return tuple(headers[: len(APPROVAL_HEADERS)]) == APPROVAL_HEADERS


def _element(document, tag, **attributes):
    element = document.createElement(tag)
    for name, value in attributes.items():
        element.setAttribute(name.replace("_", ":"), str(value))
    return element


def _replace_child(parent, tag, replacement, before=None):
    """Put ``replacement`` in place of ``parent``'s existing ``tag`` child."""
    for existing in _children(parent, tag):
        parent.removeChild(existing)
    parent.insertBefore(replacement, before)


def _pin_widths(document, table, widths):
    properties = _children(table, "w:tblPr")[0]

    # An explicit table width plus a fixed layout: without the fixed layout the
    # column widths below are only a hint, which every renderer is free to
    # recompute from the cell contents -- and does.
    width = _children(properties, "w:tblW")[0]
    width.setAttribute("w:type", "dxa")
    width.setAttribute("w:w", str(sum(widths)))
    # Schema order puts w:tblLayout after w:tblW and before w:tblLook.
    _replace_child(
        properties, "w:tblLayout",
        _element(document, "w:tblLayout", w_type="fixed"),
        before=width.nextSibling,
    )

    for column, twips in zip(_children(_children(table, "w:tblGrid")[0], "w:gridCol"), widths):
        column.setAttribute("w:w", str(twips))

    for row in _children(table, "w:tr"):
        for cell, twips in zip(_children(row, "w:tc"), widths):
            cell_properties = _children(cell, "w:tcPr")[0]
            _replace_child(
                cell_properties, "w:tcW",
                _element(document, "w:tcW", w_type="dxa", w_w=twips),
                before=cell_properties.firstChild,
            )


def _pin_row_heights(document, table, height):
    """
    Give every data row a minimum height, leaving the header row alone.

    The cells are deliberately left top-aligned.  The signatures in the signed
    documents look vertically centred, but they are not -- they are top-anchored
    too, and only look centred because the ink is short relative to a one-inch
    row.  Adding w:vAlign center here was measured against a real signing: it
    moves the signature field further from where the originals put it, and
    pushes the field past the bottom of the row.
    """
    for row in _children(table, "w:tr")[1:]:
        row_properties = _element(document, "w:trPr")
        row_properties.appendChild(
            # "atLeast" rather than "exact": the row must still be free to grow
            # if a name wraps, where "exact" would clip it.
            _element(document, "w:trHeight", w_hRule="atLeast", w_val=height)
        )
        # w:trPr comes first in a row, after w:tblPrEx if the row carries one.
        existing = _children(row, "w:tblPrEx")
        after = existing[0].nextSibling if existing else row.firstChild
        _replace_child(row, "w:trPr", row_properties, before=after)


def pin_approval_table(xml_bytes):
    """
    Give the Document Approval table the geometry of the signed documents.

    pandoc writes the back-matter tables with equal column widths and a table
    width of ``auto``, which asks the renderer to size the columns to their
    contents.  That works for the tables whose cells hold something; the
    signature cells are empty, so the column stays too narrow for a signature
    and the rows one line tall.  Fixing the widths and the row height here means
    the geometry no longer depends on the renderer, on the length of whatever
    the signature cell eventually holds, or on the number of approvers.

    Documents with no Document Approval table are left alone.
    """
    document = minidom.parseString(xml_bytes)
    for table in document.getElementsByTagName("w:tbl"):
        if not _is_approval_table(table):
            continue
        columns = len(_children(_children(table, "w:tblGrid")[0], "w:gridCol"))
        if columns != len(APPROVAL_COLUMN_WIDTHS):
            raise SystemExit(
                f"The Document Approval table has {columns} columns, but "
                f"{len(APPROVAL_COLUMN_WIDTHS)} widths are defined for it. "
                "Update APPROVAL_COLUMN_WIDTHS -- silently leaving the table "
                "unpinned would leave no room for the signatures."
            )
        _pin_widths(document, table, APPROVAL_COLUMN_WIDTHS)
        _pin_row_heights(document, table, APPROVAL_ROW_HEIGHT)
        break
    return document.toxml(encoding="utf-8")


def fill(docx_path, properties_path, date, output_path=None, **overrides):
    properties = read_front_matter(properties_path)
    context = build_context(properties, date, **overrides)

    with zipfile.ZipFile(docx_path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    render_templated_parts(parts, context)
    parts["word/document.xml"] = pin_approval_table(parts["word/document.xml"])

    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(docx_path) or ".", output_filename(context)
        )
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in parts.items():
            output.writestr(name, data)
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", help="The unfilled .docx written by n2y")
    parser.add_argument(
        "--properties",
        "-p",
        help="Markdown export of the same page, read for its YAML front matter "
        "(default: the .docx path with a .md extension, ignoring any "
        "'-unfilled' suffix)",
    )
    parser.add_argument(
        "--date",
        "-d",
        default=datetime.date.today().strftime("%m/%d/%Y"),
        help="Effective date for the header, MM/DD/YYYY (default: today)",
    )
    parser.add_argument("--title", help="Override the header title")
    parser.add_argument("--revision", help="Override the revision")
    parser.add_argument(
        "--output", "-o", help="Where to write the filled document "
        "(default: the document-control filename, next to the input)"
    )
    args = parser.parse_args(argv)

    properties_path = args.properties or default_properties_path(args.docx)
    written = fill(
        args.docx,
        properties_path,
        args.date,
        args.output,
        title=args.title,
        revision=args.revision,
    )
    print(f"Wrote {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
