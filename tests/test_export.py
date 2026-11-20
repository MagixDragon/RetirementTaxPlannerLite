"""Year table export: CSV and OpenDocument spreadsheets."""

import csv
import zipfile
import xml.etree.ElementTree as ET

from rtpl.export import write_csv, write_ods

OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"

HEADER = ["Year", "Ages", "Spending", "Marginal rate", "Notes"]
ROWS = [["2026", "Alex 62, Sam 60", "110,000", "16.0%", ""],
        ["2027", "Alex 63, Sam 61", "-1,772", "31.8%", "Short $5 & <more>"]]
NUMERIC = [False, False, True, True, False]


def test_csv(tmp_path):
    path = tmp_path / "t.csv"
    write_csv(path, HEADER, ROWS)
    with open(path, newline="", encoding="utf-8") as fh:
        assert list(csv.reader(fh)) == [HEADER, *ROWS]


def test_ods_is_a_valid_spreadsheet_with_numbers(tmp_path):
    path = tmp_path / "t.ods"
    write_ods(path, HEADER, ROWS, NUMERIC)
    with zipfile.ZipFile(path) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"
        ET.fromstring(z.read("META-INF/manifest.xml"))
        root = ET.fromstring(z.read("content.xml"))
    rows = root.iter(f"{TABLE}table-row")
    head = next(rows)
    assert ["".join(c.itertext()) for c in head] == HEADER
    cells = list(next(rows))
    assert cells[0].get(f"{OFFICE}value") == "2026" and cells[0].get(f"{TABLE}style-name") is None
    assert cells[1].get(f"{OFFICE}value-type") == "string"
    assert cells[2].get(f"{OFFICE}value") == "110000" and cells[2].get(f"{TABLE}style-name") == "money"
    assert cells[3].get(f"{OFFICE}value-type") == "percentage" and float(cells[3].get(f"{OFFICE}value")) == 0.16
    cells = list(next(rows))
    assert cells[2].get(f"{OFFICE}value") == "-1772"
    assert "".join(cells[4].itertext()) == "Short $5 & <more>"
