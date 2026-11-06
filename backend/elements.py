"""Reads elements by revision, and resolves a stored citation to where it now opens."""

from typing import Any

import sqlalchemy as sa

from backend import database, locators
from backend.database import chunks, documents, elements, revisions

MOST_ELEMENTS = 2000


def _shown(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ordinal": row["ordinal"],
        "kind": row["kind"],
        "text": row["text"],
        "locator": row["locator"],
        "label": locators.label(locators.load(row["locator"])),
        "reading_state": row["reading_state"],
        "reading_reason": row["reading_reason"],
    }


def _active_revision(document_id: str) -> str | None:
    with database.read() as connection:
        return connection.execute(
            sa.select(documents.c.active_revision_id).where(
                documents.c.id == document_id
            )
        ).scalar()


def of_document(document_id: str, offset: int, limit: int) -> dict[str, Any]:
    """A window of the active revision's stored text, for the viewers built on it."""
    revision_id = _active_revision(document_id)
    if revision_id is None:
        return {"elements": [], "total": 0}
    with database.read() as connection:
        total = connection.execute(
            sa.select(sa.func.count())
            .select_from(elements)
            .where(elements.c.revision_id == revision_id)
        ).scalar_one()
        rows = connection.execute(
            sa.select(elements)
            .where(elements.c.revision_id == revision_id)
            .order_by(elements.c.ordinal)
            .offset(offset)
            .limit(min(limit, MOST_ELEMENTS))
        ).mappings()
        return {"elements": [_shown(row) for row in rows], "total": total}


def of_page(document_id: str, page: int) -> list[dict[str, Any]]:
    """One page's or slide's elements, in order."""
    revision_id = _active_revision(document_id)
    if revision_id is None:
        return []
    place = sa.func.coalesce(
        sa.func.json_extract(elements.c.locator, "$.page"),
        sa.func.json_extract(elements.c.locator, "$.slide"),
    )
    with database.read() as connection:
        rows = connection.execute(
            sa.select(elements)
            .where(elements.c.revision_id == revision_id, place == page)
            .order_by(elements.c.ordinal)
        ).mappings()
        return [_shown(row) for row in rows]


def cited(document_id: str, element_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(elements)
                .join(revisions, revisions.c.id == elements.c.revision_id)
                .where(
                    elements.c.id == element_id, revisions.c.document_id == document_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"element": None, "current": False}
        active = _active_revision(document_id)
        if row["revision_id"] == active:
            return {"element": _shown(row), "current": True}
        same = _same_place(connection, active, row)
    if same is not None:
        return {"element": _shown(same), "current": True}
    return {"element": _shown(row), "current": False}


def sheets(document_id: str) -> list[dict[str, Any]]:
    """Each sheet of a spreadsheet, and where its rows start, as the stored text is
    read a window at a time."""
    revision_id = _active_revision(document_id)
    if revision_id is None:
        return []
    name = sa.func.json_extract(elements.c.locator, "$.sheet")
    with database.read() as connection:
        rows = connection.execute(
            sa.select(name.label("sheet"), sa.func.min(elements.c.ordinal))
            .where(elements.c.revision_id == revision_id, name.is_not(None))
            .group_by(name)
            .order_by(sa.func.min(elements.c.ordinal))
        ).all()
    return [{"sheet": row[0], "first_ordinal": row[1]} for row in rows]


def get(element_id: str) -> dict[str, Any] | None:
    with database.read() as connection:
        row = (
            connection.execute(sa.select(elements).where(elements.c.id == element_id))
            .mappings()
            .first()
        )
    return _shown(row) if row else None


def _same_place(connection: Any, active: str | None, row: Any) -> Any:
    """The active revision's element with this text, if it can only be that one: text
    repeated on other pages must not move a citation to the wrong occurrence."""
    candidates = (
        connection.execute(
            sa.select(elements).where(
                elements.c.revision_id == active,
                elements.c.text_hash == row["text_hash"],
            )
        )
        .mappings()
        .all()
    )
    if len(candidates) == 1:
        return candidates[0]
    placed = [c for c in candidates if c["locator"] == row["locator"]]
    return placed[0] if len(placed) == 1 else None


def resolve(document_id: str, revision_id: str, element_id: str) -> dict[str, Any]:
    """Where a stored citation opens now. On the active revision, its own element. If
    the document was indexed again, the element with the same text. Otherwise its
    stored text, with no place to highlight, so nothing points at the wrong words."""
    with database.read() as connection:
        active = connection.execute(
            sa.select(documents.c.active_revision_id).where(
                documents.c.id == document_id
            )
        ).scalar()
        row = (
            connection.execute(
                sa.select(elements).where(
                    elements.c.id == element_id, elements.c.revision_id == revision_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"state": "gone", "element": None}
        if revision_id == active:
            return {"state": "current", "element": _shown(row)}
        same = _same_place(connection, active, row)
    if same is not None:
        return {"state": "moved", "element": _shown(same)}
    return {"state": "stored", "element": _shown(row)}


def first_of_chunk(document_id: str, chunk_id: str) -> str | None:
    with database.read() as connection:
        ids = connection.execute(
            sa.select(chunks.c.element_ids).where(
                chunks.c.id == chunk_id, chunks.c.document_id == document_id
            )
        ).scalar()
    return ids[0] if ids else None
