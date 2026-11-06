"""Keeps a document's row in documents_fts in step with its title, path and card, which
are what the Documents group searches."""

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.database import cards, document_locations, documents


def refresh_in(connection: Connection, document_id: str) -> None:
    row = connection.execute(
        sa.select(documents.c.title, documents.c.file_name).where(
            documents.c.id == document_id
        )
    ).first()
    if row is None:
        return
    path = connection.execute(
        sa.select(document_locations.c.path)
        .where(document_locations.c.document_id == document_id)
        .limit(1)
    ).scalar()
    card = connection.execute(
        sa.select(
            cards.c.summary, cards.c.people, cards.c.organizations, cards.c.tags
        ).where(cards.c.document_id == document_id)
    ).first()
    summary = card.summary if card else ""
    names = [*card.people, *card.organizations, *card.tags] if card else []
    connection.exec_driver_sql(
        "DELETE FROM documents_fts WHERE document_id = ?", (document_id,)
    )
    connection.exec_driver_sql(
        "INSERT INTO documents_fts "
        "(document_id, title, file_name, path, summary, entities) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (document_id, row.title, row.file_name, path or "", summary, " ".join(names)),
    )
