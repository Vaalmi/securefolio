"""Turns a file's bytes into elements with locators. Docling reads most formats;
born-digital plain pages skip its layout models. The checks use recorded Docling
output for anything that would need a model."""

import importlib
import io
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pypdfium2

from backend import locators
from backend.locators import AnchorLocator, PdfLocator, Rect, SlideLocator
from worker import (
    born_digital,
    emails,
    html_anchors,
    picture_text,
    shown_text,
    spreadsheets,
    text_lines,
)
from worker.content import (
    TARGET_TOKENS,
    Chunk,
    Element,
    PageFacts,
    Parsed,
    estimate_tokens,
    split_to_limit,
)
from worker.hard_pages import normalize

MAX_PAGES_PER_BATCH = 8
GARBLED_SHARE = 0.5
Converter = Callable[[str, bytes, tuple[int, int] | None], Any]
_recorded: list[Callable[[str, bytes, tuple[int, int] | None], Any]] = []
PICTURE_SHARE = 0.05
_last_used: list[float] = []
KIND_OF_SUFFIX = {".png": "PNG", ".jpeg": "JPEG", ".heic": "HEIC", ".webp": "WEBP"}


class CantRead(Exception):
    """A file Docling or a reader couldn't make sense of; its message is plain."""


def unload_models() -> None:
    """Frees the layout models before a page-reading model loads."""
    if "worker.docling_engine" in sys.modules:
        _engine().unload_converters()
    _last_used.clear()


def unload_if_idle(now: float, idle_seconds: float) -> None:
    if _last_used and now - _last_used[0] >= idle_seconds:
        unload_models()


def use_recorded(converter: Converter | None) -> None:
    """Stands in for Docling's model-backed conversion, in the checks."""
    _recorded[:] = [converter] if converter else []


def _engine() -> Any:
    return importlib.import_module("worker.docling_engine")


def _convert(name: str, data: bytes, page_range: tuple[int, int] | None = None) -> Any:
    if _recorded:
        return _recorded[0](name, data, page_range)
    engine = _engine()
    _last_used[:] = [time.monotonic()]
    source = engine.stream_of(name, io.BytesIO(data))
    arguments = {"page_range": page_range} if page_range else {}
    return engine.full_converter().convert(source, **arguments)


def _to_rect(item_prov: Any, document: Any) -> Rect:
    size = document.pages[item_prov.page_no].size
    box = item_prov.bbox.to_top_left_origin(size.height)
    return locators.rect((box.l, box.t, box.r, box.b), (size.width, size.height))


def _element_kind(item: Any) -> str:
    name = type(item).__name__
    if name == "TableItem":
        return "table"
    return "picture" if name == "PictureItem" else "text"


def _item_text(item: Any, document: Any) -> str:
    kind = _element_kind(item)
    if kind == "table":
        return item.export_to_markdown(doc=document)
    if kind == "picture":
        return picture_text.text_of(item, document)
    return item.text


def _picture_needs_reading(item: Any, rect: Rect) -> bool:
    """Charts, and other pictures over 5% of the page, get read after publish."""
    area = (rect.x1 - rect.x0) * (rect.y1 - rect.y0)
    return _is_chart(item) or area > PICTURE_SHARE


def _is_chart(item: Any) -> bool:
    for meta in getattr(item, "annotations", []):
        classes = getattr(meta, "predicted_classes", None) or []
        if classes and "chart" in classes[0].class_name.lower():
            return True
    meta = getattr(item, "meta", None)
    classification = getattr(meta, "classification", None)
    predictions = getattr(classification, "predictions", None) or []
    return bool(predictions) and "chart" in predictions[0].class_name.lower()


def pdf_elements(document: Any) -> list[Element]:
    found = []
    for item, _level in document.iterate_items():
        provenance = getattr(item, "prov", None)
        if not provenance:
            continue
        rect = _to_rect(provenance[0], document)
        kind = _element_kind(item)
        text = _item_text(item, document)
        if kind != "picture" and not text.strip():
            continue
        # A native or OCR word box is only known for what grounding proves; the
        # first index cites the element's own box.
        locator = PdfLocator(
            page=provenance[0].page_no, rects=[rect], precision="element"
        )
        element = Element(kind, text, locator, self_ref=item.self_ref)
        if kind == "picture":
            element.reading_state = (
                "queued" if _picture_needs_reading(item, rect) else None
            )
        found.append(element)
    return found


def _chunks_of(document: Any, elements: list[Element]) -> list[Chunk]:
    by_ref = {e.self_ref: i for i, e in enumerate(elements) if e.self_ref}
    engine = _engine()
    chunker = engine.chunker()
    found = []
    for piece in chunker.chunk(document):
        ordinals = [
            by_ref[item.self_ref]
            for item in piece.meta.doc_items
            if item.self_ref in by_ref
        ]
        found.append(Chunk(chunker.contextualize(piece), ordinals))
    return found


def _table_facts(item: Any, facts: PageFacts) -> None:
    cells = item.data.table_cells
    if cells:
        empty = sum(not cell.text.strip() for cell in cells) / len(cells)
        facts.tables_empty_share.append(empty)
    if any(cell.row_span > 1 or cell.col_span > 1 for cell in cells):
        facts.table_has_span = True


def _facts_of(result: Any, pages: list[int]) -> list[PageFacts]:
    facts = {page: PageFacts(page) for page in pages}
    confidence = getattr(getattr(result, "confidence", None), "pages", {}) or {}
    for page, scores in confidence.items():
        if page in facts:
            facts[page].mean_grade = str(scores.mean_grade.value)
            facts[page].low_grade = str(scores.low_grade.value)
    for parsed_page in getattr(result, "pages", []) or []:
        if parsed_page.page_no + 1 in facts and getattr(
            parsed_page, "_broken_text_layer", False
        ):
            facts[parsed_page.page_no + 1].broken_text_layer = True
    document = result.document
    for item, _level in document.iterate_items():
        provenance = getattr(item, "prov", None)
        if not provenance or provenance[0].page_no not in facts:
            continue
        page = facts[provenance[0].page_no]
        kind = _element_kind(item)
        if kind == "table":
            _table_facts(item, page)
        elif kind == "picture":
            rect = _to_rect(provenance[0], document)
            page.picture_boxes.append([rect.x0, rect.y0, rect.x1, rect.y1])
    _mark_continued_tables(document, facts)
    return list(facts.values())


def _mark_continued_tables(document: Any, facts: dict[int, PageFacts]) -> None:
    """A table last on one page and first on the next, with as many columns."""
    tables = [
        item
        for item, _level in document.iterate_items()
        if _element_kind(item) == "table" and getattr(item, "prov", None)
    ]
    for first, second in zip(tables, tables[1:], strict=False):
        same_width = first.data.num_cols == second.data.num_cols
        if same_width and second.prov[0].page_no == first.prov[0].page_no + 1:
            for table in (first, second):
                if table.prov[0].page_no in facts:
                    facts[table.prov[0].page_no].table_continues = True


def _page_runs(pages: list[int]) -> list[tuple[int, int]]:
    """Consecutive pages as (first, last), at most a batch of 8 each."""
    runs: list[tuple[int, int]] = []
    for page in pages:
        if (
            runs
            and page == runs[-1][1] + 1
            and page - runs[-1][0] < MAX_PAGES_PER_BATCH
        ):
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


def _offset_chunks(pieces: list[Chunk], offset: int) -> list[Chunk]:
    return [Chunk(c.text, [o + offset for o in c.element_ordinals]) for c in pieces]


def parse_pdf(data: bytes, name: str) -> Parsed:
    source = pypdfium2.PdfDocument(data)
    try:
        return _parse_pdf(data, name, source)
    finally:
        source.close()


def _parse_pdf(data: bytes, name: str, source: pypdfium2.PdfDocument) -> Parsed:
    total = len(source)
    simple = [
        n for n in range(1, total + 1) if born_digital.is_simple_page(source[n - 1])
    ]
    hard = [n for n in range(1, total + 1) if n not in simple]
    parts: list[tuple[int, Any, list[PageFacts]]] = []
    for first, last in _page_runs(simple):
        run = list(range(first, last + 1))
        document = born_digital.convert(data, name, run)
        shown_text.drop_hidden_text(document, source, {})  # its text is the layer's
        parts.append((first, document, [PageFacts(n) for n in run]))
    for first, last in _page_runs(hard):
        result = _convert(name, data, (first, last))
        # The routing's facts come from what Docling read, before anything is left out.
        run_facts = _facts_of(result, list(range(first, last + 1)))
        document = result.document
        shown_text.drop_hidden_text(document, source, shown_text.ocr_boxes(result))
        parts.append((first, document, run_facts))
    parts.sort(key=lambda part: part[0])
    elements: list[Element] = []
    chunks: list[Chunk] = []
    facts: list[PageFacts] = []
    for _first, document, page_facts in parts:
        found = pdf_elements(document)
        for fact in page_facts:
            on_page = [e for e in found if getattr(e.locator, "page", 0) == fact.page]
            fact.empty_or_garbled = _is_empty_or_garbled(on_page)
        chunks += _offset_chunks(_chunks_of(document, found), len(elements))
        elements += found
        facts += page_facts
    return Parsed(elements, chunks, total, facts)


def _is_empty_or_garbled(on_page: list[Element]) -> bool:
    text = " ".join(e.text for e in on_page if e.kind != "picture")
    if any(e.kind == "picture" for e in on_page) and not text.strip():
        return False
    if not text.strip():
        return True
    letters = sum(c.isalnum() or c.isspace() for c in text)
    return letters / len(text) < GARBLED_SHARE


def parse_image(data: bytes, name: str, kind: str) -> Parsed:
    png = _to_png(data, kind)
    result = _convert(Path(name).with_suffix(".png").name, png, None)
    elements = pdf_elements(result.document)
    return Parsed(elements, _chunks_of(result.document, elements), 1, [])


def _to_png(data: bytes, kind: str) -> bytes:
    if kind == "PNG":
        return data
    image_module = importlib.import_module("PIL.Image")
    if kind == "HEIC":
        importlib.import_module("pillow_heif").register_heif_opener()
    buffer = io.BytesIO()
    image_module.open(io.BytesIO(data)).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def _simple_docling(name: str, data: bytes) -> Any:
    """DOCX, PPTX and HTML take Docling's simple pipeline, which needs no model."""
    engine = _engine()
    converter = engine.DocumentConverter()
    return converter.convert(engine.stream_of(name, io.BytesIO(data))).document


def parse_docx(data: bytes, name: str) -> Parsed:
    document = _simple_docling(name, data)
    return _anchored(document, {})


def parse_html(data: bytes, name: str) -> Parsed:
    document = _simple_docling(name, data)
    return _anchored(document, html_anchors.anchors_by_text(data))


def _anchored(document: Any, anchors: dict[str, str]) -> Parsed:
    """Elements with DOM anchors: an anchor found in the page, or the element's place
    in the extracted structure the viewer shows."""
    elements = []
    for item, _level in document.iterate_items():
        kind = _element_kind(item)
        text = _item_text(item, document)
        if not text.strip():
            continue
        anchor = anchors.get(normalize(text), f"e{len(elements)}")
        elements.append(
            Element(kind, text, AnchorLocator(anchor=anchor), self_ref=item.self_ref)
        )
    return Parsed(elements, _chunks_of(document, elements))


def parse_pptx(data: bytes, name: str) -> Parsed:
    document = _simple_docling(name, data)
    elements = []
    for item, _level in document.iterate_items():
        provenance = getattr(item, "prov", None)
        kind = _element_kind(item)
        text = _item_text(item, document)
        if not text.strip() or not provenance:
            continue
        locator = SlideLocator(slide=provenance[0].page_no)
        elements.append(Element(kind, text, locator, self_ref=item.self_ref))
    return Parsed(elements, _chunks_of(document, elements), len(document.pages))


def _chunks_by_size(elements: list[Element]) -> list[Chunk]:
    """Own readers' elements, gathered to about 512 tokens, none over the ceiling."""
    chunks: list[Chunk] = []
    text: list[str] = []
    ordinals: list[int] = []

    def flush() -> None:
        if text:
            chunks.append(Chunk("\n\n".join(text), list(ordinals)))
        text.clear()
        ordinals.clear()

    for ordinal, element in enumerate(elements):
        for piece in split_to_limit(element.text):
            if estimate_tokens("\n\n".join([*text, piece])) > TARGET_TOKENS:
                flush()
            text.append(piece)
            ordinals.append(ordinal)
    flush()
    return chunks


def parse(kind: str, data: bytes, name: str) -> Parsed:
    try:
        return _parse(kind, data, name)
    except CantRead:
        raise
    except Exception as error:  # Any library's failure on a bad file reads the same.
        raise CantRead(
            "SecureFolio couldn't read this file. It may be damaged."
        ) from error


def _parse_text(data: bytes, _name: str) -> Parsed:
    elements = text_lines.read(data)
    return Parsed(elements, _chunks_by_size(elements))


def _parse_email(data: bytes, _name: str) -> Parsed:
    elements = emails.read(data)
    return Parsed(elements, _chunks_by_size(elements))


PARSERS = {
    "PDF": parse_pdf,
    "DOCX": parse_docx,
    "PPTX": parse_pptx,
    "HTML": parse_html,
    "MD": _parse_text,
    "TXT": _parse_text,
    "EML": _parse_email,
}


def _parse(kind: str, data: bytes, name: str) -> Parsed:
    if kind in ("XLSX", "CSV"):
        return spreadsheets.read(kind, data, name)
    if kind in PARSERS:
        return PARSERS[kind](data, name)
    return parse_image(data, name, kind)
