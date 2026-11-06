"""Notes, which keep their citations with revision ids, and v1's saved queries."""

import time
import uuid
from typing import Any

import sqlalchemy as sa

from backend import database, elements, refusals, states
from backend.database import notes, saved_queries

WRITTEN_BY_OWNER = "Written by you"
TITLE_CHARACTERS = 40


def _level_in(connection: Any, document_id: str | None) -> str:
    """Read in the writing transaction, so a change to Private can't slip in between."""
    if document_id is None:
        return states.STANDARD
    level = connection.execute(
        sa.select(database.documents.c.sensitivity).where(
            database.documents.c.id == document_id
        )
    ).scalar()
    if level is None:
        raise refusals.not_found("There's no such document.")
    return level


NO_TEXT = "A note needs some text."
NO_HEADING = "A note needs a heading."


def create(
    body: str, document_id: str | None, heading: str | None = None
) -> dict[str, Any]:
    """A note on a document carries that document's sensitivity. Its heading is the
    one given, or the text's first 40 characters."""
    text = body.strip()
    if not text:
        raise refusals.invalid(NO_TEXT)
    row = {
        "id": uuid.uuid4().hex,
        "title": (heading or "").strip() or text[:TITLE_CHARACTERS],
        "body": text,
        "citations": [],
        "document_id": document_id,
        "source_label": WRITTEN_BY_OWNER,
        "created_at": time.time(),
    }
    with database.write() as connection:
        row["sensitivity"] = _level_in(connection, document_id)
        connection.execute(notes.insert().values(**row))
    return row


CARD_ELEMENT = "card:"  # a list's citations point at a card, as its rows do


def _citations_of(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """A list answer's rows cite their cards, which have no element to point at."""
    cited = [
        {key: c[key] for key in ("document_id", "revision_id", "element_id")}
        for c in answer.get("citations", [])
        if not str(c["element_id"]).startswith(CARD_ELEMENT)
    ]
    rows = (answer.get("table") or {}).get("rows", [])
    return cited + [
        {
            "document_id": row["document_id"],
            "revision_id": row["revision_id"],
            "element_id": None,
        }
        for row in rows
    ]


def from_answer(message_id: str) -> dict[str, Any]:
    """A saved answer carries its citations with their revision ids, and the
    sensitivity of the chat it came from."""
    with database.write() as connection:
        message = (
            connection.execute(
                sa.select(database.messages).where(database.messages.c.id == message_id)
            )
            .mappings()
            .first()
        )
        if message is None or not message["answer"]:
            raise refusals.not_found("There's no such answer.")
        saved = _citations_of(message["answer"])
        # A count's message holds no numbers, for its follow-ups. The owner keeps them.
        body = message["content"]
        if message["answer"].get("counted"):
            body = "".join(part["text"] for part in message["answer"]["parts"])
        row = {
            "id": uuid.uuid4().hex,
            "title": body[:TITLE_CHARACTERS],
            "body": body,
            "citations": saved,
            "document_id": None,
            "source_label": "Saved from a chat",
            "sensitivity": message["sensitivity"],
            "thread_id": message["thread_id"],
            "created_at": time.time(),
        }
        connection.execute(notes.insert().values(**row))
    return row


def _resolved(row: Any) -> dict[str, Any]:
    opened = []
    for citation in row["citations"]:
        opened.append(
            {
                **citation,
                "opens": (
                    elements.resolve(
                        citation["document_id"],
                        citation["revision_id"],
                        citation["element_id"],
                    )
                    if citation["element_id"]
                    else {"state": "card", "element": None}
                ),
            }
        )
    return {**row, "citations": opened}


def all_notes(document_id: str | None = None) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(notes).order_by(notes.c.created_at.desc())
        ).mappings()
        found = [dict(r) for r in rows]
    if document_id is not None:
        found = [
            r
            for r in found
            if r["document_id"] == document_id
            or any(c["document_id"] == document_id for c in r["citations"])
        ]
    return [_resolved(r) for r in found]


def edit(note_id: str, body: str, heading: str) -> None:
    """The heading and the text change together. Sensitivity stays as it was: a
    Private document's note, or one saved from a Private chat, stays Private."""
    text, title = body.strip(), heading.strip()
    if not title:
        raise refusals.invalid(NO_HEADING)
    if not text:
        raise refusals.invalid(NO_TEXT)
    with database.write() as connection:
        moved = connection.execute(
            notes.update().where(notes.c.id == note_id).values(title=title, body=text)
        )
    if moved.rowcount != 1:
        raise refusals.not_found("There's no such note.")


def delete(note_id: str) -> None:
    with database.write() as connection:
        connection.execute(notes.delete().where(notes.c.id == note_id))


def queries() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(saved_queries).order_by(saved_queries.c.created_at.desc())
        ).mappings()
        return [dict(r) for r in rows]
