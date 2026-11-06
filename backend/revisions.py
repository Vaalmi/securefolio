"""A revision's life: staged, then published in one transaction that switches the
active pointer and retires the old one. Only an active revision is searchable."""

import time
import uuid
from typing import Any

import sqlalchemy as sa

from backend import cards, database, refusals, states
from backend.database import (
    chunks,
    documents,
    elements,
    index_ops,
    jobs,
    messages,
    notes,
    page_readings,
    revisions,
    runs,
)
from backend.index import OP_PUBLISH

PARSER_VERSION = "1"


def stage(document_id: str, job_id: str, content_sha256: str) -> str:
    """A staging revision for this job, reusing one a crash left behind."""
    with database.write() as connection:
        found = connection.execute(
            sa.select(revisions.c.id).where(
                revisions.c.job_id == job_id, revisions.c.state == states.STAGING
            )
        ).scalar()
        if found:
            return found
        revision_id = uuid.uuid4().hex
        connection.execute(
            revisions.insert().values(
                id=revision_id,
                document_id=document_id,
                job_id=job_id,
                content_sha256=content_sha256,
                parser_version=PARSER_VERSION,
                state=states.STAGING,
                created_at=time.time(),
            )
        )
    return revision_id


def write_content(
    revision_id: str,
    document_id: str,
    new_elements: list[dict[str, Any]],
    new_chunks: list[dict[str, Any]],
) -> None:
    """A staged revision's elements and chunks, replacing any from an earlier try. The
    chunks take the document's sensitivity as it is now, inside this transaction, so
    a Private move while the file was being read can't be undone by them."""
    with database.write() as connection:
        level = connection.execute(
            sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
        ).scalar_one()
        connection.execute(chunks.delete().where(chunks.c.revision_id == revision_id))
        connection.execute(
            elements.delete().where(elements.c.revision_id == revision_id)
        )
        if new_elements:
            rows = [{"revision_id": revision_id, **e} for e in new_elements]
            connection.execute(elements.insert(), rows)
        if new_chunks:
            rows = [
                {
                    "revision_id": revision_id,
                    "document_id": document_id,
                    "sensitivity": level,
                    **c,
                }
                for c in new_chunks
            ]
            connection.execute(chunks.insert(), rows)


def staged_for_job(job_id: str) -> str | None:
    with database.read() as connection:
        return connection.execute(
            sa.select(revisions.c.id).where(
                revisions.c.job_id == job_id, revisions.c.state == states.STAGING
            )
        ).scalar()


def has_content(revision_id: str) -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(chunks.c.id)
                .where(chunks.c.revision_id == revision_id)
                .limit(1)
            ).first()
            is not None
        )


def unembedded(revision_id: str) -> list[tuple[str, str]]:
    with database.read() as connection:
        return [
            (row.id, row.text)
            for row in connection.execute(
                sa.select(chunks.c.id, chunks.c.text).where(
                    chunks.c.revision_id == revision_id, chunks.c.embedding.is_(None)
                )
            )
        ]


def store_embeddings(pairs: list[tuple[str, bytes]]) -> None:
    with database.write() as connection:
        for chunk_id, blob in pairs:
            connection.execute(
                chunks.update().where(chunks.c.id == chunk_id).values(embedding=blob)
            )


def publish(
    revision_id: str, job_id: str, make_indexed: bool, pages: int | None = None
) -> str:
    """One transaction: switches the active revision, retires the old one, writes
    the card, and records the index write to replay after a crash."""
    with database.write() as connection:
        row = (
            connection.execute(
                sa.select(revisions).where(revisions.c.id == revision_id)
            )
            .mappings()
            .first()
        )
        if row is None or row["state"] != states.STAGING:
            raise refusals.conflict("This revision isn't staged.")
        cancelled = connection.execute(
            sa.select(jobs.c.cancel_requested).where(jobs.c.id == job_id)
        ).scalar()
        if cancelled:
            raise refusals.conflict("This job was cancelled.")
        document_id = row["document_id"]
        doc = (
            connection.execute(
                sa.select(documents).where(documents.c.id == document_id)
            )
            .mappings()
            .one()
        )
        if doc["status"] == states.REMOVED:
            raise refusals.conflict("This document was removed from the library.")
        if doc["content_sha256"] != row["content_sha256"]:
            raise refusals.conflict("This file changed while it was being read.")
        if doc["active_revision_id"]:
            retire_in(connection, doc["active_revision_id"])
        connection.execute(
            revisions.update()
            .where(revisions.c.id == revision_id)
            .values(state=states.ACTIVE)
        )
        values: dict[str, Any] = {
            "active_revision_id": revision_id,
            "status_reason": None,
            "error_code": None,
        }
        if make_indexed:
            values["status"] = states.INDEXED
        if pages is not None:
            values["pages"] = pages
        connection.execute(
            documents.update().where(documents.c.id == document_id).values(**values)
        )
        text = " ".join(
            connection.execute(
                sa.select(chunks.c.text)
                .where(chunks.c.revision_id == revision_id)
                .order_by(chunks.c.ordinal)
                .limit(3)
            ).scalars()
        )
        cards.write_basic_in(connection, dict(doc), revision_id, text)
        connection.execute(
            index_ops.insert().values(
                kind=OP_PUBLISH,
                document_id=document_id,
                revision_id=revision_id,
                created_at=time.time(),
            )
        )
    return document_id


def _cited_revisions(connection: Any) -> set[str]:
    cited: set[str] = set()
    for value in connection.execute(sa.select(notes.c.citations)).scalars():
        cited |= {c.get("revision_id") for c in value}
    for answer in connection.execute(
        sa.select(messages.c.answer).where(messages.c.answer.is_not(None))
    ).scalars():
        cited |= {c.get("revision_id") for c in (answer or {}).get("citations", [])}
    return cited


def retire_in(connection: Any, revision_id: str) -> None:
    """Retires a revision: its vectors and chunks go, but elements a note or message
    cites stay, so old citations still open."""
    connection.execute(
        revisions.update()
        .where(revisions.c.id == revision_id)
        .values(state=states.RETIRED)
    )
    connection.execute(chunks.delete().where(chunks.c.revision_id == revision_id))
    # An answer being written may cite it: sweep_retired() looks again once none is.
    if revision_id not in _cited_revisions(connection) and not _an_answer_is_running(
        connection
    ):
        connection.execute(
            elements.delete().where(elements.c.revision_id == revision_id)
        )


def _an_answer_is_running(connection: Any) -> bool:
    return (
        connection.execute(
            sa.select(runs.c.id).where(runs.c.state.in_(("running", "paused"))).limit(1)
        ).first()
        is not None
    )


def sweep_retired() -> int:
    """Elements kept only because an answer was running, once none is and none cites."""
    with database.write() as connection:
        if _an_answer_is_running(connection):
            return 0
        retired = connection.execute(
            sa.select(revisions.c.id).where(revisions.c.state == states.RETIRED)
        ).scalars()
        gone = set(retired) - set(_cited_revisions(connection))
        if not gone:
            return 0
        moved = connection.execute(
            elements.delete().where(elements.c.revision_id.in_(gone))
        )
    return moved.rowcount


def discard(revision_id: str) -> None:
    """Removes a staged revision and everything it wrote."""
    with database.write() as connection:
        state = connection.execute(
            sa.select(revisions.c.state).where(revisions.c.id == revision_id)
        ).scalar()
        if state != states.STAGING:
            return
        connection.execute(chunks.delete().where(chunks.c.revision_id == revision_id))
        connection.execute(
            elements.delete().where(elements.c.revision_id == revision_id)
        )
        connection.execute(
            page_readings.delete().where(page_readings.c.revision_id == revision_id)
        )
        connection.execute(revisions.delete().where(revisions.c.id == revision_id))


def discard_for_job(job_id: str) -> None:
    with database.read() as connection:
        found = (
            connection.execute(
                sa.select(revisions.c.id).where(
                    revisions.c.job_id == job_id, revisions.c.state == states.STAGING
                )
            )
            .scalars()
            .all()
        )
    for revision_id in found:
        discard(revision_id)


def content_hash(revision_id: str) -> str | None:
    with database.read() as connection:
        return connection.execute(
            sa.select(revisions.c.content_sha256).where(revisions.c.id == revision_id)
        ).scalar()


def published_by_job(job_id: str) -> str | None:
    """The revision this job already published, if a crash came after that."""
    with database.read() as connection:
        return connection.execute(
            sa.select(revisions.c.id).where(
                revisions.c.job_id == job_id, revisions.c.state != states.STAGING
            )
        ).scalar()


def active_elements(document_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        active = connection.execute(
            sa.select(documents.c.active_revision_id).where(
                documents.c.id == document_id
            )
        ).scalar()
        if active is None:
            return []
        rows = (
            connection.execute(
                sa.select(elements)
                .where(elements.c.revision_id == active)
                .order_by(elements.c.ordinal)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]
