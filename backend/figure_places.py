"""Where a sentence's shown figures are in the element it cites, for the viewer to
highlight: on a PDF page with a text layer, pdfium's character boxes; in a text kind,
character ranges in the element's text. A figure is placed only when its passage's
spelling is found there exactly once, and on a page only inside the element's own
box, so a place is never a guess. A page whose text layer the first index found
broken, empty or garbled, a picture, a chart, a slide, a sheet row or a card places
nothing, and the citation opens at its passage as before. The figures come from the
saved answer, never from the request."""

import threading
from dataclasses import dataclass
from typing import Any

import pypdfium2
import sqlalchemy as sa

from backend import database, documents, elements, files, threads

TEXT_KINDS = ("DOCX", "MD", "TXT", "EML", "HTML")
SLACK = 0.004  # of the page: a character's box may touch its element's edge
# PDFium can't be called from two threads at once, not even on two documents, and
# each request runs on a thread of its own: one placing at a time, with all it opened
# closed before the next, never left to a finalizer on another thread.
_pdfium = threading.Lock()


@dataclass(frozen=True)
class Shown:
    text: str  # as the sentence says it
    found: str  # as the passage spells it


def shown_in(message_id: str, sentence: int, element_id: str) -> list[Shown]:
    """The figures that sentence of that answer shows in a passage citing this
    element, with the passage's own spelling."""
    saved = threads.message(message_id)
    answer = (saved or {}).get("answer") or {}
    parts = answer.get("parts") or []
    if not 0 <= sentence < len(parts):
        return []
    citing = {
        c["n"] for c in answer.get("citations") or [] if c["element_id"] == element_id
    }
    return [
        Shown(f["text"], f["found"])
        for f in parts[sentence].get("figures") or []
        if f["state"] == "shown" and f.get("cite") in citing and f.get("found")
    ]


def _untrusted_text_layer(document_id: str, page: int) -> bool:
    """Whether the first index found the active revision's page's text layer broken,
    empty or garbled: where its words sit can't be trusted, so nothing on it is
    placed."""
    with database.read() as connection:
        reasons = connection.execute(
            sa.select(database.page_readings.c.reasons)
            .join(
                database.documents,
                database.documents.c.active_revision_id
                == database.page_readings.c.revision_id,
            )
            .where(
                database.documents.c.id == document_id,
                database.page_readings.c.page == page,
            )
        ).scalar()
    found = reasons or {}
    return bool(found.get("broken_text_layer") or found.get("empty_or_garbled"))


def _inside(box: dict[str, float], rects: list[dict[str, float]]) -> bool:
    return any(
        box["x0"] >= r["x0"] - SLACK
        and box["y0"] >= r["y0"] - SLACK
        and box["x1"] <= r["x1"] + SLACK
        and box["y1"] <= r["y1"] + SLACK
        for r in rects
    )


def _lines(boxes: list[dict[str, float]]) -> list[dict[str, float]]:
    """A figure's character boxes as one box for each line it runs across."""
    lines: list[dict[str, float]] = []
    for box in boxes:
        middle = (box["y0"] + box["y1"]) / 2
        line = next((x for x in lines if x["y0"] <= middle <= x["y1"]), None)
        if line is None:
            lines.append(dict(box))
        else:
            line.update(
                x0=min(line["x0"], box["x0"]),
                y0=min(line["y0"], box["y0"]),
                x1=max(line["x1"], box["x1"]),
                y1=max(line["y1"], box["y1"]),
            )
    return lines


def _hits(
    textpage: Any, found: str, shown: tuple[float, float, float, float]
) -> list[list[dict[str, float]]]:
    """Each place the page's text layer holds the spelling, as character boxes from
    the shown page's top left, in fractions of it: pdfium gives the whole page's
    coordinates, and only the shown box is drawn, as the index places its
    passages."""
    left_edge, bottom_edge, right_edge, top_edge = shown
    width, height = right_edge - left_edge, top_edge - bottom_edge
    hits = []
    searcher = textpage.search(found, match_case=True)
    while (hit := searcher.get_next()) is not None:
        start, count = hit
        boxes = []
        for at in range(start, start + count):
            left, bottom, right, top = textpage.get_charbox(at)
            boxes.append(
                {
                    "x0": (left - left_edge) / width,
                    "y0": 1 - (top - bottom_edge) / height,
                    "x1": (right - left_edge) / width,
                    "y1": 1 - (bottom - bottom_edge) / height,
                }
            )
        hits.append(boxes)
    return hits


def pdf_places(
    data: bytes, locator: dict[str, Any], figures: list[Shown]
) -> list[dict[str, Any]]:
    """Each figure found exactly once inside the element's box on its page."""
    with _pdfium:
        try:
            pdf = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:  # locked, or nothing pdfium can open
            return []
        try:
            return _on_page(pdf[int(locator["page"]) - 1], locator, figures)
        finally:
            pdf.close()  # its page, text page and searches with it


def _on_page(
    page: pypdfium2.PdfPage, locator: dict[str, Any], figures: list[Shown]
) -> list[dict[str, Any]]:
    if page.get_rotation():  # its boxes would need turning: never guessed
        return []
    textpage = page.get_textpage()
    # The page as pdfium shows it, its media box and crop box intersected, whichever
    # of them a producer left larger: the frame the index counts its passages in.
    shown = page.get_bbox()
    rects = locator.get("rects") or []
    placed = []
    for figure in figures:
        inside = [
            boxes
            for boxes in _hits(textpage, figure.found, shown)
            if boxes and all(_inside(box, rects) for box in boxes)
        ]
        if len(inside) == 1:
            placed.append(
                {"kind": "pdf", "text": figure.text, "rects": _lines(inside[0])}
            )
    return placed


def text_places(text: str, figures: list[Shown]) -> list[dict[str, Any]]:
    """Each figure found exactly once in the element's text, by characters."""
    placed = []
    for figure in figures:
        at = text.find(figure.found)
        if at >= 0 and text.find(figure.found, at + 1) < 0:
            placed.append(
                {
                    "kind": "text",
                    "text": figure.text,
                    "range": [at, at + len(figure.found)],
                }
            )
    return placed


def _on_its_page(
    document_id: str,
    revision: str | None,
    locator: dict[str, Any],
    figures: list[Shown],
) -> list[dict[str, Any]]:
    """The figures on the cited PDF page, from the file of the revision `places`
    first read, or nothing."""
    if _untrusted_text_layer(document_id, int(locator["page"])):
        return []
    data = files.serve(document_id).data
    # Each read above looks up the active revision on its own. One published between
    # them would put an older element's box on newer bytes; a replaced revision never
    # comes back, so the same one now means the same one throughout.
    if documents.get(document_id)["active_revision_id"] != revision:
        return []
    return pdf_places(data, locator, figures)


def places(
    document_id: str, element_id: str, message_id: str, sentence: int
) -> list[dict[str, Any]]:
    """Each shown figure of the sentence placed in the element, as the active
    revision holds it, or nothing for it."""
    figures = shown_in(message_id, sentence, element_id)
    if not figures:
        return []
    document = documents.get(document_id)
    revision = document["active_revision_id"]
    cited = elements.cited(document_id, element_id)
    element = cited["element"]
    # A picture's words, a chart's included, describe it: none sits on the page.
    if element is None or not cited["current"] or element["kind"] == "picture":
        return []
    kind = document["file_kind"]
    locator = element["locator"]
    if kind == "PDF" and locator.get("kind") == "pdf" and locator.get("rects"):
        return _on_its_page(document_id, revision, locator, figures)
    if kind in TEXT_KINDS:
        return text_places(element["text"], figures)
    return []
