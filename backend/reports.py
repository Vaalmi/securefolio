"""A wrong-answer report: what "This was wrong" saves, as a question in the owner's
own set, `<vault>/evals/my_questions.json`, beside the evaluation's other records of
their library. It stays on this Mac: nothing here sends anything. A report holds the
question and those before it in the chat, the right document and the place in it,
or that it isn't in the library, the fact and a note, and what the answer rested
on, so each run of the set can tell again whether it's Private."""

import datetime
import json
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend import database, documents, elements, refusals, states, threads, vault

SET_NAME = "my_questions.json"
NOT_IN_LIBRARY = "unanswerable"  # the evaluation's category for a question to refuse
CELL = re.compile(r"^([A-Z]{1,3})([1-9]\d*)$")  # a column and a row from 1
# The place a later answer must cite, by the kind of the document picked. A Word
# document or a web page has no numbered place: its passages are anchors, so the
# fact typed is what the later answer must hold.
PLACE_OF = {
    "PDF": "page",
    "PNG": "page",
    "JPEG": "page",
    "HEIC": "page",
    "WEBP": "page",
    "PPTX": "slide",
    "XLSX": "cells",
    "CSV": "cells",
    "EML": "message",
    "MD": "lines",
    "TXT": "lines",
}
LOCATOR_FIELD = {"slide": "slide", "message": "message", "lines": "end"}
# One save at a time: each reads the whole set, checks it, and writes it back.
_saving = threading.Lock()


def set_path() -> Path:
    return vault.root() / "evals" / SET_NAME


def _saved() -> list[dict[str, Any]]:
    path = set_path()
    return json.loads(path.read_text()) if path.exists() else []


def reported_in(thread_id: str) -> set[str]:
    """The answers in this chat that have a report already, for the app's button."""
    return {
        q["reported"]["message"]
        for q in _saved()
        if q.get("reported", {}).get("thread") == thread_id
    }


def _whole(value: Any) -> int:
    """A place's number as given: a whole number from 1, or 0, which no check takes.
    2.5 or "3" isn't read as another number."""
    whole = isinstance(value, int) and not isinstance(value, bool)
    return value if whole and value >= 1 else 0


def _column(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def cells_box(cells: str) -> tuple[int, int, int, int] | None:
    """A cell or a range, such as B4:F4, as its first and last column and row, or
    None when it isn't one."""
    corners = []
    for corner in cells.upper().replace("$", "").split(":", 1):  # B4:F4:G9 fails
        found = CELL.match(corner.strip())
        if found is None:
            return None
        corners.append((_column(found[1]), int(found[2])))
    (left, top), (right, bottom) = corners[0], corners[-1]
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def _sheet_extent(document_id: str, sheet: str) -> tuple[int, int]:
    """How many columns and rows a sheet of the active revision has, from where its
    elements are."""
    locator = database.elements.c.locator
    with database.read() as connection:
        ranges = connection.execute(
            sa.select(sa.func.json_extract(locator, "$.range"))
            .join(
                database.documents,
                database.documents.c.active_revision_id
                == database.elements.c.revision_id,
            )
            .where(
                database.documents.c.id == document_id,
                sa.func.json_extract(locator, "$.sheet") == sheet,
            )
        ).scalars()
        boxes = [box for found in ranges if (box := cells_box(str(found or "")))]
    return max((b[2] for b in boxes), default=0), max((b[3] for b in boxes), default=0)


def _largest(document_id: str, field: str) -> int:
    """The largest value an element of the active revision gives a locator field: how
    many slides, messages or lines it has."""
    found = sa.func.max(sa.func.json_extract(database.elements.c.locator, f"$.{field}"))
    with database.read() as connection:
        value = connection.execute(
            sa.select(found)
            .join(
                database.documents,
                database.documents.c.active_revision_id
                == database.elements.c.revision_id,
            )
            .where(database.documents.c.id == document_id)
        ).scalar()
    return int(value or 0)


def _page(document: dict[str, Any], place: dict[str, Any]) -> dict[str, Any]:
    page = _whole(place.get("page"))
    if not 1 <= page <= (document["pages"] or 1):
        raise refusals.invalid(f"{document['title']} has no page {place.get('page')}.")
    return {"kind": "page", "page": page}


def _cells(document: dict[str, Any], place: dict[str, Any]) -> dict[str, Any]:
    sheet = str(place.get("sheet") or "")
    cells = str(place.get("cells") or "").upper().replace(" ", "")
    if sheet not in {s["sheet"] for s in elements.sheets(document["id"])}:
        raise refusals.invalid(f"{document['title']} has no sheet {sheet!r}.")
    box = cells_box(cells)
    if box is None:
        raise refusals.invalid("Give the cells as a range, such as B4:F4.")
    columns, rows = _sheet_extent(document["id"], sheet)
    if box[2] > columns or box[3] > rows:
        raise refusals.invalid(f"{sheet} in {document['title']} has no cells {cells}.")
    return {"kind": "cells", "sheet": sheet, "cells": cells}


def _lines(document: dict[str, Any], place: dict[str, Any]) -> dict[str, Any]:
    start, end = _whole(place.get("start")), _whole(place.get("end"))
    if not 1 <= start <= end <= _largest(document["id"], "end"):
        said = f"{place.get('start')}–{place.get('end')}"
        raise refusals.invalid(f"{document['title']} has no lines {said}.")
    return {"kind": "lines", "start": start, "end": end}


def _numbered(kind: str):
    """A slide or a message, by its number."""

    def checked(document: dict[str, Any], place: dict[str, Any]) -> dict[str, Any]:
        number = _whole(place.get(kind))
        if not 1 <= number <= _largest(document["id"], LOCATOR_FIELD[kind]):
            said = place.get(kind)
            raise refusals.invalid(f"{document['title']} has no {kind} {said}.")
        return {"kind": kind, kind: number}

    return checked


CHECKS = {
    "page": _page,
    "cells": _cells,
    "lines": _lines,
    "slide": _numbered("slide"),
    "message": _numbered("message"),
}


def _checked_place(document: dict[str, Any], place: dict[str, Any] | None) -> Any:
    """The place, if it's one the document has; refused otherwise. None for a kind
    with no numbered place."""
    kind = PLACE_OF.get(document["file_kind"])
    if kind is None:
        return None
    if not place or place.get("kind") != kind:
        raise refusals.invalid(f"Say which {kind} of {document['title']} it's on.")
    return CHECKS[kind](document, place)


def _turn(thread_id: str, message_id: str) -> tuple[dict[str, Any], str, list[str]]:
    """The answer reported, its question, and the questions before it, in order."""
    transcript = threads.transcript(thread_id)
    saved = next((m for m in transcript if m["id"] == message_id), None)
    answer = (saved or {}).get("answer")
    if saved is None or not answer or answer.get("kind") == "passages":
        raise refusals.not_found("There's no such answer.")
    asked = [
        m
        for m in transcript
        if m["role"] == threads.ROLE_USER and m["ordinal"] < saved["ordinal"]
    ]
    if not asked:
        raise refusals.not_found("There's no such answer.")
    return saved, asked[-1]["content"], [m["content"] for m in asked[:-1]]


def _rested_on(answer: dict[str, Any]) -> dict[str, list[str]]:
    """What the answer was given or cited: what it saved, and what its citations,
    rows, uncounted rows and passages name. Both, never one for the other: an answer
    saved before reports saved nothing, and one saved before its uncounted rows were
    saved left them out."""
    named = [c["document_id"] for c in answer.get("citations") or []]
    table = answer.get("table") or {}
    named += [r["document_id"] for r in table.get("rows", [])]
    named += [r["document_id"] for r in table.get("pending", [])]
    named += [p["document_id"] for p in answer.get("also_searched") or []]
    named += [p["document_id"] for p in answer.get("closest") or []]
    return {
        "documents": list(dict.fromkeys([*(answer.get("rested_on") or []), *named])),
        "memories": list(answer.get("memory_ids") or []),
    }


def why_private(
    private_chat: bool, picked: str | None, rested_on: dict[str, list[str]]
) -> str | None:
    """Why a report is Private, from the settings as they are now, or None."""
    if private_chat:
        return "this chat is Private"
    with database.read() as connection:
        private = {
            row.id: row.title
            for row in connection.execute(
                sa.select(database.documents.c.id, database.documents.c.title).where(
                    database.documents.c.id.in_(
                        [d for d in [picked, *rested_on["documents"]] if d]
                    ),
                    database.documents.c.sensitivity == states.PRIVATE,
                )
            )
        }
        memories = connection.execute(
            sa.select(database.memories.c.text).where(
                database.memories.c.id.in_(rested_on["memories"]),
                database.memories.c.sensitivity == states.PRIVATE,
            )
        ).first()
    if picked in private:
        return f"{private[picked]} is in a Private folder"
    rested = next((private[d] for d in rested_on["documents"] if d in private), None)
    if rested:
        return f"the answer rested on {rested}, which is Private"
    if memories is not None:
        return f"the answer used a Private memory, “{memories.text}”"
    return None


def privacy(thread_id: str, message_id: str, picked: str | None) -> str | None:
    """For the form, before it's saved: why the report would be Private."""
    saved, _question, _earlier = _turn(thread_id, message_id)
    thread = threads.get(thread_id)
    return why_private(
        bool(thread["private_tainted"]), picked, _rested_on(saved["answer"])
    )


@dataclass(frozen=True)
class Report:
    """What the owner says in "This was wrong"."""

    document_id: str | None  # None: it isn't in the library
    place: dict[str, Any] | None
    fact: str | None
    note: str | None


def save(thread_id: str, message_id: str, said: Report) -> dict[str, Any]:
    """The report, written into the owner's own set. Refused: passages found, which
    aren't an answer; an answer reported already; a document not in the library; a
    place that isn't in the document, or missing; nothing to check a Word document
    or web page by."""
    with _saving:
        return _saved_report(thread_id, message_id, said)


def _saved_report(thread_id: str, message_id: str, said: Report) -> dict[str, Any]:
    saved, question, earlier = _turn(thread_id, message_id)
    known = _saved()
    if any(q.get("reported", {}).get("message") == message_id for q in known):
        raise refusals.conflict("This answer is reported already.")
    fact = (said.fact or "").strip()
    document_id = said.document_id
    picked = documents.get(document_id) if document_id else None
    checked = _checked_place(picked, said.place) if picked else None
    if picked and checked is None and not fact:
        raise refusals.invalid(
            f"{picked['title']} has no pages to point to: type the fact the answer "
            "should have held."
        )
    thread = threads.get(thread_id)
    rested = _rested_on(saved["answer"])
    why = why_private(bool(thread["private_tainted"]), document_id, rested)
    question_row = {
        "id": f"r-{uuid.uuid4().hex[:12]}",
        "category": NOT_IN_LIBRARY if picked is None else "reported",
        "question": question,
        "earlier": earlier,
        "expected_document": Path(picked["file_name"]).name if picked else None,
        "expected_document_id": document_id,
        "expected_place": checked,
        # A question to refuse owes no fact, so one typed with it is kept for you.
        "answer_facts": [fact] if fact and picked else [],
        "fact_noted": fact if fact and picked is None else None,
        "note": (said.note or "").strip(),
        "private": why is not None,
        "private_chat": bool(thread["private_tainted"]),
        "rested_on": rested,
        "reported": {
            "message": message_id,
            "thread": thread_id,
            "model": saved["answer"].get("model") or "",
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        },
    }
    path = set_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    written = path.with_suffix(f".{uuid.uuid4().hex[:8]}.writing")
    written.write_text(json.dumps([*known, question_row], indent=1))
    written.replace(path)
    return {"id": question_row["id"], "private": why is not None, "why": why}
