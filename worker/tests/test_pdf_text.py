"""What a PDF page's index holds: the text drawn inside a picture, such as a chart's
labels, and none of the text its frames hide."""

import io
from pathlib import Path
from types import SimpleNamespace

import pypdfium2
import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from docling_core.types.doc.labels import DocItemLabel
from docling_core.types.doc.page import BoundingRectangle
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from backend import embedder
from backend.locators import PdfLocator, Rect
from worker import (
    born_digital,
    hard_pages,
    ocr,
    page_reader,
    parse,
    shown_text,
    unlock,
)
from worker.content import Element
from worker.ocr import Word

DATA = Path(__file__).parents[2] / "data" / "library"
PAGE = Size(width=100, height=100)
TOPLEFT = CoordOrigin.TOPLEFT


def provenance(left: float, top: float, right: float, bottom: float, text: str):
    box = BoundingBox(
        l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT
    )
    return ProvenanceItem(page_no=1, bbox=box, charspan=(0, len(text)))


def chart_document() -> DoclingDocument:
    """A page with a title, then a chart whose labels Docling keeps as its children."""
    document = DoclingDocument(name="chart")
    document.add_page(page_no=1, size=PAGE)
    title = "Water use by month"
    document.add_text(
        label=DocItemLabel.TEXT, text=title, prov=provenance(5, 5, 95, 10, title)
    )
    chart = document.add_picture(prov=provenance(5, 20, 95, 80, ""))
    for label in ("Kitchen tap", "42%"):
        document.add_text(
            label=DocItemLabel.TEXT,
            text=label,
            prov=provenance(10, 30, 40, 35, label),
            parent=chart,
        )
    return document


def test_a_pictures_drawn_text_is_its_elements_text():
    elements = parse.pdf_elements(chart_document())
    picture = next(e for e in elements if e.kind == "picture")
    assert picture.text.splitlines() == ["Kitchen tap", "42%"]


def test_a_pictures_drawn_text_reaches_its_chunk_and_cites_the_picture():
    document = chart_document()
    elements = parse.pdf_elements(document)
    chunks = parse._chunks_of(document, elements)
    picture = next(i for i, e in enumerate(elements) if e.kind == "picture")
    holding = [c for c in chunks if "Kitchen tap" in c.text and "42%" in c.text]
    assert holding
    assert picture in holding[0].element_ordinals


def pdf_with_a_hidden_label() -> bytes:
    """A shown line, a label its frame clips away, and one placed off the page."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.drawString(72, 700, "Monthly water use by meter")
    canvas.saveState()
    frame = canvas.beginPath()
    frame.rect(300, 300, 10, 10)
    canvas.clipPath(frame, stroke=0, fill=0)
    canvas.drawString(72, 500, "Quarterly gas use by meter")
    canvas.restoreState()
    canvas.drawString(72, -40, "Off the page")
    canvas.save()
    return buffer.getvalue()


def test_text_a_frame_hides_or_the_page_leaves_out_isnt_indexed():
    pdf = pdf_with_a_hidden_label()
    document = born_digital.convert(pdf, "a.pdf")
    assert "Quarterly gas use by meter" in document.export_to_text()
    shown_text.drop_hidden_text(document, pypdfium2.PdfDocument(pdf), {})
    elements = parse.pdf_elements(document)
    text = " ".join(e.text for e in elements)
    assert "Monthly water use by meter" in text
    assert "Quarterly gas use by meter" not in text
    assert "Off the page" not in text
    chunks = parse._chunks_of(document, elements)
    assert "Quarterly gas use by meter" not in " ".join(c.text for c in chunks)


def letter_document(name: str) -> DoclingDocument:
    document = DoclingDocument(name=name)
    document.add_page(page_no=1, size=Size(width=letter[0], height=letter[1]))
    return document


def test_a_hidden_table_cell_is_left_empty_and_the_table_kept():
    pdf = pdf_with_a_hidden_label()
    shown, hidden = "Monthly water use by meter", "Quarterly gas use by meter"
    cells = [
        TableCell(
            text=text,
            bbox=BoundingBox(l=70, t=top, r=260, b=top + 16, coord_origin=TOPLEFT),
            start_row_offset_idx=row,
            end_row_offset_idx=row + 1,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        )
        for row, (text, top) in enumerate(((shown, 80), (hidden, 280)))
    ]
    document = letter_document("table")
    document.add_table(
        data=TableData(num_rows=2, num_cols=1, table_cells=cells),
        prov=provenance(60, 70, 300, 310, ""),
    )
    shown_text.drop_hidden_text(document, pypdfium2.PdfDocument(pdf), {})
    table = parse.pdf_elements(document)[0]
    assert table.kind == "table"
    assert shown in table.text
    assert hidden not in table.text


def faint_scan(with_page_number: bool) -> bytes:
    """A faded scan, with or without a typed page number in its text layer."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.setFillGray(0.97)
    canvas.rect(0, 0, *letter, stroke=0, fill=1)
    if with_page_number:
        canvas.setFillGray(0)
        canvas.drawString(300, 30, "Page 7")
    canvas.save()
    return buffer.getvalue()


@pytest.mark.parametrize("with_page_number", [False, True])
def test_text_ocr_read_is_kept_however_faint(with_page_number):
    """OCR saw it, so a faded line isn't taken for hidden text, even on a page whose
    text layer holds something else."""
    document = letter_document("scan")
    faded = "Account closed on request"
    document.add_text(
        label=DocItemLabel.TEXT, text=faded, prov=provenance(72, 72, 300, 90, faded)
    )
    read_by_ocr = {1: [Rect(x0=0.12, y0=0.09, x1=0.49, y1=0.11)]}
    pdf = faint_scan(with_page_number)
    shown_text.drop_hidden_text(document, pypdfium2.PdfDocument(pdf), read_by_ocr)
    assert faded in document.export_to_text()


def test_where_ocr_read_comes_from_doclings_parsed_pages():
    def cell(from_ocr: bool):
        box = BoundingBox(l=72, t=72, r=300, b=90, coord_origin=TOPLEFT)
        return SimpleNamespace(
            from_ocr=from_ocr, rect=BoundingRectangle.from_bounding_box(box)
        )

    page = SimpleNamespace(
        page_no=0,
        size=Size(width=letter[0], height=letter[1]),
        parsed_page=SimpleNamespace(textline_cells=[cell(True), cell(False)]),
    )
    boxes = shown_text.ocr_boxes(SimpleNamespace(pages=[page]))
    assert list(boxes) == [1]
    [box] = boxes[1]
    assert box.x0 == pytest.approx(72 / letter[0])
    assert box.y0 == pytest.approx(72 / letter[1])


def test_the_same_faint_line_from_the_text_layer_is_hidden_text():
    document = letter_document("layer")
    faded = "Account closed on request"
    document.add_text(
        label=DocItemLabel.TEXT, text=faded, prov=provenance(72, 72, 300, 90, faded)
    )
    shown_text.drop_hidden_text(document, pypdfium2.PdfDocument(faint_scan(True)), {})
    assert faded not in document.export_to_text()


def real_pdfs() -> list[Path]:
    """The fixtures that hold a PDF; a dataless placeholder holds nothing yet."""
    return sorted(p for p in DATA.rglob("*.pdf") if p.read_bytes()[:5] == b"%PDF-")


@pytest.mark.parametrize("path", real_pdfs(), ids=lambda path: path.name)
def test_no_fixture_loses_text_it_shows(library, path, monkeypatch):
    """Rotated and cropped pages included: every fixture shows all its text."""
    data = path.read_bytes()
    if unlock.needs_password("PDF", data):
        pytest.skip("a locked fixture is read once its password is given")
    kept = [e.text for e in parse.parse_pdf(data, path.name).elements]
    monkeypatch.setattr(
        shown_text, "drop_hidden_text", lambda _document, _source, _ocr: None
    )
    assert kept == [e.text for e in parse.parse_pdf(data, path.name).elements]


# ---- A second reading replaces a page unless it holds less of what it shows ----

SCAN = DATA / "Scans" / "Scanned account letter.pdf"


def seen(*texts: str) -> list[Word]:
    return [
        Word(t, 1.0, (0.1 + i * 0.1, 0.1, 0.19 + i * 0.1, 0.12))
        for i, t in enumerate(texts)
    ]


def reader_finding(text: str):
    def read(_pdf, page):
        document = DoclingDocument(name="read")
        document.add_page(page_no=page, size=PAGE)
        document.add_text(
            label=DocItemLabel.TEXT, text=text, prov=provenance(5, 5, 95, 20, text)
        )
        return document

    return read


def first_reading(kind: str, text: str) -> hard_pages._Reading:
    box = Rect(x0=0.05, y0=0.05, x1=0.95, y1=0.5)
    locator = PdfLocator(page=1, rects=[box], precision="element")
    return hard_pages._Reading([Element(kind, text, locator)])


@pytest.fixture
def second_reading(monkeypatch):
    monkeypatch.setattr(embedder, "unload", lambda: None)
    monkeypatch.setattr(parse, "unload_models", lambda: None)
    ocr.use_stand_in(lambda _image: seen("Late", "fee", "$40.00", "monthly"))
    yield
    ocr.use_stand_in(None)
    page_reader.use_stand_in(None)


def test_a_second_reading_that_drops_a_table_isnt_used(second_reading):
    reading = first_reading(
        "table", "| Late fee | $40.00 |\n| --- | --- |\n| monthly | |"
    )
    page_reader.use_stand_in(reader_finding("Late fee"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert not reading.changed
    assert reading.elements[0].kind == "table"


def test_a_second_reading_that_holds_more_of_the_page_is_used(second_reading):
    reading = first_reading("text", "Lnte fcc S4O.OO")
    page_reader.use_stand_in(reader_finding("Late fee $40.00 monthly"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert reading.changed
    assert "Late fee $40.00 monthly" in " ".join(e.text for e in reading.elements)


def test_with_nothing_seen_a_reading_that_changed_the_page_is_used(second_reading):
    ocr.use_stand_in(lambda _image: [])
    reading = first_reading("text", "the old reading")
    page_reader.use_stand_in(reader_finding("the new reading"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert reading.changed


def test_a_second_reading_that_puts_the_same_words_in_order_is_used(second_reading):
    reading = first_reading("text", "monthly $40.00 Late fee")
    page_reader.use_stand_in(reader_finding("Late fee $40.00 monthly"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert reading.changed
    assert "Late fee $40.00 monthly" in " ".join(e.text for e in reading.elements)


def test_a_second_reading_that_flattens_a_table_into_text_isnt_used(second_reading):
    reading = first_reading(
        "table", "| Late fee | $40.00 |\n| --- | --- |\n| monthly | |"
    )
    page_reader.use_stand_in(reader_finding("Late fee $40.00 monthly"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert not reading.changed
    assert reading.elements[0].kind == "table"


@pytest.mark.parametrize(
    "vision", [["Summary"], []], ids=["only the heading seen", "nothing seen"]
)
def test_a_table_is_never_flattened_whatever_vision_missed(second_reading, vision):
    ocr.use_stand_in(lambda _image: seen(*vision))
    box = Rect(x0=0.05, y0=0.05, x1=0.95, y1=0.5)
    reading = hard_pages._Reading(
        [
            Element(
                "text", "Summary", PdfLocator(page=1, rects=[box], precision="element")
            ),
            Element(
                "table",
                "| Late fee | $40.00 |\n| --- | --- |\n| monthly | |",
                PdfLocator(page=1, rects=[box], precision="element"),
            ),
        ]
    )
    page_reader.use_stand_in(reader_finding("Summary Late fee $40.00 monthly"))
    hard_pages._read_page_if_hard(SCAN.read_bytes(), 1, {}, True, reading)
    assert not reading.changed
    assert [e.kind for e in reading.elements] == ["text", "table"]
