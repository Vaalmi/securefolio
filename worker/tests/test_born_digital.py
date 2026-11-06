"""The path for simple born-digital pages, with no models."""

import io
from typing import Any

import pypdfium2
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from worker import born_digital


def pdf_with(draw) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    draw(canvas)
    canvas.save()
    return buffer.getvalue()


def first_page(pdf: bytes):
    return pypdfium2.PdfDocument(pdf)[0]


def paragraphs(canvas: Canvas) -> None:
    canvas.drawString(72, 700, "The first paragraph starts here")
    canvas.drawString(72, 686, "and carries on here.")
    canvas.drawString(72, 600, "A second paragraph.")


def test_a_plain_page_is_simple_and_keeps_its_text_and_boxes():
    pdf = pdf_with(paragraphs)
    assert born_digital.is_simple_page(first_page(pdf))
    document = born_digital.convert(pdf, "a.pdf")
    items: list[Any] = [item for item, _ in document.iterate_items()]
    assert [item.text for item in items] == [
        "The first paragraph starts here and carries on here.",
        "A second paragraph.",
    ]
    assert items[0].prov[0].page_no == 1
    assert items[0].prov[0].bbox.t > items[1].prov[0].bbox.t


def test_two_columns_arent_simple():
    def columns(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "Left")
        canvas.drawString(400, 700, "Right")

    assert not born_digital.is_simple_page(first_page(pdf_with(columns)))


def test_a_ruling_line_isnt_simple():
    def ruled(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "Text")
        canvas.line(72, 690, 400, 690)

    assert not born_digital.is_simple_page(first_page(pdf_with(ruled)))


def test_a_page_with_no_text_isnt_simple():
    assert not born_digital.is_simple_page(first_page(pdf_with(lambda c: c.showPage())))


def test_a_heading_or_a_list_isnt_simple():
    def heading(canvas: Canvas) -> None:
        canvas.setFont("Helvetica", 18)
        canvas.drawString(72, 700, "A heading")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(72, 660, "A paragraph.")

    def bullets(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "- an item")

    def bold_same_size(canvas: Canvas) -> None:
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(72, 700, "A bold heading.")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(72, 660, "A paragraph.")

    def lettered(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "a) the first item.")

    def roman(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "iv. the fourth item.")

    def plain_heading(canvas: Canvas) -> None:
        canvas.drawString(72, 700, "Terms of the lease")
        canvas.drawString(72, 660, "A paragraph.")

    for draw in (heading, bullets, bold_same_size, lettered, roman, plain_heading):
        assert not born_digital.is_simple_page(first_page(pdf_with(draw)))


def test_a_rotated_page_isnt_simple():
    document = pypdfium2.PdfDocument(pdf_with(paragraphs))
    document[0].set_rotation(90)
    assert not born_digital.is_simple_page(document[0])


def test_a_blank_line_between_paragraphs_splits_them():
    def blank_lines(canvas: Canvas) -> None:
        for y in (700, 686, 672, 644, 630, 616, 588):
            canvas.drawString(72, y, f"A line of text at height {y}")

    groups = born_digital._paragraphs(
        born_digital._text_lines(first_page(pdf_with(blank_lines)))
    )
    assert [len(group) for group in groups] == [3, 3, 1]
