#!/usr/bin/env python3
"""
Measure the geometry of the back-matter tables in a rendered document.

The Definitions, Document Approval and Document History tables are written by
pandoc the way Innolitics' exporter wrote them: equal column widths and a table
width of ``auto``, which is an instruction to size the columns to their
contents.  Renderers disagree about that instruction -- SignNow (which produced
the signed PDFs) and Word size the columns to the cell text, while LibreOffice
lays them out at the equal widths it is given -- so the geometry cannot be read
off the .docx.  It has to be measured from a rendering, which is what this
script does.

Given a PDF it reports, for each back-matter table, the table's extent, the
column widths (in points, twips and as a fraction of the table) and the row
heights.  Point out the signed PDF of a document and you get the ground-truth
proportions; point it at a LibreOffice rendering of our own export and you get
what we currently produce.

Usage:

    python scripts/measure_table_geometry.py SIGNED.pdf [MORE.pdf ...]

Requires poppler's ``pdftotext`` and ``pdftoppm`` (both in the dev container).

How it works: the table style draws a rule under every row, so the horizontal
rules give the table's left and right edges and its row boundaries, while the x
of the first word in each column gives the column boundaries.  The inset
between a column's boundary and its text (the cell margin plus half the border)
is not assumed -- it is calibrated from the first column, whose boundary is the
measured left edge of the table.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

# The header row of each back-matter table, used to find it and to count its
# columns.  A header cell is matched on its first word, which is what the
# column's x position is taken from.
TABLES = {
    "Definitions": ["Term", "Definition"],
    "Document Approval": ["Activity", "Name", "Signature/Date"],
    "Document History": ["Revision", "Date", "Author", "Summary"],
}

RASTER_DPI = 300
SCALE = 72.0 / RASTER_DPI        # raster pixels -> points
DARK = 160                       # 0..255; anti-aliased rules stay well below
RULE_COVERAGE = 0.3              # a rule is a solid run this wide, page-relative
TWIPS_PER_POINT = 20


def page_words(pdf):
    """[[(x, y, text), ...] per page], from pdftotext's bounding-box output."""
    xml = subprocess.run(
        ["pdftotext", "-bbox-layout", pdf, "-"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    pages = []
    for body in re.findall(r"<page\b.*?>(.*?)</page>", xml, re.S):
        pages.append(
            [
                (float(x), float(y), text)
                for x, y, text in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>',
                    body,
                )
            ]
        )
    return pages


def page_raster(pdf, page):
    """(width, height, greyscale bytes) for one page."""
    with tempfile.TemporaryDirectory() as directory:
        root = os.path.join(directory, "page")
        subprocess.run(
            ["pdftoppm", "-gray", "-r", str(RASTER_DPI), "-f", str(page), "-l",
             str(page), "-singlefile", pdf, root],
            check=True, capture_output=True,
        )
        with open(root + ".pgm", "rb") as raster:
            pgm = raster.read()
    fields, pos = [], 0
    while len(fields) < 4:                      # P5, width, height, maxval
        while pgm[pos:pos + 1].isspace():
            pos += 1
        if pgm[pos:pos + 1] == b"#":
            pos = pgm.index(b"\n", pos)
            continue
        start = pos
        while not pgm[pos:pos + 1].isspace():
            pos += 1
        fields.append(pgm[start:pos])
    return int(fields[1]), int(fields[2]), pgm[pos + 1:]


def horizontal_rules(pdf, page):
    """[(y, left, right)] in points, one per drawn rule, top to bottom."""
    width, height, data = page_raster(pdf, page)
    rows = []
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        # A rule is a solid run of dark pixels.  Counting dark pixels anywhere
        # on the line instead would mistake a long line of body text for one.
        start = best = None
        run_start = None
        for x in range(width + 1):
            if x < width and row[x] < DARK:
                if run_start is None:
                    run_start = x
            elif run_start is not None:
                if best is None or x - run_start > best - start:
                    start, best = run_start, x
                run_start = None
        if best is not None and best - start > width * RULE_COVERAGE:
            rows.append((y, start, best))

    rules, group = [], []
    for row in rows:
        # A rule is a few pixels tall once anti-aliased; adjacent rows are one.
        if group and row[0] - group[-1][0] > 2:
            rules.append(group)
            group = []
        group.append(row)
    if group:
        rules.append(group)

    return [
        (
            (group[0][0] + group[-1][0]) / 2 * SCALE,
            min(r[1] for r in group) * SCALE,
            max(r[2] for r in group) * SCALE,
        )
        for group in rules
    ]


def header_row(words, headers):
    """
    (column x positions, header y) for the table's header row.

    The header words have to appear on one line and in column order; the same
    word elsewhere on the page (a heading, a repeated header on a continuation
    page) is rejected by that test.
    """
    lines = {}
    for x, y, text in words:
        lines.setdefault(round(y, 1), []).append((x, text))
    for y in sorted(lines):
        positions = []
        for header in headers:
            matches = [x for x, text in lines[y] if text == header]
            if not matches:
                break
            positions.append(min(matches))
        else:
            if positions == sorted(positions):
                return positions, y
    return None, None


def measure(pdf):
    """{table name: geometry} for every back-matter table found in the PDF."""
    found = {}
    rules_by_page = {}
    for number, words in enumerate(page_words(pdf), 1):
        for name, headers in TABLES.items():
            if name in found:
                continue
            columns, header_y = header_row(words, headers)
            if columns is None:
                continue
            if number not in rules_by_page:
                rules_by_page[number] = horizontal_rules(pdf, number)
            geometry = _geometry(rules_by_page[number], columns, header_y, words)
            if geometry:
                found[name] = dict(page=number, **geometry)
    return found


def _geometry(rules, columns, header_y, words):
    """Turn rules and column x positions into widths and row heights."""
    # The table's top rule is the last one above its header text; the rules
    # below it that share both edges are the rest of the table.  Anchoring on
    # the header text is what keeps the page header's own rules out.
    above = [r for r in rules
             if r[0] < header_y and r[1] < columns[0] and r[2] > columns[-1]]
    if not above:
        return None
    top = above[-1]
    left, right = top[1], top[2]
    inset = columns[0] - left                        # cell margin + half border

    # Where the table ends: the rules below its top rule that share both edges,
    # stopping at the first one with text between it and its predecessor that
    # begins outside the first column -- a heading, and so a following table.
    # Two back-matter tables can share their edges exactly (they do in a
    # LibreOffice rendering, where every table is laid out at the grid widths),
    # so the edges alone cannot tell them apart.
    outside = sorted(y for x, y, _ in words if x < left + inset - 1.0)
    band = [top]
    for rule in [r for r in rules if r[0] > top[0]]:
        if abs(rule[1] - left) > 1.5 or abs(rule[2] - right) > 1.5:
            continue
        if any(band[-1][0] < y < rule[0] for y in outside):
            break
        band.append(rule)
    if len(band) < 2:
        return None

    boundaries = [left] + [x - inset for x in columns[1:]] + [right]
    widths = [b - a for a, b in zip(boundaries, boundaries[1:])]
    total = right - left
    return {
        "left": left,
        "right": right,
        "width": total,
        "inset": inset,
        "columns": [
            {
                "points": round(w, 1),
                "twips": round(w * TWIPS_PER_POINT),
                "fraction": round(w / total, 4),
            }
            for w in widths
        ],
        "rows": [round(b - a, 1) for a, b in zip(
            [r[0] for r in band], [r[0] for r in band][1:]
        )],
    }


def report(pdf, geometry):
    print(f"=== {pdf}")
    if not geometry:
        print("    no back-matter tables found")
        return
    for name, table in geometry.items():
        print(
            f"  {name} (page {table['page']}): "
            f"x {table['left']:.1f}..{table['right']:.1f}, "
            f"width {table['width']:.1f}pt"
        )
        for index, column in enumerate(table["columns"], 1):
            print(
                f"      column {index}: {column['points']:7.1f}pt "
                f"{column['twips']:6d} twips  {column['fraction'] * 100:5.1f}%"
            )
        print(f"      row heights (pt): {table['rows']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", help="Rendered PDFs to measure")
    args = parser.parse_args(argv)
    for pdf in args.pdfs:
        report(pdf, measure(pdf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
