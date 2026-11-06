"""The only code that decides which pages are hard and runs their second reading,
charts, figures, and grounding: whether a passage can be highlighted, or only its
area shown. Runs after publish, so a document is searchable first."""

import hashlib
import importlib
import io
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pypdfium2
import sqlalchemy as sa
from PIL import Image
from rapidfuzz.distance import Levenshtein

from agent import models
from backend import (
    database,
    documents,
    embedder,
    jobs,
    locators,
    refusals,
    revisions,
    settings,
    states,
)
from backend.database import page_readings
from backend.locators import PdfLocator, Rect
from worker import chunking, embedding, ocr, page_reader, publishing, unlock
from worker.content import Element, element_rows, estimate_tokens
from worker.ocr import Word

logger = logging.getLogger(__name__)
PENDING_PICTURES_PASS = 200
GROUNDING_RUN_WORDS = 5
TABLE_HEAD_LINES = 2

GRADES_THAT_ARE_HARD = {"fair", "poor"}


@dataclass(frozen=True)
class Thresholds:
    """Proposed values, measured on the calibration fixtures by
    scripts/measure/hard_pages.py."""

    character_error_rate: float = 0.10
    coverage: float = 0.90
    empty_table_cells: float = 0.20
    picture_area: float = 0.50


THRESHOLDS = Thresholds()

Box = tuple[float, float, float, float]


@dataclass
class PageSignals:
    """What's known about one page, from Docling and a fast read. A score Docling
    leaves unset stays None or NaN, and is ignored."""

    mean_grade: str | None = None
    low_grade: str | None = None
    broken_text_layer: bool = False
    has_native_text: bool = False
    text_cells: list[Word] = field(default_factory=list)
    vision_words: list[Word] = field(default_factory=list)
    picture_boxes: list[Box] = field(default_factory=list)
    tables_empty_share: list[float] = field(default_factory=list)
    table_has_span: bool = False
    table_continues: bool = False
    empty_or_garbled_after_ocr: bool = False
    owner_asked: bool = False


def normalize(text: str) -> str:
    joined = re.sub(r"-\s*\n\s*", "", text)
    folded = unicodedata.normalize("NFKC", joined).casefold()
    return re.sub(r"\s+", " ", folded).strip()


def _center_inside(box: Box, outer: Box) -> bool:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _outside_pictures(words: list[Word], pictures: list[Box]) -> list[Word]:
    return [w for w in words if not any(_center_inside(w.box, p) for p in pictures)]


def _reading_order(words: list[Word]) -> str:
    ordered = sorted(words, key=lambda w: (round(w.box[1], 2), w.box[0]))
    return normalize(" ".join(w.text for w in ordered))


def character_error_rate(text_layer: str, vision: str) -> float:
    """Edit distance over the length of the Apple Vision read."""
    if not vision:
        return 0.0
    return Levenshtein.distance(text_layer, vision) / len(vision)


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clean_word(text: str) -> str:
    return normalize(text).strip(".,;:!?()[]\"'")


def _words_of(text: str) -> set[str]:
    return {_clean_word(word) for word in normalize(text).split()}


def coverage(cells: list[Word], vision: list[Word]) -> float:
    """The share of Apple Vision's characters whose word matches a text-layer word
    with an overlapping box."""
    total = sum(len(normalize(w.text)) for w in vision)
    if total == 0:
        return 1.0
    layer = [(_words_of(cell.text), cell.box) for cell in cells]
    covered = 0
    for word in vision:
        text = _clean_word(word.text)
        if any(text in words and _overlaps(word.box, box) for words, box in layer):
            covered += len(normalize(word.text))
    return covered / total


def _unset(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def text_layer_reasons(
    signals: PageSignals, limits: Thresholds = THRESHOLDS
) -> list[str]:
    if not signals.has_native_text:
        return []
    cells = _outside_pictures(signals.text_cells, signals.picture_boxes)
    vision = _outside_pictures(signals.vision_words, signals.picture_boxes)
    if not vision:
        return []
    reasons = []
    if (
        character_error_rate(_reading_order(cells), _reading_order(vision))
        > limits.character_error_rate
    ):
        reasons.append("text_layer_differs")
    if coverage(cells, vision) < limits.coverage:
        reasons.append("text_layer_partial")
    return reasons


def picture_area(boxes: list[Box]) -> float:
    return sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)


def why_hard(signals: PageSignals, limits: Thresholds = THRESHOLDS) -> list[str]:
    """The reasons a page is hard, or nothing when it isn't."""
    reasons: list[str] = []
    if signals.owner_asked:
        reasons.append("owner_asked")
    if signals.mean_grade in GRADES_THAT_ARE_HARD or signals.low_grade == "poor":
        reasons.append("low_confidence")
    if signals.broken_text_layer:
        reasons.append("broken_text_layer")
    reasons += text_layer_reasons(signals, limits)
    empty = [s for s in signals.tables_empty_share if not _unset(s)]
    if (
        any(s > limits.empty_table_cells for s in empty)
        or signals.table_has_span
        or signals.table_continues
    ):
        reasons.append("weak_table")
    if picture_area(signals.picture_boxes) > limits.picture_area:
        reasons.append("pictures_over_half")
    if signals.empty_or_garbled_after_ocr:
        reasons.append("empty_or_garbled")
    return reasons


# ---- Grounding: a highlight only when the words are placed without doubt ----


def _word_text(word: Word) -> str:
    return _clean_word(word.text)


def _rect_of(box: Box) -> Rect:
    return Rect(x0=box[0], y0=box[1], x1=box[2], y1=box[3])


def _line_rects(words: list[Word]) -> list[Rect]:
    """One rectangle per line of the given words."""
    lines: dict[int, list[Word]] = {}
    for word in words:
        lines.setdefault(round(word.box[1] * 200), []).append(word)
    return [
        Rect(
            x0=min(w.box[0] for w in line),
            y0=min(w.box[1] for w in line),
            x1=max(w.box[2] for w in line),
            y1=max(w.box[3] for w in line),
        )
        for line in lines.values()
    ]


def ground_in_box(text: str, box: Box, vision: list[Word]) -> PdfLocator | None:
    """A DocTags box counts when the Vision words inside it match the element's text."""
    inside = [w for w in vision if _center_inside(w.box, box)]
    if not inside:
        return None
    expected = _clean_tokens(text)
    found = [
        _word_text(w)
        for w in sorted(inside, key=lambda w: (round(w.box[1], 2), w.box[0]))
    ]
    if expected != found:
        return None
    return PdfLocator(page=1, rects=_line_rects(inside), precision="words")


def _clean_tokens(text: str) -> list[str]:
    return [_clean_word(word) for word in normalize(text).split()]


def ground_without_box(text: str, vision: list[Word]) -> PdfLocator | None:
    """Text with no box (a page Claude read) is placed only by a run of at least 5
    words that occurs once on the page."""
    tokens = _clean_tokens(text)
    page = [
        _word_text(w)
        for w in sorted(vision, key=lambda w: (round(w.box[1], 2), w.box[0]))
    ]
    size = len(tokens)
    if size < GROUNDING_RUN_WORDS:
        return None
    hits = [i for i in range(len(page) - size + 1) if page[i : i + size] == tokens]
    if len(hits) != 1:
        return None
    start = hits[0]
    ordered = sorted(vision, key=lambda w: (round(w.box[1], 2), w.box[0]))
    return PdfLocator(
        page=1, rects=_line_rects(ordered[start : start + size]), precision="words"
    )


def place(element: Element, page: int, vision: list[Word]) -> None:
    """Sets an element's locator from grounding: words, its box, or the whole page."""
    locator = element.locator
    box = None
    if isinstance(locator, PdfLocator) and locator.rects:
        rect = locator.rects[0]
        box = (rect.x0, rect.y0, rect.x1, rect.y1)
    if element.kind in ("picture", "table") and box is not None:
        element.locator = PdfLocator(
            page=page, rects=[_rect_of(box)], precision="element"
        )
        return
    placed = (
        ground_in_box(element.text, box, vision)
        if box
        else ground_without_box(element.text, vision)
    )
    if placed is not None:
        placed.page = page
        element.locator = placed
    elif box is not None:
        element.locator = PdfLocator(
            page=page, rects=[_rect_of(box)], precision="element"
        )
    else:
        element.locator = PdfLocator(
            page=page, rects=[locators.WHOLE_PAGE], precision="page"
        )


# ---- The second reading ----


def _render(pdf: bytes, page: int) -> tuple[bytes, Any]:
    document = pypdfium2.PdfDocument(pdf)
    picture = document[page - 1].render(scale=2).to_pil()
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue(), picture


def text_cells(pdf: bytes, page: int) -> list[Word]:
    """The text layer's cells, in the frame the page displays in."""
    document = pypdfium2.PdfDocument(pdf)
    current = document[page - 1]
    width, height = current.get_size()
    text_page = current.get_textpage()
    cells = []
    for index in range(text_page.count_rects()):
        left, bottom, right, top = text_page.get_rect(index)
        text = text_page.get_text_bounded(left, bottom, right, top)
        cells.append(
            Word(
                text,
                1.0,
                (left / width, 1 - top / height, right / width, 1 - bottom / height),
            )
        )
    return cells


def _signals(
    pdf: bytes, page: int, facts: dict[str, Any], vision: list[Word]
) -> PageSignals:
    cells = text_cells(pdf, page)
    return PageSignals(
        mean_grade=facts.get("mean_grade"),
        low_grade=facts.get("low_grade"),
        broken_text_layer=facts.get("broken_text_layer", False),
        has_native_text=any(c.text.strip() for c in cells),
        text_cells=cells,
        vision_words=vision,
        picture_boxes=[tuple(b) for b in facts.get("picture_boxes", [])],
        tables_empty_share=facts.get("tables_empty_share", []),
        table_has_span=facts.get("table_has_span", False),
        table_continues=facts.get("table_continues", False),
        empty_or_garbled_after_ocr=facts.get("empty_or_garbled", False),
    )


def _stored_facts(revision_id: str) -> dict[int, dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(page_readings.c.page, page_readings.c.reasons).where(
                page_readings.c.revision_id == revision_id
            )
        ).all()
    return {row.page: row.reasons for row in rows}


def _as_elements(rows: list[dict[str, Any]]) -> list[Element]:
    return [
        Element(r["kind"], r["text"], locators.load(r["locator"]), r["reading_state"])
        for r in rows
    ]


def _read_again(pdf: bytes, page: int, vision: list[Word]) -> list[Element] | None:
    """A page read again from how it looks, each element grounded or an area, or
    None when the reader failed on it."""
    document = page_reader.read_page(pdf, page)
    if document is None:
        logger.warning("a page couldn't be read again: its first reading is kept")
        return None
    from_parse = _pdf_elements_of(document)
    fresh = [e for e in from_parse if e.kind != "picture"]
    for element in fresh:
        place(element, page, vision)
    return fresh


def _pdf_elements_of(document: Any) -> list[Element]:
    parser = _parser()
    return parser.pdf_elements(document)


def _parser() -> Any:
    # parse.py imports this module's normalize, so it comes in at call time.
    return importlib.import_module("worker.parse")


def _page_text(elements: list[Element], page: int) -> str:
    return normalize(
        " ".join(
            e.text
            for e in elements
            if chunking.is_on_page(e, page) and e.kind != "picture"
        )
    )


def _replace_page(
    elements: list[Element], page: int, fresh: list[Element]
) -> list[Element]:
    """The page's text and tables replaced in place; its pictures kept."""
    kept = [
        e
        for e in elements
        if not (chunking.is_on_page(e, page) and e.kind != "picture")
    ]
    position = next(
        (
            i
            for i, e in enumerate(kept)
            if isinstance(e.locator, PdfLocator) and e.locator.page >= page
        ),
        len(kept),
    )
    return [*kept[:position], *fresh, *kept[position:]]


def _crop(picture: Any, element: Element) -> bytes:
    assert isinstance(element.locator, PdfLocator)  # noqa: S101 - only PDF pictures reach here
    rect = element.locator.rects[0]
    width, height = picture.size
    cropped = picture.crop(
        (rect.x0 * width, rect.y0 * height, rect.x1 * width, rect.y1 * height)
    )
    buffer = io.BytesIO()
    cropped.save(buffer, "PNG")
    return buffer.getvalue()


def _table_markdown(table: list[list[Any]]) -> str:
    if not table:
        return ""
    lines = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in table]
    lines.insert(1, "| " + " | ".join("---" for _ in table[0]) + " |")
    return "\n".join(lines)


def _waiting_pictures(
    elements: list[Element], page: int | None = None
) -> list[Element]:
    return [
        e
        for e in elements
        if e.kind == "picture"
        and e.reading_state in (states.QUEUED, states.PENDING)
        and (page is None or chunking.is_on_page(e, page))
    ]


def _read_pictures(
    job_id: str, elements: list[Element], crops: dict[int, bytes], tag: str
) -> None:
    """Charts and figures the local model can read now. A picture with no reader
    stays pending, and is read once there is one. Cancel is checked between pictures."""
    waiting = _waiting_pictures(elements)
    if not waiting:
        return
    if not models.tag_listed(tag):
        for element in waiting:
            element.reading_state = states.PENDING
        return
    for element in waiting:
        if not jobs.advance_stage(job_id, "pictures"):
            raise HardPagesCancelled
        if id(element) in crops:
            _read_one_picture(element, crops[id(element)], tag)


def _read_one_picture(element: Element, image: bytes, tag: str) -> None:
    try:
        reading = models.read_picture(image, tag)
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("a picture couldn't be read")
        element.reading_state = states.FAILED
        return
    if reading.get("kind") == "chart":
        text = _table_markdown(reading.get("table", []))
    else:
        text = reading.get("description", "")
    element.text = f"{element.text}\n{text}".strip()
    element.reading_state = states.READ


def stitch_tables(elements: list[Element], continued: set[int]) -> None:
    """A table continuing onto the next page takes the first part's header; each part
    keeps its own page's locator."""
    tables = [
        e for e in elements if e.kind == "table" and isinstance(e.locator, PdfLocator)
    ]
    for first, second in zip(tables, tables[1:], strict=False):
        pages = (first.locator.page, second.locator.page)  # type: ignore[union-attr]
        if pages[1] != pages[0] + 1 or pages[0] not in continued:
            continue
        head = first.text.splitlines()[:2]
        body = second.text.splitlines()
        if (
            len(head) == TABLE_HEAD_LINES
            and body[:1] != head[:1]
            and _columns(head[0]) == _columns(body[0])
        ):
            second.text = "\n".join([*head, *body])


def _columns(line: str) -> int:
    return line.count("|")


PAGE_UNREAD = "This page couldn't be read again. Its first reading is kept."
NEW_VERSION = "This document changed before its page was sent, so it wasn't sent."
NO_COST_KEPT = (
    "This page was asked for before SecureFolio kept the cost it showed, so it wasn't "
    "sent. Send it again to see its cost."
)


class HardPagesCancelled(Exception):
    """The owner cancelled, or a file's change made this reading stale."""


class AskedPageUnread(Exception):
    """The page the owner asked to read again couldn't be read; the rest was."""


class Stale(Exception):
    """The file changed since the revision this reading started from."""


def _chunk_rows(
    revision_id: str, ids: list[str], elements: list[Element]
) -> list[dict]:
    return [
        {
            "id": f"{revision_id[:16]}-{ordinal}",
            "ordinal": ordinal,
            "element_ids": [ids[o] for o in chunk.element_ordinals],
            "text": chunk.text,
            "token_count": estimate_tokens(chunk.text),
        }
        for ordinal, chunk in enumerate(chunking.from_elements(elements))
    ]


def queue_pending_when_ready() -> int:
    """Documents holding a picture that waited for the local model, once it's here."""
    if not models.local_ready(settings.LOCAL_FIGURES):
        return 0
    with database.read() as connection:
        ids = list(
            connection.execute(
                sa.select(database.documents.c.id)
                .distinct()
                .join(
                    database.elements,
                    database.elements.c.revision_id
                    == database.documents.c.active_revision_id,
                )
                .where(
                    database.documents.c.status == states.INDEXED,
                    database.elements.c.kind == "picture",
                    database.elements.c.reading_state == states.PENDING,
                )
                .limit(PENDING_PICTURES_PASS)
            ).scalars()
        )
    for document_id in ids:
        jobs.enqueue(
            jobs.HARD_PAGES,
            jobs.PRIORITY_HARD_PAGES,
            document_id=document_id,
            key=f"pictures:{document_id}",
            payload={"pictures_only": True},  # the pages read before stay as read
        )
    return len(ids)


def _queue_again(document_id: str) -> None:
    jobs.enqueue(
        jobs.HARD_PAGES,
        jobs.PRIORITY_HARD_PAGES,
        document_id=document_id,
        key=f"hard:{document_id}:again",
    )


def _publish_changes(
    job: dict[str, Any], document: dict[str, Any], elements: list[Element], base: str
) -> None:
    embedder.unload()
    page_reader.unload()
    if documents.get(document["id"])["active_revision_id"] != base:
        _queue_again(document["id"])  # another revision arrived: this reading is stale
        return
    revision_id = revisions.stage(document["id"], job["id"], document["content_sha256"])
    rows, ids = element_rows(elements)
    chunks = _chunk_rows(revision_id, ids, elements)
    revisions.write_content(revision_id, document["id"], rows, chunks)
    embedding.embed_revision(revision_id)
    if not jobs.advance_stage(job["id"], "publish"):
        raise HardPagesCancelled
    try:
        publishing.publish(revision_id, job["id"], False, None)
    except refusals.Refused as refused:
        if jobs.cancel_requested(job["id"]):
            raise HardPagesCancelled from refused
        raise Stale from refused


def _verified(document: dict[str, Any], base: str) -> bytes:
    """The bytes the active revision was read from, or Stale: reading a newer file
    against the old revision's elements could mis-place everything."""
    data = documents.read_content(document)
    if hashlib.sha256(data).hexdigest() != revisions.content_hash(base):
        raise Stale
    return data


def _pdf_bytes(document: dict[str, Any], base: str) -> bytes | None:
    data = _verified(document, base)
    if not unlock.needs_password("PDF", data):
        return data
    password = unlock.held_for(document["id"])
    return unlock.decrypt("PDF", data, password) if password else None


def _image_as_pdf(document: dict[str, Any], base: str) -> bytes:
    png = _parser()._to_png(_verified(document, base), document["file_kind"])
    buffer = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(buffer, "PDF")
    return buffer.getvalue()


@dataclass
class _Reading:
    elements: list[Element]
    changed: bool = False
    continued: set[int] = field(default_factory=set)
    asked_page_unread: bool = False


def _read_page_if_hard(
    pdf: bytes, page: int, facts: dict[str, Any], forced: bool, reading: _Reading
) -> Any:
    image, picture = _render(pdf, page)
    vision = ocr.read_words(image)
    signals = _signals(pdf, page, facts, vision)
    signals.owner_asked = forced
    reasons = why_hard(signals)
    if signals.table_continues:
        reading.continued.add(page)
    if not reasons or not page_reader.available():
        return picture
    embedder.unload()
    _parser().unload_models()
    fresh = _read_again(pdf, page, vision)
    if fresh is None:
        # The page keeps its first reading and the job reads on; a page the owner
        # asked about ends the job as failed, so the tray says so and offers Retry.
        reading.asked_page_unread |= forced
        return picture
    if fresh and _page_text(fresh, page) != _page_text(reading.elements, page):
        candidate = _replace_page(reading.elements, page, fresh)
        if _loses_nothing_shown(candidate, reading.elements, page, vision):
            reading.elements = candidate
            reading.changed = True
    return picture


def _words_held(
    elements: list[Element], page: int, seen: Counter[str], tables_only: bool
) -> int:
    """How many of the words Apple Vision sees on the page these elements hold."""
    held = Counter(
        _clean_tokens(
            " ".join(
                e.text
                for e in elements
                if chunking.is_on_page(e, page)
                and (e.kind == "table" or not tables_only)
            )
        )
    )
    return sum((seen & held).values())


def _tables_on(elements: list[Element], page: int) -> int:
    return sum(e.kind == "table" and chunking.is_on_page(e, page) for e in elements)


def _loses_nothing_shown(
    candidate: list[Element], current: list[Element], page: int, vision: list[Word]
) -> bool:
    """A second reading replaces a page unless it has fewer tables, or holds fewer
    of the words the page shows, in all or in its tables: a small reader can drop a
    whole table that the first reading had right, or flatten one into text, and
    Vision can miss a dense table's words. A reading that only puts the same words
    in a better order or row is kept. With nothing seen to judge by, a reading that
    keeps its tables and changed something is kept, as before."""
    tables_before = _tables_on(current, page)
    if _tables_on(candidate, page) < tables_before:
        logger.info(
            "a page's second reading had fewer than its %d tables: the first is kept",
            tables_before,
        )
        return False
    seen = Counter(_word_text(w) for w in vision if _word_text(w))
    if not seen:
        return True
    for tables_only in (False, True):
        before = _words_held(current, page, seen, tables_only)
        after = _words_held(candidate, page, seen, tables_only)
        if after < before:
            logger.info(
                "a page's second reading held %d of the %d words it shows%s, the "
                "first %d: the first is kept",
                after,
                sum(seen.values()),
                " in tables" if tables_only else "",
                before,
            )
            return False
    return True


def run(job: dict[str, Any]) -> None:
    """Terminal in every case: cancelled, stale, failed or done, with nothing staged
    left behind and any password dropped."""
    document = documents.get(job["document_id"])
    try:
        base = document["active_revision_id"]
        if base is not None and document["status"] == states.INDEXED:
            _run(job, document, base)
        jobs.succeed(job["id"])
    except HardPagesCancelled:
        revisions.discard_for_job(job["id"])
        jobs.finish_cancelled(job["id"])
    except Stale:
        revisions.discard_for_job(job["id"])
        jobs.skip(job["id"], "The file changed; a rescan will index it again.", "stale")
    except AskedPageUnread:
        jobs.fail(job["id"], PAGE_UNREAD, "hard_pages")
    except models.CloudRefused as refused:
        revisions.discard_for_job(job["id"])
        jobs.skip(job["id"], str(refused), "refused")
    except Exception:
        logger.exception("hard pages failed")
        revisions.discard_for_job(job["id"])
        jobs.fail(
            job["id"], "This document's hard pages couldn't be read.", "hard_pages"
        )
    finally:
        unlock.forget(document["id"])
        page_reader.unload()


def _read_page_with_claude(
    job: dict[str, Any], document: dict[str, Any], pdf: bytes, base: str, page: int
) -> None:
    """The one page the owner chose goes to Claude, and its text replaces the page.
    The owner saw that revision's page: if another has replaced it, nothing is sent."""
    if job["payload"].get("revision_id") != base:
        raise models.CloudRefused(NEW_VERSION)
    model, price = job["payload"].get("model"), job["payload"].get("price")
    if not model or not price:
        # Queued before the cost shown went with the job: Settings may have changed.
        raise models.CloudRefused(NO_COST_KEPT)
    if not jobs.advance_stage(job["id"], f"page {page}"):
        raise HardPagesCancelled
    image = _render(pdf, page)[0]
    text = models.read_page_in_cloud(
        document["id"],
        page,
        job["payload"]["claude_page"],
        image,
        (model, (price[0], price[1])),
    )
    vision = ocr.read_words(image)
    fresh = [
        Element("text", part.strip(), PdfLocator(page=page, rects=[], precision="page"))
        for part in text.split("\n\n")
        if part.strip()
    ]
    for element in fresh:
        place(element, page, vision)
    rows = revisions.active_elements(document["id"])
    elements = _replace_page(_as_elements(rows), page, fresh)
    _publish_changes(job, document, elements, base)


def _run(job: dict[str, Any], document: dict[str, Any], base: str) -> None:
    # The figures model is the job's from its start: one chosen as it runs is the
    # next job's.
    figures_tag = models.local_tag(settings.LOCAL_FIGURES)
    # A requeue of waiting pictures reads them only: no page read before is redone.
    pictures_only = bool(job["payload"].get("pictures_only"))
    rows = revisions.active_elements(document["id"])
    reading = _Reading(_as_elements(rows))
    is_pdf = document["file_kind"] == "PDF"
    pdf = _pdf_bytes(document, base) if is_pdf else _image_as_pdf(document, base)
    if pdf is None:
        logger.info("hard pages wait for a password")
        return
    claude_page = job["payload"].get("claude_page")
    if claude_page is not None:
        _read_page_with_claude(job, document, pdf, base, claude_page)
        return
    forced_page = job["payload"].get("reread_page")
    facts = _stored_facts(base)
    crops: dict[int, bytes] = {}
    pages = len(pypdfium2.PdfDocument(pdf))
    for page in range(1, pages + 1):
        if not jobs.advance_stage(job["id"], f"page {page}"):
            raise HardPagesCancelled
        forced = page == forced_page
        if not pictures_only and (forced or (is_pdf and forced_page is None)):
            picture = _read_page_if_hard(
                pdf, page, facts.get(page, {}), forced, reading
            )
        elif _waiting_pictures(reading.elements, page):
            picture = _render(pdf, page)[1]
        else:
            continue
        # Only the crops are kept: a long PDF's page images would fill memory.
        for element in _waiting_pictures(reading.elements, page):
            crops[id(element)] = _crop(picture, element)
    before = [(e.text, e.reading_state) for e in reading.elements]
    stitch_tables(reading.elements, reading.continued)
    _read_pictures(job["id"], reading.elements, crops, figures_tag)
    if reading.changed or before != [
        (e.text, e.reading_state) for e in reading.elements
    ]:
        _publish_changes(job, document, reading.elements, base)
    if reading.asked_page_unread:
        raise AskedPageUnread
