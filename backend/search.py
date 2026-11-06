"""Typeahead: Documents from documents_fts and Passages from the index, each checked
against the active revision and current policy. It never waits on a model."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, embedder, index, locators, policy, states
from backend.database import (
    cards,
    chunks,
    collection_items,
    document_locations,
    documents,
    elements,
    sources,
)

DOCUMENT_LIMIT = 4
PASSAGE_LIMIT = 4
FIRST_FETCH = 12
MOST_FETCHED = 384
SNIPPET_CHARACTERS = 240
WORD = re.compile(r"\S+")
HAS_TOKEN = re.compile(r"\w")
# Title first, then the file name, entities, summary and path.
RANK = "bm25(documents_fts, 0.0, 10.0, 6.0, 1.0, 2.0, 3.0)"
DOCUMENTS_FETCHED = 32
FTS = sa.table("documents_fts", sa.column("document_id"))


@dataclass(frozen=True)
class Scope:
    include_private: bool = False
    source_id: str | None = None
    doc_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    collection_id: str | None = None
    tag: str | None = None
    document_ids: tuple[str, ...] | None = None

    def narrowed(self) -> bool:
        return (
            self.source_id is not None
            or self.collection_id is not None
            or self.tag is not None
            or self.document_ids is not None
        )


def _fts_query(text: str) -> str | None:
    """Every word must match, the last as a prefix, since this runs as you type."""
    words = [w for w in WORD.findall(text) if HAS_TOKEN.search(w)]
    if not words:
        return None
    quoted = [f'"{word.replace(chr(34), chr(34) * 2)}"' for word in words]
    quoted[-1] += "*"
    return " ".join(quoted)


def _narrowed_ids(connection: Connection, scope: Scope) -> list[str] | None:
    """The source, collection, tag and selection filters, as document ids."""
    found: set[str] | None = (
        None if scope.document_ids is None else set(scope.document_ids)
    )

    def keep(ids: set[str]) -> None:
        nonlocal found
        found = ids if found is None else found & ids

    if scope.source_id is not None:
        keep(
            set(
                connection.execute(
                    sa.select(document_locations.c.document_id).where(
                        document_locations.c.source_id == scope.source_id
                    )
                ).scalars()
            )
        )
    if scope.collection_id is not None:
        keep(
            set(
                connection.execute(
                    sa.select(collection_items.c.document_id).where(
                        collection_items.c.collection_id == scope.collection_id
                    )
                ).scalars()
            )
        )
    if scope.tag is not None:
        tags = sa.func.json_each(cards.c.tags).table_valued("value")
        keep(
            set(
                connection.execute(
                    sa.select(cards.c.document_id)
                    .select_from(cards.join(tags, sa.true()))
                    .where(tags.c.value == scope.tag)
                ).scalars()
            )
        )
    return None if found is None else sorted(found)


def _in_scope(row: Any, scope: Scope, ids: list[str] | None) -> bool:
    """The fresh check every passage passes, whatever the index still says."""
    if row["sensitivity"] not in policy.scope_sensitivities(scope.include_private):
        return False
    if scope.doc_type is not None and row["doc_type"] != scope.doc_type:
        return False
    date = row["doc_date"]
    if scope.date_from is not None and (date is None or date < scope.date_from):
        return False
    if scope.date_to is not None and (date is None or date > scope.date_to):
        return False
    return ids is None or row["id"] in ids


def _matching(query: str, scope: Scope, ids: list[str] | None) -> Any:
    """Documents the words match, narrowed before anything is ranked or cut off."""
    found = (
        sa.select(documents)
        .select_from(FTS.join(documents, documents.c.id == FTS.c.document_id))
        .where(
            sa.text("documents_fts MATCH :query").bindparams(query=query),
            documents.c.status != states.REMOVED,
        )
    )
    if scope.doc_type is not None:
        found = found.where(documents.c.doc_type == scope.doc_type)
    if scope.date_from is not None:
        found = found.where(documents.c.doc_date >= scope.date_from)
    if scope.date_to is not None:
        found = found.where(documents.c.doc_date <= scope.date_to)
    if ids is not None:
        found = found.where(documents.c.id.in_(ids))
    return found


def _folder_of(document_id: Any) -> Any:
    return (
        sa.select(sources.c.name)
        .join(document_locations, document_locations.c.source_id == sources.c.id)
        .where(document_locations.c.document_id == document_id)
        .limit(1)
        .scalar_subquery()
    )


def _documents(
    connection: Connection, text: str, scope: Scope, ids: list[str] | None
) -> tuple[list[dict[str, Any]], set[str]]:
    """The documents shown, and the ids of those the scope leaves out."""
    query = _fts_query(text)
    if query is None:
        return [], set()
    matching = _matching(query, scope, ids)
    allowed = policy.scope_sensitivities(scope.include_private)
    rows = connection.execute(
        matching.add_columns(_folder_of(documents.c.id).label("folder"))
        .where(documents.c.sensitivity.in_(allowed))
        .order_by(sa.text(RANK))
        .limit(DOCUMENTS_FETCHED)
    ).mappings()
    found = [dict(row) for row in rows]
    left_out = matching.where(documents.c.sensitivity.not_in(allowed)).subquery()
    hidden = set(connection.execute(sa.select(left_out.c.id)).scalars())
    needle = text.casefold().strip()
    found.sort(key=lambda row: needle not in row["title"].casefold())
    return found[:DOCUMENT_LIMIT], hidden


def _hidden_passages(text: str, scope: Scope, ids: list[str] | None) -> set[str]:
    """Private documents a passage of which holds every word, when the scope leaves
    Private out. SQLite says which documents qualify, never the index's columns, which
    can be stale. The index's own all-words match, never a copy of its tokenizer, and
    no vector: a nearest vector always exists, so it proves no match."""
    if scope.include_private or not HAS_TOKEN.search(text):
        return set()
    eligible = sa.select(documents.c.id).where(
        documents.c.sensitivity == states.PRIVATE,
        documents.c.status != states.REMOVED,
    )
    if scope.doc_type is not None:
        eligible = eligible.where(documents.c.doc_type == scope.doc_type)
    if scope.date_from is not None:
        eligible = eligible.where(documents.c.doc_date >= scope.date_from)
    if scope.date_to is not None:
        eligible = eligible.where(documents.c.doc_date <= scope.date_to)
    if ids is not None:
        eligible = eligible.where(documents.c.id.in_(ids))
    with database.read() as connection:
        private = sorted(connection.execute(eligible).scalars())
    if not private:
        return set()
    where = index.where_clause(document_ids=private)
    rows = index.search_every_word(text, where, MOST_FETCHED)
    widened = replace(scope, include_private=True)
    with database.read() as connection:
        kept = _checked(connection, rows, widened, ids)
    return {
        item["document"]["id"]
        for item in kept
        if item["document"]["sensitivity"] == states.PRIVATE
    }


def _checked(
    connection: Connection,
    rows: list[dict[str, Any]],
    scope: Scope,
    ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Drops a candidate from a retired revision, or one whose stale index column
    disagrees with SQLite. Vectors are the index's; the truth is here."""
    wanted = {row["document_id"] for row in rows}
    current = {
        row["id"]: row
        for row in connection.execute(
            sa.select(documents).where(documents.c.id.in_(wanted))
        ).mappings()
    }
    kept = []
    for row in rows:
        document = current.get(row["document_id"])
        if (
            document is not None
            and document["active_revision_id"] == row["revision_id"]
            and document["status"] != states.REMOVED
            and _in_scope(document, scope, ids)
        ):
            kept.append({"hit": row, "document": document})
    return kept


def _place(
    connection: Connection, chunk_id: str, needle: str
) -> tuple[str | None, dict | None]:
    """Where a passage opens: the element in its chunk holding the most of what was
    asked, so a chunk of many rows or paragraphs opens at the right one."""
    element_ids = connection.execute(
        sa.select(chunks.c.element_ids).where(chunks.c.id == chunk_id)
    ).scalar()
    if not element_ids:
        return None, None
    rows = connection.execute(
        sa.select(elements.c.id, elements.c.text, elements.c.locator).where(
            elements.c.id.in_(element_ids)
        )
    ).all()
    words = WORD.findall(needle)

    def score(row: Any) -> tuple[bool, int, int]:
        text = row.text.casefold()
        held = sum(word in text for word in words)
        return (needle in text, held, -element_ids.index(row.id))

    best = max(rows, key=score)
    return best.id, best.locator


def _snippet(text: str, needle: str) -> str:
    at = text.casefold().find(needle)
    if at < SNIPPET_CHARACTERS // 2:
        return text[:SNIPPET_CHARACTERS]
    start = at - SNIPPET_CHARACTERS // 3
    return "…" + text[start : start + SNIPPET_CHARACTERS]


def _candidates(
    text: str, vector: Any, where: str | None, scope: Scope, ids: list[str] | None
) -> list[dict[str, Any]]:
    """Refills when a check drops candidates, so a stale row can't shrink the group."""
    fetch = FIRST_FETCH
    needle = text.casefold().strip()
    while True:
        rows = index.search(text, vector, where, fetch)
        with database.read() as connection:
            kept = _checked(connection, rows, scope, ids)
        kept.sort(key=lambda item: needle not in item["hit"]["text"].casefold())
        if len(kept) >= PASSAGE_LIMIT or len(rows) < fetch or fetch >= MOST_FETCHED:
            return kept[:PASSAGE_LIMIT]
        fetch *= 2


def _passages(
    text: str, vector: Any, scope: Scope, ids: list[str] | None
) -> list[dict]:
    where = index.where_clause(
        sensitivities=policy.scope_sensitivities(scope.include_private),
        document_ids=ids,
        doc_type=scope.doc_type,
        date_from=scope.date_from,
        date_to=scope.date_to,
    )
    items = _candidates(text, vector, where, scope, ids)
    needle = text.casefold().strip()
    passages = []
    with database.read() as connection:
        for item in items:
            hit, document = item["hit"], item["document"]
            element_id, locator = _place(connection, hit["id"], needle)
            passages.append(
                {
                    "id": hit["id"],
                    "element_id": element_id,
                    "snippet": _snippet(hit["text"], needle),
                    "locator": locator,
                    "label": _label(locator),
                    "document": dict(document),
                }
            )
    return passages


def _label(locator: dict | None) -> str:
    return locators.label(locators.load(locator)) if locator else "In the document"


def warm() -> None:
    """Focusing a search box starts the load; nothing waits for it."""
    embedder.start_loading()


def run(
    text: str, scope: Scope, ready: Callable[[], bool] = embedder.is_ready
) -> dict[str, Any]:
    """BM25 at once. Once the model has loaded, the app's next request gets meaning
    merged in, which `semantic` tells it to make."""
    semantic = ready()
    vector = embedder.embed_query(text) if semantic and text.strip() else None
    with database.read() as connection:
        ids = _narrowed_ids(connection, scope) if scope.narrowed() else None
        found, hidden = _documents(connection, text, scope, ids)
    hidden |= _hidden_passages(text, scope, ids)
    return {
        "documents": found,
        "passages": _passages(text, vector, scope, ids) if text.strip() else [],
        "hidden_private": len(hidden),
        "semantic": semantic,
    }


def retrieve(
    query: str, scope: Scope, fetch: int, exclude: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """For Ask: candidates with their elements, each checked fresh. Unlike typeahead
    this waits for the embedder, as a question is worth a real answer."""
    vector = embedder.embed_query(query)
    with database.read() as connection:
        ids = _narrowed_ids(connection, scope) if scope.narrowed() else None
    where = index.where_clause(
        sensitivities=policy.scope_sensitivities(scope.include_private),
        document_ids=ids,
        doc_type=scope.doc_type,
        date_from=scope.date_from,
        date_to=scope.date_to,
    )
    rows = index.search(query, vector, where, fetch)
    found = []
    with database.read() as connection:
        for item in _checked(connection, rows, scope, ids):
            if item["document"]["id"] in exclude:
                continue
            found.append(_with_elements(connection, item))
    return found


def _with_elements(connection: Connection, item: dict[str, Any]) -> dict[str, Any]:
    hit, document = item["hit"], item["document"]
    element_ids = connection.execute(
        sa.select(chunks.c.element_ids).where(chunks.c.id == hit["id"])
    ).scalar()
    rows = (
        connection.execute(
            sa.select(elements.c.id, elements.c.text, elements.c.locator).where(
                elements.c.id.in_(element_ids or [])
            )
        )
        .mappings()
        .all()
    )
    order = {element_id: at for at, element_id in enumerate(element_ids or [])}
    placed = sorted(rows, key=lambda row: order[row["id"]])
    return {
        "chunk_id": hit["id"],
        "text": hit["text"],
        "document_id": document["id"],
        "revision_id": document["active_revision_id"],
        "title": document["title"],
        "sensitivity": document["sensitivity"],
        "elements": [
            {
                "id": row["id"],
                "text": row["text"],
                "locator": row["locator"],
                "label": _label(row["locator"]),
            }
            for row in placed
        ],
    }


def coverage(scope: Scope) -> dict[str, int]:
    """What "searched" means in a no-evidence reply, counted in code."""
    with database.read() as connection:
        ids = _narrowed_ids(connection, scope) if scope.narrowed() else None
        conditions = [
            documents.c.sensitivity.in_(
                policy.scope_sensitivities(scope.include_private)
            )
        ]
        if ids is not None:
            conditions.append(documents.c.id.in_(ids))

        def count(*wanted: str) -> int:
            return connection.execute(
                sa.select(sa.func.count())
                .select_from(documents)
                .where(*conditions, documents.c.status.in_(wanted))
            ).scalar_one()

        hidden = 0
        if not scope.include_private:
            hidden = connection.execute(
                sa.select(sa.func.count())
                .select_from(sources)
                .where(
                    sources.c.sensitivity == states.PRIVATE,
                    sources.c.status != states.REMOVED,
                )
            ).scalar_one()
        return {
            "searched": count(states.INDEXED),
            "indexing": count(states.QUEUED, states.INDEXING),
            "private_folders": hidden,
        }


def library_facts(scope: Scope) -> dict[str, Any]:
    """What a question about the library itself is answered from, counted in code."""
    with database.read() as connection:
        ids = _narrowed_ids(connection, scope) if scope.narrowed() else None
        conditions = [
            documents.c.status != states.REMOVED,
            documents.c.sensitivity.in_(
                policy.scope_sensitivities(scope.include_private)
            ),
        ]
        if ids is not None:
            conditions.append(documents.c.id.in_(ids))
        statuses = connection.execute(
            sa.select(documents.c.status, sa.func.count())
            .where(*conditions)
            .group_by(documents.c.status)
        ).all()
        types = connection.execute(
            sa.select(documents.c.doc_type, sa.func.count())
            .where(*conditions, documents.c.doc_type.is_not(None))
            .group_by(documents.c.doc_type)
            .order_by(sa.func.count().desc(), documents.c.doc_type)
        ).all()
    return {
        "statuses": {status: count for status, count in statuses},
        "types": {name: count for name, count in types},
        "private_folders": coverage(scope)["private_folders"],
    }


def evidence(chunk_ids: list[str], include_private: bool) -> list[dict[str, Any]]:
    """Passages by id, checked again against the active revision and current policy."""
    if not chunk_ids:
        return []
    allowed = policy.scope_sensitivities(include_private)
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(
                    chunks.c.id.label("chunk_id"),
                    chunks.c.text,
                    documents.c.id.label("document_id_ref"),
                    documents.c.title,
                    documents.c.sensitivity,
                    documents.c.active_revision_id,
                )
                .join(documents, documents.c.id == chunks.c.document_id)
                .where(
                    chunks.c.id.in_(chunk_ids),
                    documents.c.active_revision_id == chunks.c.revision_id,
                    documents.c.sensitivity.in_(allowed),
                    documents.c.status != states.REMOVED,
                )
            )
            .mappings()
            .all()
        )
        by_id = {row["chunk_id"]: row for row in rows}
        found = []
        for chunk_id in chunk_ids:
            row = by_id.get(chunk_id)
            if row is None:
                continue
            document = {
                "id": row["document_id_ref"],
                "title": row["title"],
                "sensitivity": row["sensitivity"],
                "active_revision_id": row["active_revision_id"],
            }
            hit = {"id": chunk_id, "text": row["text"]}
            found.append(_with_elements(connection, {"hit": hit, "document": document}))
    return found


def title_of(document_id: str) -> str:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(documents.c.title).where(documents.c.id == document_id)
            ).scalar()
            or document_id
        )
