"""XLSX and CSV without Docling: a streaming read, one element per row, chunks of 25
rows headed by file, sheet and column names, capped at 10,000 rows a sheet."""

import csv
import io
from dataclasses import dataclass

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from backend.locators import SheetLocator
from worker.content import (
    MAX_TOKENS,
    Chunk,
    Element,
    Parsed,
    estimate_tokens,
    split_to_limit,
)

ROWS_PER_CHUNK = 25
MAX_ROWS = 10_000
MAX_HEADING_TOKENS = 256
CSV_SHEET = "Sheet1"


@dataclass
class _Sheet:
    name: str
    rows: list[list[str]]
    capped: bool


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _sheets_of_workbook(data: bytes) -> list[_Sheet]:
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = []
    for sheet in workbook.worksheets:
        rows: list[list[str]] = []
        capped = False
        for row in sheet.iter_rows(values_only=True):
            if len(rows) == MAX_ROWS:
                capped = True
                break
            rows.append([_text(value) for value in row])
        sheets.append(_Sheet(sheet.title, rows, capped))
    workbook.close()
    return sheets


def _sheet_of_csv(data: bytes) -> list[_Sheet]:
    rows: list[list[str]] = []
    capped = False
    reader = csv.reader(io.StringIO(data.decode("utf-8", errors="replace")))
    for row in reader:
        if len(rows) == MAX_ROWS:
            capped = True
            break
        rows.append(row)
    return [_Sheet(CSV_SHEET, rows, capped)]


def read(kind: str, data: bytes, file_name: str) -> Parsed:
    sheets = _sheets_of_workbook(data) if kind == "XLSX" else _sheet_of_csv(data)
    elements: list[Element] = []
    chunks: list[Chunk] = []
    for sheet in sheets:
        _read_sheet(sheet, file_name, elements, chunks)
    return Parsed(elements, chunks)


def _read_sheet(
    sheet: _Sheet, file_name: str, elements: list[Element], chunks: list[Chunk]
) -> None:
    """Row numbers stay the sheet's own, blank rows included, so a citation points
    at the right cells. A chunk ends at 25 rows or the token ceiling."""
    numbered = [
        (number, row)
        for number, row in enumerate(sheet.rows, start=1)
        if any(cell.strip() for cell in row)
    ]
    if not numbered:
        return
    header = numbered[0][1]
    note = " (first 10,000 rows)" if sheet.capped else ""
    full = f"{file_name}, sheet {sheet.name}{note}. Columns: {', '.join(header)}"
    # A very wide sheet's heading can't be allowed to take the whole chunk.
    heading = split_to_limit(full, MAX_HEADING_TOKENS)[0]
    lines = [heading]
    ordinals: list[int] = []

    def flush() -> None:
        if ordinals:
            chunks.append(Chunk("\n".join(lines), list(ordinals)))
        lines[1:] = []
        ordinals.clear()

    for number, row in numbered:
        last = get_column_letter(max(len(row), 1))
        locator = SheetLocator(sheet=sheet.name, range=f"A{number}:{last}{number}")
        ordinal = len(elements)
        text = " | ".join(row)
        elements.append(Element("text", text, locator))
        for piece in split_to_limit(text, MAX_TOKENS - estimate_tokens(heading)):
            size = estimate_tokens("\n".join([*lines, piece]))
            if len(ordinals) >= ROWS_PER_CHUNK or size > MAX_TOKENS:
                flush()
            lines.append(piece)
            ordinals.append(ordinal)
    flush()
