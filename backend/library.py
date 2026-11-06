"""The library list: windows by offset, sort, filters, facets and totals."""

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import cards as card_records
from backend import database, states
from backend.database import (
    cards,
    collection_items,
    collections,
    document_locations,
    documents,
    sources,
)

SORTS = {
    "title": sa.func.lower(documents.c.title),
    "type": documents.c.doc_type,
    "date": documents.c.doc_date,
    "size": documents.c.size_bytes,
}
RECENT_VIEW = "recents"
STARRED_VIEW = "starred"
MOST_ROWS = 500
MOST_TAGS = 40


@dataclass(frozen=True)
class Filters:
    view: str | None = None
    doc_type: str | None = None
    source_id: str | None = None
    collection_id: str | None = None
    tag: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name: str | None = None
    include_private: bool = True


def _folder_name() -> Any:
    return (
        sa.select(sources.c.name)
        .join(document_locations, document_locations.c.source_id == sources.c.id)
        .where(document_locations.c.document_id == documents.c.id)
        .order_by(document_locations.c.missing, document_locations.c.id)
        .limit(1)
        .scalar_subquery()
        .label("folder")
    )


def _tags() -> Any:
    return sa.func.json_each(cards.c.tags).table_valued("value")


def _membership(filters: Filters, skip: str | None) -> list[Any]:
    """The filters that pick documents through another table."""
    found: list[Any] = []
    if filters.source_id and skip != "source":
        found.append(
            documents.c.id.in_(
                sa.select(document_locations.c.document_id).where(
                    document_locations.c.source_id == filters.source_id
                )
            )
        )
    if filters.collection_id:
        found.append(
            documents.c.id.in_(
                sa.select(collection_items.c.document_id).where(
                    collection_items.c.collection_id == filters.collection_id
                )
            )
        )
    if filters.tag and skip != "tag":
        tags = _tags()
        found.append(
            documents.c.id.in_(
                sa.select(cards.c.document_id)
                .select_from(cards.join(tags, sa.true()))
                .where(tags.c.value == filters.tag)
            )
        )
    return found


def _name_filter(name: str) -> Any:
    escaped = (
        name.strip()
        .lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return sa.func.lower(documents.c.title).like(f"%{escaped}%", escape="\\")


def _conditions(filters: Filters, skip: str | None = None) -> list[Any]:
    """The filters, leaving out one so its own facet still lists its other choices."""
    found: list[Any] = [documents.c.status != states.REMOVED]
    if filters.view == RECENT_VIEW:
        found.append(documents.c.last_opened_at.is_not(None))
    if filters.view == STARRED_VIEW:
        found.append(documents.c.starred.is_(True))
    if filters.doc_type and skip != "doc_type":
        found.append(documents.c.doc_type == filters.doc_type)
    if filters.date_from:
        found.append(documents.c.doc_date >= filters.date_from)
    if filters.date_to:
        found.append(documents.c.doc_date <= filters.date_to)
    if filters.name and filters.name.strip():
        found.append(_name_filter(filters.name))
    if not filters.include_private:
        found.append(documents.c.sensitivity == states.STANDARD)
    return found + _membership(filters, skip)


def _order(filters: Filters, sort: str, descending: bool) -> list[Any]:
    if filters.view == RECENT_VIEW:
        return [documents.c.last_opened_at.desc(), documents.c.id]
    column = SORTS[sort]
    # Undated and unsized documents stay at the end, whichever way it's sorted.
    return [
        column.is_(None),
        column.desc() if descending else column.asc(),
        documents.c.id,
    ]


def window(
    filters: Filters, sort: str, descending: bool, offset: int, limit: int
) -> dict[str, Any]:
    """One window of rows, with the total for the scrollbar."""
    conditions = _conditions(filters)
    with database.read() as connection:
        total = connection.execute(
            sa.select(sa.func.count()).select_from(documents).where(*conditions)
        ).scalar_one()
        rows = connection.execute(
            sa.select(documents, _folder_name())
            .where(*conditions)
            .order_by(*_order(filters, sort, descending))
            .offset(offset)
            .limit(min(limit, MOST_ROWS))
        ).mappings()
        return {"rows": [dict(row) for row in rows], "total": total, "offset": offset}


def ids(filters: Filters, sort: str, descending: bool) -> list[str]:
    """Select-all: every id the filters match, in the list's order."""
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(documents.c.id)
                .where(*_conditions(filters))
                .order_by(*_order(filters, sort, descending))
            ).scalars()
        )


def _type_counts(connection: Connection, filters: Filters) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(documents.c.doc_type, sa.func.count())
        .where(
            *_conditions(filters, skip="doc_type"), documents.c.doc_type.is_not(None)
        )
        .group_by(documents.c.doc_type)
    ).all()
    counts = {name: count for name, count in rows}
    listed = [t for t in card_records.TYPES if t in counts]
    return [{"value": t, "count": counts[t]} for t in listed]


def _folder_counts(connection: Connection, filters: Filters) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(
            sources.c.id, sources.c.name, sa.func.count(documents.c.id.distinct())
        )
        .join(document_locations, document_locations.c.source_id == sources.c.id)
        .join(documents, documents.c.id == document_locations.c.document_id)
        .where(*_conditions(filters, skip="source"))
        .group_by(sources.c.id)
        .order_by(sources.c.name)
    ).all()
    return [{"value": row[0], "label": row[1], "count": row[2]} for row in rows]


def _tag_counts(connection: Connection, filters: Filters) -> list[dict[str, Any]]:
    tags = _tags()
    count = sa.func.count().label("count")
    rows = connection.execute(
        sa.select(tags.c.value, count)
        .select_from(cards.join(tags, sa.true()))
        .where(
            cards.c.document_id.in_(
                sa.select(documents.c.id).where(*_conditions(filters, skip="tag"))
            )
        )
        .group_by(tags.c.value)
        .order_by(count.desc(), tags.c.value)
        .limit(MOST_TAGS)
    ).all()
    return [{"value": row[0], "count": row[1]} for row in rows]


def _collection_list(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(collections.c.id, collections.c.name).order_by(collections.c.name)
    ).all()
    return [{"value": row[0], "label": row[1]} for row in rows]


def facets(filters: Filters) -> dict[str, list[dict[str, Any]]]:
    with database.read() as connection:
        return {
            "types": _type_counts(connection, filters),
            "folders": _folder_counts(connection, filters),
            "tags": _tag_counts(connection, filters),
            "collections": _collection_list(connection),
        }


def sidebar_counts() -> dict[str, Any]:
    with database.read() as connection:
        total = connection.execute(
            sa.select(sa.func.count())
            .select_from(documents)
            .where(documents.c.status != states.REMOVED)
        ).scalar_one()
        types = connection.execute(
            sa.select(documents.c.doc_type, sa.func.count())
            .where(
                documents.c.status != states.REMOVED, documents.c.doc_type.is_not(None)
            )
            .group_by(documents.c.doc_type)
        ).all()
    return {"total": total, "types": {name: count for name, count in types}}
