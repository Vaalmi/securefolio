"""Which of a PDF page's text the page shows. A text layer can hold words that a
frame's clipping or a hidden layer keeps off the page, or that sit off it: an
InDesign export can carry another page's chart labels this way, which would be
found and cited on the wrong page. Such words are left out. Text OCR read is always
kept, however faint: OCR saw it."""

import math
from functools import cached_property
from typing import Any

import pypdfium2

from backend import locators
from backend.locators import Rect

# A proposed value: in the reports measured, shown text measured 62 and up, and text
# their clipping frames hide measured under 25.
SHOWN_CONTRAST = 32

OcrBoxes = dict[int, list[Rect]]


def ocr_boxes(result: Any) -> OcrBoxes:
    """Where OCR read text, page by page, from Docling's parsed pages."""
    boxes: OcrBoxes = {}
    for page in getattr(result, "pages", None) or []:
        parsed = getattr(page, "parsed_page", None)
        if parsed is None or page.size is None:
            continue
        size = (page.size.width, page.size.height)
        for cell in parsed.textline_cells:
            if cell.from_ocr:
                box = cell.rect.to_bounding_box().to_top_left_origin(size[1])
                boxes.setdefault(page.page_no + 1, []).append(
                    locators.rect((box.l, box.t, box.r, box.b), size)
                )
    return boxes


def _overlaps(a: Rect, b: Rect) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


class _Page:
    """One page, rendered only once a text-layer item on it needs looking at."""

    def __init__(self, page: pypdfium2.PdfPage, read_by_ocr: list[Rect]) -> None:
        self.page = page
        self.read_by_ocr = read_by_ocr

    @cached_property
    def image(self) -> Any:
        return self.page.render(scale=1, grayscale=True).to_pil().convert("L")

    def hides(self, rect: Rect) -> bool:
        """Text from the text layer that sits off the page, or where nothing shows."""
        if any(_overlaps(rect, box) for box in self.read_by_ocr):
            return False
        if rect.x1 <= rect.x0 or rect.y1 <= rect.y0:
            return True
        return not self._shows(rect)

    def _shows(self, rect: Rect) -> bool:
        width, height = self.image.size
        box = (
            math.floor(rect.x0 * width),
            math.floor(rect.y0 * height),
            math.ceil(rect.x1 * width),
            math.ceil(rect.y1 * height),
        )
        darkest, lightest = self.image.crop(box).getextrema()
        return lightest - darkest >= SHOWN_CONTRAST


def _rect(box: Any, document: Any, page: int) -> Rect:
    size = document.pages[page].size
    top_left = box.to_top_left_origin(size.height)
    return locators.rect(
        (top_left.l, top_left.t, top_left.r, top_left.b), (size.width, size.height)
    )


def _clear_hidden_cells(table: Any, document: Any, page: _Page, page_no: int) -> None:
    """A table keeps its shape; a cell whose text is hidden is left empty."""
    for cell in table.data.table_cells:
        if cell.bbox is None or not cell.text.strip():
            continue
        if page.hides(_rect(cell.bbox, document, page_no)):
            cell.text = ""


def _hidden_item(item: Any, document: Any, page: _Page, page_no: int) -> bool:
    text = getattr(item, "text", "")
    return bool(text.strip()) and page.hides(
        _rect(item.prov[0].bbox, document, page_no)
    )


def drop_hidden_text(
    document: Any, source: pypdfium2.PdfDocument, read_by_ocr: OcrBoxes
) -> None:
    """Leaves out the text items and table cells that the page doesn't show."""
    pages: dict[int, _Page] = {}
    hidden = []
    for item, _level in document.iterate_items(traverse_pictures=True):
        provenance = getattr(item, "prov", None)
        if not provenance:
            continue
        page_no = provenance[0].page_no
        if page_no not in pages:
            pages[page_no] = _Page(source[page_no - 1], read_by_ocr.get(page_no, []))
        page = pages[page_no]
        name = type(item).__name__
        if name == "TableItem":
            _clear_hidden_cells(item, document, page, page_no)
        elif name != "PictureItem" and _hidden_item(item, document, page, page_no):
            hidden.append(item)
    if hidden:
        document.delete_items(node_items=hidden)
