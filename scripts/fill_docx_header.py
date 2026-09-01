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

import jinja2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from n2y.utils import sanitize_filename  # noqa: E402

UNFILLED_SUFFIX = "-unfilled"

TEMPLATED_PARTS = [f"word/{kind}{n}.xml" for kind in ("header", "footer") for n in (1, 2, 3)]


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


def fill(docx_path, properties_path, date, output_path=None, **overrides):
    properties = read_front_matter(properties_path)
    context = build_context(properties, date, **overrides)

    with zipfile.ZipFile(docx_path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    render_templated_parts(parts, context)

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
