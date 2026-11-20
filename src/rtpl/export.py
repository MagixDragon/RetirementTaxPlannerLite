"""Write a table (header plus rows of text) as CSV or as an OpenDocument
spreadsheet (.ods), with no extra dependencies.

The rows are the text the app shows ("12,389", "-1,772", "16.0%"). In the
.ods file, money becomes numbers with a thousands separator and rates
become percentages, so they add up in LibreOffice, Excel or Google Sheets.
"""

from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

_NUMBER = re.compile(r"^-?[\d,]+(\.\d+)?$")
_PERCENT = re.compile(r"^-?\d+(\.\d+)?%$")


def write_csv(path: str | Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _cell(text: str, numeric: bool) -> str:
    if not numeric:
        if text.isdigit():  # a year: a plain number, no thousands separator
            return f'<table:table-cell office:value-type="float" office:value="{text}"><text:p>{text}</text:p></table:table-cell>'
    elif _NUMBER.match(text):
        value = text.replace(",", "")
        return (f'<table:table-cell table:style-name="money" office:value-type="float" '
                f'office:value="{value}"><text:p>{escape(text)}</text:p></table:table-cell>')
    if numeric and _PERCENT.match(text):
        value = float(text[:-1]) / 100
        return (f'<table:table-cell table:style-name="rate" office:value-type="percentage" '
                f'office:value="{value!r}"><text:p>{escape(text)}</text:p></table:table-cell>')
    if not text:
        return "<table:table-cell/>"
    return f'<table:table-cell office:value-type="string"><text:p>{escape(text)}</text:p></table:table-cell>'


_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"'
)


def write_ods(path: str | Path, header: list[str], rows: list[list[str]], numeric: list[bool] | None = None,
              sheet: str = "Year table") -> None:
    """A one-sheet spreadsheet: a bold header row, then the rows. Columns
    marked ``numeric`` hold money ("12,389") or rates ("16.0%")."""
    numeric = numeric if numeric is not None else [True] * len(header)
    styles = (
        "<office:automatic-styles>"
        '<number:number-style style:name="N0"><number:number number:decimal-places="0" '
        'number:min-decimal-places="0" number:min-integer-digits="1" number:grouping="true"/>'
        "</number:number-style>"
        '<number:percentage-style style:name="P1"><number:number number:decimal-places="1" '
        'number:min-decimal-places="1" number:min-integer-digits="1"/><number:text>%</number:text>'
        "</number:percentage-style>"
        '<style:style style:name="money" style:family="table-cell" style:data-style-name="N0"/>'
        '<style:style style:name="rate" style:family="table-cell" style:data-style-name="P1"/>'
        '<style:style style:name="head" style:family="table-cell">'
        '<style:text-properties fo:font-weight="bold"/></style:style>'
        "</office:automatic-styles>"
    )
    head = "".join(
        f'<table:table-cell table:style-name="head" office:value-type="string"><text:p>{escape(h)}</text:p>'
        "</table:table-cell>" for h in header
    )
    body = "".join("<table:table-row>" + "".join(_cell(t, n) for t, n in zip(row, numeric)) + "</table:table-row>" for row in rows)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {_NS} office:version="1.3">{styles}'
        f"<office:body><office:spreadsheet><table:table table:name={quoteattr(sheet[:31])}>"
        f'<table:table-column table:number-columns-repeated="{max(1, len(header))}"/>'
        f"<table:table-header-rows><table:table-row>{head}</table:table-row></table:table-header-rows>"
        f"{body}</table:table></office:spreadsheet></office:body></office:document-content>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.3">'
        '<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
        'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    with zipfile.ZipFile(path, "w") as z:
        # The mimetype entry must come first and be stored uncompressed.
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
