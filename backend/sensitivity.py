"""The one operation that moves a document's sensitivity, however it came about: a
folder's setting or a copy found in a Private folder. Everything derived from the
document moves with it in one transaction, and LanceDB follows."""

import time

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import index, states
from backend.database import (
    cards,
    chunks,
    document_locations,
    documents,
    memories,
    messages,
    notes,
    thread_documents,
    thread_evidence,
    threads,
)
from backend.index import OP_SENSITIVITY


def strictest(connection: Connection, document_id: str) -> str:
    """Private if any live location is; a document with none keeps Private out of
    reach only when it had it."""
    values = connection.execute(
        sa.select(document_locations.c.sensitivity).where(
            document_locations.c.document_id == document_id
        )
    ).scalars()
    return states.PRIVATE if states.PRIVATE in set(values) else states.STANDARD


def _make_derived_private(connection: Connection, document_id: str) -> None:
    for table in (chunks, cards):
        connection.execute(
            table.update()
            .where(table.c.document_id == document_id)
            .values(sensitivity=states.PRIVATE)
        )
    # A note's or answer's citations name their documents, in JSON.
    mentions = f"%{document_id}%"
    connection.execute(
        notes.update()
        .where(
            sa.or_(
                notes.c.document_id == document_id,
                sa.cast(notes.c.citations, sa.String).like(mentions),
            )
        )
        .values(sensitivity=states.PRIVATE)
    )
    _taint_threads(connection, document_id, mentions)


def _taint_threads(connection: Connection, document_id: str, mentions: str) -> None:
    """Every thread that scoped, used or cited it is Private for good, and so are its
    messages and the memories that came from it."""
    used = sa.select(thread_documents.c.thread_id).where(
        thread_documents.c.document_id == document_id
    )
    drew_on = sa.select(thread_evidence.c.thread_id).where(
        thread_evidence.c.document_id == document_id
    )
    cited = sa.select(messages.c.thread_id).where(
        sa.cast(messages.c.answer, sa.String).like(mentions)
    )
    scoped = sa.select(threads.c.id).where(
        sa.cast(threads.c.scope, sa.String).like(mentions)
    )
    affected = list(connection.execute(used.union(drew_on, cited, scoped)).scalars())
    if not affected:
        return
    connection.execute(
        threads.update().where(threads.c.id.in_(affected)).values(private_tainted=True)
    )
    connection.execute(
        messages.update()
        .where(messages.c.thread_id.in_(affected))
        .values(sensitivity=states.PRIVATE)
    )
    connection.execute(
        memories.update()
        .where(memories.c.source_thread_id.in_(affected))
        .values(sensitivity=states.PRIVATE)
    )


def _make_derived_standard(connection: Connection, document_id: str) -> None:
    """Chunks and the card follow the document. Tainted threads, their messages and
    memories stay Private."""
    for table in (chunks, cards):
        connection.execute(
            table.update()
            .where(table.c.document_id == document_id)
            .values(sensitivity=states.STANDARD)
        )


def apply_in(connection: Connection, document_id: str) -> bool:
    """Recomputes a document's sensitivity from its locations inside the caller's
    transaction, recording the index write to replay. True when it changed."""
    new = strictest(connection, document_id)
    current = connection.execute(
        sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
    ).scalar()
    if current == new:
        return False
    connection.execute(
        documents.update().where(documents.c.id == document_id).values(sensitivity=new)
    )
    if new == states.PRIVATE:
        _make_derived_private(connection, document_id)
    else:
        _make_derived_standard(connection, document_id)
    connection.execute(
        index.index_ops.insert().values(
            kind=OP_SENSITIVITY, document_id=document_id, created_at=time.time()
        )
    )
    return True


def follow_in_index() -> None:
    """After the transaction commits: LanceDB catches up at once."""
    index.replay_ops()
