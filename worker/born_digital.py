"""Born-digital single-column pages skip the layout models. Docling has no switch
for that, so this builds a DoclingDocument from the page's own text lines, which
the shared chunker and locators then treat like any other."""

import ctypes
import itertools
import re
from dataclasses import dataclass

import pypdfium2
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel
from pypdfium2 import raw as pdfium

COLUMN_GAP_SHARE = 0.05  # no line holds two text cells further apart than this
PARAGRAPH_GAP_LINES = 1.6
LOOSE_PITCH = 1.25
LIST_START = re.compile(r"^\s*([-•*–]|\(?[0-9a-zA-Z]{1,4}[.)])\s")


@dataclass(frozen=True)
class Line:
    text: str
    left: float
    bottom: float
    right: float
    top: float


def _text_lines(page: pypdfium2.PdfPage) -> list[Line]:
    textpage = page.get_textpage()
    lines = []
    for index in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(index)
        text = textpage.get_text_bounded(left, bottom, right, top).strip()
        if text:
            lines.append(Line(text, left, bottom, right, top))
    lines.sort(key=lambda line: (-round(line.top, 1), line.left))
    return lines


def _has_graphics(page: pypdfium2.PdfPage) -> bool:
    """Ruling lines or images: anything that isn't text."""
    objects = page.get_objects(max_depth=2)
    return any(item.type != pdfium.FPDF_PAGEOBJ_TEXT for item in objects)  # pyright: ignore[reportAttributeAccessIssue]


def _holds_columns(lines: list[Line], width: float) -> bool:
    for current, following in zip(lines, lines[1:], strict=False):
        same_line = abs(current.top - following.top) < 1
        if same_line and following.left - current.right > COLUMN_GAP_SHARE * width:
            return True
    return False


def _has_structure(page: pypdfium2.PdfPage, lines: list[Line]) -> bool:
    """Headings or lists: this path has no way to label them, so the layout models
    read such a page. One font size and no bullets means plain paragraphs."""
    fonts = set()
    for item in page.get_objects(max_depth=2):
        size = ctypes.c_float()
        if pdfium.FPDFTextObj_GetFontSize(item.raw, size):
            font = pdfium.FPDFTextObj_GetFont(item.raw)
            name = ctypes.create_string_buffer(128)
            pdfium.FPDFFont_GetBaseFontName(font, name, len(name))
            fonts.add((round(size.value, 1), name.value))
    # A change of font or size, a list marker, or a line standing alone with
    # no end punctuation (a heading in the body font) all mean structure.
    return len(fonts) > 1 or any(
        LIST_START.match(line.text) or _is_lone_heading(line, lines) for line in lines
    )


def _is_lone_heading(line: Line, lines: list[Line]) -> bool:
    groups = _paragraphs(lines)
    return any(group == [line] and line.text[-1] not in ".!?:;" for group in groups)


def is_simple_page(page: pypdfium2.PdfPage) -> bool:
    """A page with a text layer, upright and uncropped, in one column of plain
    paragraphs, with no ruling lines and no images."""
    width, height = page.get_size()
    if page.get_rotation() != 0 or page.get_cropbox() != (0, 0, width, height):
        return False
    lines = _text_lines(page)
    return (
        bool(lines)
        and not _has_graphics(page)
        and not _holds_columns(lines, width)
        and not _has_structure(page, lines)
    )


def _paragraphs(lines: list[Line]) -> list[list[Line]]:
    """A new paragraph where a line starts further below the last than the page's
    ordinary line spacing, or where the gap is more than a line and a half. The page's
    y grows upward, and its lines come sorted from the top."""
    pitches = sorted(a.top - b.top for a, b in itertools.pairwise(lines))
    usual = pitches[len(pitches) // 4] if pitches else 0.0
    groups: list[list[Line]] = []
    for line in lines:
        if groups and _same_paragraph(groups[-1][-1], line, usual):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _same_paragraph(last: Line, line: Line, usual: float) -> bool:
    height = max(line.top - line.bottom, 1.0)
    if last.bottom - line.top >= PARAGRAPH_GAP_LINES * height:
        return False
    return last.top - line.top <= usual * LOOSE_PITCH


def add_page(document: DoclingDocument, page: pypdfium2.PdfPage, number: int) -> None:
    width, height = page.get_size()
    document.add_page(page_no=number, size=Size(width=width, height=height))
    for group in _paragraphs(_text_lines(page)):
        text = " ".join(line.text for line in group)
        box = BoundingBox(
            l=min(line.left for line in group),
            t=max(line.top for line in group),
            r=max(line.right for line in group),
            b=min(line.bottom for line in group),
            coord_origin=CoordOrigin.BOTTOMLEFT,
        )
        provenance = ProvenanceItem(page_no=number, bbox=box, charspan=(0, len(text)))
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=provenance)


def convert(pdf: bytes, name: str, pages: list[int] | None = None) -> DoclingDocument:
    """A document of the given simple pages (1-based), read from their text alone."""
    source = pypdfium2.PdfDocument(pdf)
    document = DoclingDocument(name=name)
    for number in pages or range(1, len(source) + 1):
        add_page(document, source[number - 1], number)
    return document
