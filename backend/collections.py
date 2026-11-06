"""The collections the owner makes."""

import time
import uuid
from typing import Any

import sqlalchemy as sa

from backend import database, refusals, states
from backend.database import collection_items, collections, documents

PREVIEW_TITLES = 3


def all_collections() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(collections).order_by(collections.c.created_at)
        ).mappings()
        found = []
        for row in rows.all():
            members = connection.execute(
                sa.select(documents.c.title, documents.c.sensitivity)
                .join(
                    collection_items, collection_items.c.document_id == documents.c.id
                )
                .where(
                    collection_items.c.collection_id == row["id"],
                    documents.c.status != states.REMOVED,
                )
                .order_by(documents.c.title)
            ).all()
            found.append(
                {
                    **row,
                    "count": len(members),
                    "titles": [m.title for m in members[:PREVIEW_TITLES]],
                    "has_private": any(
                        m.sensitivity == states.PRIVATE for m in members
                    ),
                }
            )
    return found


def create(name: str) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        raise refusals.invalid("A collection needs a name.")
    now = time.time()
    row = {
        "id": uuid.uuid4().hex,
        "name": cleaned,
        "created_at": now,
        "updated_at": now,
    }
    with database.write() as connection:
        connection.execute(collections.insert().values(**row))
    return {**row, "count": 0, "titles": [], "has_private": False}


def _touch(connection: Any, collection_id: str) -> None:
    moved = connection.execute(
        collections.update()
        .where(collections.c.id == collection_id)
        .values(updated_at=time.time())
    )
    if moved.rowcount != 1:
        raise refusals.not_found("There's no such collection.")


def add_documents(collection_id: str, document_ids: list[str]) -> None:
    with database.write() as connection:
        _touch(connection, collection_id)
        for document_id in document_ids:
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO collection_items (collection_id, document_id) "
                "VALUES (?, ?)",
                (collection_id, document_id),
            )


def remove_documents(collection_id: str, document_ids: list[str]) -> None:
    with database.write() as connection:
        _touch(connection, collection_id)
        connection.execute(
            collection_items.delete().where(
                collection_items.c.collection_id == collection_id,
                collection_items.c.document_id.in_(document_ids),
            )
        )


def of_document(document_id: str) -> list[dict[str, str]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(collections.c.id, collections.c.name)
            .join(
                collection_items, collection_items.c.collection_id == collections.c.id
            )
            .where(collection_items.c.document_id == document_id)
            .order_by(collections.c.name)
        ).all()
    return [{"id": row[0], "name": row[1]} for row in rows]
