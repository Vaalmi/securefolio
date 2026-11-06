"""Cards: the basic one written at publish, a model's card merged without touching
what the owner edited, and the owner's own edits."""

import re
import time
from typing import Any

import numpy as np
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import (
    database,
    embedder,
    index,
    refusals,
    search_row,
    settings,
    states,
)
from backend.database import cards, documents

BASIC = "Basic (pending)"
FIELDS = {
    "title",
    "summary",
    "doc_type",
    "dates",
    "people",
    "organizations",
    "amounts",
    "tags",
}
TYPES = [
    "Contract",
    "Warranty",
    "Receipt",
    "Statement",
    "Report",
    "Letter",
    "Email",
    "Tax return",
    "Policy",
    "Manual",
    "Image",
    "Note",
    "Other",
]
DATE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December) \d{1,2}, \d{4})\b"
)


MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
LONG_DATE = re.compile(r"^([A-Za-z]+)\s+(?:(\d{1,2}),\s*)?(\d{4})$")
YEAR = re.compile(r"^\d{4}$")


def iso_date(value: str) -> str | None:
    """A card's date as YYYY-MM-DD, when it can be read as one. A year or a month alone
    becomes its first day, so a period's filter still finds it."""
    text = value.strip()
    if YEAR.match(text):
        return f"{text}-01-01"
    found = ISO_DATE.match(text)
    if found:
        return f"{found[1]}-{found[2]}-{found[3] or '01'}"
    found = LONG_DATE.match(text)
    if found and found[1].casefold() in MONTHS:
        month = MONTHS[found[1].casefold()]
        return f"{found[3]}-{month:02d}-{int(found[2] or 1):02d}"
    return None


def dates_found(text: str, limit: int = 5) -> list[dict[str, str]]:
    """The dates a document's text states, for a basic card."""
    seen: list[str] = []
    for found in DATE.findall(text):
        if found not in seen:
            seen.append(found)
    return [{"label": "Date found", "value": value} for value in seen[:limit]]


def write_basic_in(
    connection: Connection, document: dict[str, Any], revision_id: str, text: str
) -> None:
    """Publish's card, from the document's own details. A new revision marks an
    existing card pending, showing its last fields, and keeps every edit."""
    document_id = document["id"]
    sensitivity = document["sensitivity"]
    existing = connection.execute(
        sa.select(cards.c.document_id).where(cards.c.document_id == document_id)
    ).first()
    if existing:
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id)
            .values(
                state=states.PENDING, revision_id=revision_id, sensitivity=sensitivity
            )
        )
        return
    connection.execute(
        cards.insert().values(
            document_id=document_id,
            revision_id=revision_id,
            state=states.PENDING,
            title=document["title"],
            summary="",
            doc_type="Other",
            dates=dates_found(text),
            people=[],
            organizations=[],
            amounts=[],
            tags=[],
            edited_fields=[],
            written_by=BASIC,
            sensitivity=sensitivity,
        )
    )
    _sync_document(connection, document_id)


def cloud_consent_in(connection: Connection, document_id: str) -> bool:
    """Whether a cloud model may write this document's card now: it's Standard, the
    helper isn't the local model, and every folder holding it has opted in."""
    level = connection.execute(
        sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
    ).scalar()
    if (
        level != states.STANDARD
        or settings.get(settings.MODEL_HELPER) == settings.LOCAL
    ):
        return False
    switches = list(
        connection.execute(
            sa.select(database.sources.c.auto_organize)
            .join(
                database.document_locations,
                database.document_locations.c.source_id == database.sources.c.id,
            )
            .where(database.document_locations.c.document_id == document_id)
        ).scalars()
    )
    return bool(switches) and all(switches)


def write_card(
    document_id: str,
    revision_id: str,
    fields: dict[str, Any],
    written_by: str,
    cloud: bool = False,
) -> bool:
    """Merges a model's card, keeping edited fields. Refused for an old revision, and a
    cloud model's for a document whose consent was taken back while it wrote: checked
    in the same write, so nothing can change between the check and the card."""
    with database.write() as connection:
        if cloud and not cloud_consent_in(connection, document_id):
            return False
        row = (
            connection.execute(
                sa.select(cards).where(cards.c.document_id == document_id)
            )
            .mappings()
            .first()
        )
        if row is None or row["revision_id"] != revision_id:
            return False
        kept = set(row["edited_fields"])
        values = {k: v for k, v in fields.items() if k in FIELDS and k not in kept}
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id)
            .values(
                state=states.COMPLETE,
                state_reason=None,
                written_by=written_by,
                **values,
            )
        )
        _sync_document(connection, document_id)
    index.replay_ops()
    return True


def keep_pending(document_id: str, reason: str) -> None:
    """A pending card no model can write yet, and why, shown until one can."""
    with database.write() as connection:
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id, cards.c.state == states.PENDING)
            .values(state_reason=reason)
        )


def fail_card(document_id: str, reason: str) -> None:
    with database.write() as connection:
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id, cards.c.state == states.PENDING)
            .values(state=states.FAILED, state_reason=reason)
        )


def edit_card(document_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - FIELDS
    if unknown:
        raise refusals.invalid(f"A card has no field called {sorted(unknown)[0]}.")
    if "doc_type" in fields and fields["doc_type"] not in TYPES:
        raise refusals.invalid("That isn't one of the document types.")
    with database.write() as connection:
        row = (
            connection.execute(
                sa.select(cards).where(cards.c.document_id == document_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise refusals.not_found("This document has no card yet.")
        edited = sorted(set(row["edited_fields"]) | set(fields))
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id)
            .values(edited_fields=edited, **fields)
        )
        _sync_document(connection, document_id)
    index.replay_ops()
    return get(document_id)


def _sync_document(connection: Connection, document_id: str) -> None:
    """The list reads the document's row, so it's kept in step with its card, and the
    index filters on its type and date, so that's asked to follow them too."""
    row = connection.execute(
        sa.select(
            cards.c.title,
            cards.c.doc_type,
            cards.c.dates,
            documents.c.doc_type.label("was_type"),
            documents.c.doc_date.label("was_date"),
            documents.c.active_revision_id,
        )
        .join(documents, documents.c.id == cards.c.document_id)
        .where(cards.c.document_id == document_id)
    ).first()
    if row is None:
        return
    date = next(
        (found for d in row.dates if (found := iso_date(str(d.get("value", ""))))),
        None,
    )
    connection.execute(
        documents.update()
        .where(documents.c.id == document_id)
        .values(title=row.title, doc_type=row.doc_type, doc_date=date)
    )
    search_row.refresh_in(connection, document_id)
    changed = (row.doc_type, date) != (row.was_type, row.was_date)
    if changed and row.active_revision_id:
        connection.execute(
            index.index_ops.insert().values(
                kind=index.OP_PUBLISH,
                document_id=document_id,
                revision_id=row.active_revision_id,
                created_at=time.time(),
            )
        )


def get(document_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(cards).where(cards.c.document_id == document_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise refusals.not_found("This document has no card yet.")
    return {k: v for k, v in row.items() if k != "embedding"}


def in_scope(
    document_ids: list[str] | None, sensitivities: list[str]
) -> list[dict[str, Any]]:
    """Every card in a scope, for a list or total worked out by code."""
    conditions = [
        documents.c.status == states.INDEXED,
        documents.c.sensitivity.in_(sensitivities),
    ]
    if document_ids is not None:
        conditions.append(documents.c.id.in_(document_ids))
    with database.read() as connection:
        rows = connection.execute(
            sa.select(
                cards,
                documents.c.doc_date,
                documents.c.active_revision_id,
                documents.c.title.label("document_title"),
            )
            .join(documents, documents.c.id == cards.c.document_id)
            .where(*conditions)
        ).mappings()
        return [{k: v for k, v in row.items() if k != "embedding"} for row in rows]


def needing_a_model(limit: int) -> list[dict[str, Any]]:
    """Pending cards the local model could write now."""
    with database.read() as connection:
        rows = connection.execute(
            sa.select(cards.c.document_id)
            .where(cards.c.state == states.PENDING)
            .limit(limit)
        ).scalars()
        return list(rows)


def set_embedding(document_id: str, blob: bytes) -> None:
    with database.write() as connection:
        connection.execute(
            cards.update()
            .where(cards.c.document_id == document_id)
            .values(embedding=blob)
        )


def related(
    document_id: str, sensitivities: list[str], limit: int = 5
) -> list[dict[str, Any]]:
    """Documents whose cards sit closest to this one's. Cosine in NumPy."""
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(
                    cards.c.document_id,
                    cards.c.embedding,
                    documents.c.title,
                    documents.c.file_kind,
                    documents.c.doc_type,
                    documents.c.doc_date,
                    documents.c.sensitivity,
                )
                .join(documents, documents.c.id == cards.c.document_id)
                .where(
                    cards.c.embedding.is_not(None),
                    documents.c.status == states.INDEXED,
                    documents.c.sensitivity.in_(sensitivities),
                )
            )
            .mappings()
            .all()
        )
        own = connection.execute(
            sa.select(cards.c.embedding).where(cards.c.document_id == document_id)
        ).scalar()
    if own is None:
        return []
    target = embedder.from_blob(own)
    scored = [
        (float(np.dot(target, embedder.from_blob(row["embedding"]))), row)
        for row in rows
        if row["document_id"] != document_id
    ]
    scored.sort(key=lambda pair: -pair[0])
    return [
        {k: v for k, v in row.items() if k != "embedding"} for _, row in scored[:limit]
    ]
