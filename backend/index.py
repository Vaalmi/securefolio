"""The one way to LanceDB: BM25 plus vectors fused by reciprocal rank, prefiltered.
Only active revisions are ever written. SQLite stays the truth: everything here can
be rebuilt from its stored embeddings."""

import fcntl
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa
import sqlalchemy as sa
from lancedb.index import FTS
from lancedb.query import FullTextOperator, MatchQuery
from lancedb.rerankers import RRFReranker

from backend import database, embedder, states, vault
from backend.database import chunks, documents, index_generations, index_ops

logger = logging.getLogger(__name__)
WRITE_ATTEMPTS = 20
VECTOR_INDEX_AT = 5000
OP_PUBLISH = "publish"
OP_SENSITIVITY = "sensitivity"
OP_DELETE = "delete"


_thread_lock = threading.RLock()
_depth = [0]
_lock_file: list[Any] = []


@contextmanager
def _exclusive() -> Iterator[None]:
    """One process at a time changes the index, across the backend and the worker.
    Whatever a write depends on is read again inside this."""
    with _thread_lock:
        if _depth[0] == 0:
            file = open(vault.root() / "index.lock", "a+", encoding="utf-8")  # noqa: SIM115
            fcntl.flock(file, fcntl.LOCK_EX)
            _lock_file[:] = [file]
        _depth[0] += 1
        try:
            yield
        finally:
            _depth[0] -= 1
            if _depth[0] == 0:
                _lock_file.pop().close()


def _retrying[T](write: Callable[[], T]) -> T:
    """Two processes write here, and a commit can lose its race."""
    for attempt in range(WRITE_ATTEMPTS - 1):
        try:
            return write()
        except (OSError, RuntimeError, ValueError):
            logger.info("index write retried")
            time.sleep(0.05 * (attempt + 1))
    return write()


@lru_cache(maxsize=4)
def _connection(path: str) -> Any:
    return lancedb.connect(path)


def _db() -> Any:
    return _connection(str(vault.index_dir()))


def current_generation() -> int:
    """The active generation, recording the model on first use."""
    table = index_generations
    with database.write() as connection:
        found = connection.execute(
            sa.select(table.c.id).where(table.c.state == states.ACTIVE)
        ).scalar()
        if found is not None:
            return found
        result = connection.execute(
            table.insert().values(state=states.ACTIVE, **embedder.describe())
        )
        return result.lastrowid


def _table_name(generation: int) -> str:
    return f"chunks_g{generation}"


def _schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("document_id", pa.string()),
            pa.field("revision_id", pa.string()),
            pa.field("sensitivity", pa.string()),
            pa.field("doc_type", pa.string()),
            pa.field("doc_date", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), embedder.DIMENSION)),
        ]
    )


def _table_names(db: Any) -> set[str]:
    return set(db.list_tables().tables)


def _table(generation: int | None = None) -> Any:
    name = _table_name(current_generation() if generation is None else generation)
    db = _db()
    if name not in _table_names(db):

        def create() -> Any:
            table = db.create_table(name, schema=_schema(), exist_ok=True)
            table.create_index("text", config=FTS(), replace=True)
            return table

        return _retrying(create)
    return db.open_table(name)


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _rows_for(
    revision_id: str, sensitivity: str, doc_type: str, doc_date: str
) -> list[dict]:
    with database.read() as connection:
        found = connection.execute(
            sa.select(
                chunks.c.id, chunks.c.document_id, chunks.c.text, chunks.c.embedding
            ).where(
                chunks.c.revision_id == revision_id, chunks.c.embedding.is_not(None)
            )
        ).all()
    return [
        {
            "id": row.id,
            "document_id": row.document_id,
            "revision_id": revision_id,
            "sensitivity": sensitivity,
            "doc_type": doc_type,
            "doc_date": doc_date,
            "text": row.text,
            "vector": embedder.from_blob(row.embedding).tolist(),
        }
        for row in found
    ]


def publish(document_id: str, revision_id: str, generation: int | None = None) -> None:
    """Puts a revision's rows in and then takes the others out, so a reindexed document
    is never without passages. Safe to repeat: rows are upserted by chunk id. What it
    writes is read inside the lock: the active revision and sensitivity right now."""
    with _exclusive():
        with database.read() as connection:
            row = connection.execute(
                sa.select(
                    documents.c.active_revision_id,
                    documents.c.sensitivity,
                    documents.c.doc_type,
                    documents.c.doc_date,
                    documents.c.status,
                ).where(documents.c.id == document_id)
            ).first()
        if row is None or row.status == states.REMOVED:
            delete_document(document_id, generation)
            return
        active = row.active_revision_id or revision_id
        rows = _rows_for(
            active, row.sensitivity, row.doc_type or "", row.doc_date or ""
        )
        table = _table(generation)
        condition = (
            f"document_id = {_quoted(document_id)} AND revision_id != {_quoted(active)}"
        )

        def write() -> None:
            if rows:
                upsert = table.merge_insert("id")
                upsert.when_matched_update_all().when_not_matched_insert_all().execute(
                    rows
                )
            table.delete(condition)

        _retrying(write)


def delete_document(document_id: str, generation: int | None = None) -> None:
    with _exclusive():
        table = _table(generation)
        _retrying(lambda: table.delete(f"document_id = {_quoted(document_id)}"))


def set_sensitivity(document_id: str, sensitivity: str) -> None:
    with _exclusive():
        table = _table()
        _retrying(
            lambda: table.update(
                where=f"document_id = {_quoted(document_id)}",
                values={"sensitivity": sensitivity},
            )
        )


def replay_ops() -> int:
    """Finishes writes a crash left undone, or that the caller just recorded, then
    clears their records. Both processes call this; the lock serializes them, and an
    op is cleared only after its write."""
    with _exclusive():
        with database.read() as connection:
            rows = connection.execute(
                sa.select(index_ops).order_by(index_ops.c.id)
            ).all()
        for row in rows:
            if row.kind == OP_PUBLISH:
                publish(row.document_id, row.revision_id)
            elif row.kind == OP_DELETE:
                delete_document(row.document_id)
            elif row.kind == OP_SENSITIVITY:
                _replay_sensitivity(row.document_id)
            with database.write() as connection:
                connection.execute(index_ops.delete().where(index_ops.c.id == row.id))
    return len(rows)


def _replay_sensitivity(document_id: str) -> None:
    with database.read() as connection:
        value = connection.execute(
            sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
        ).scalar()
    if value is not None:
        set_sensitivity(document_id, value)


def _active_revisions() -> dict[str, str]:
    with database.read() as connection:
        return dict(
            connection.execute(
                sa.select(documents.c.id, documents.c.active_revision_id).where(
                    documents.c.active_revision_id.is_not(None),
                    documents.c.status != states.REMOVED,
                )
            ).all()
        )


def sweep(generation: int | None = None) -> int:
    """Makes a generation's table match SQLite's active revisions, both ways."""
    with _exclusive():
        replay_ops()
        active = _active_revisions()
        table = _table(generation)
        held = []
        if table.count_rows():
            query = table.search().select(["document_id", "revision_id"])
            held = query.limit(table.count_rows()).to_list()
        seen: dict[str, set[str]] = {}
        for row in held:
            seen.setdefault(row["document_id"], set()).add(row["revision_id"])
        changed = 0
        for document_id, held_revisions in seen.items():
            if held_revisions != {active.get(document_id)}:
                if document_id in active:
                    publish(document_id, active[document_id], generation)
                else:
                    delete_document(document_id, generation)
                changed += 1
        for document_id, revision_id in active.items():
            if document_id not in seen:
                publish(document_id, revision_id, generation)
                changed += 1
        return changed


def rebuild(generation: int | None = None) -> None:
    """Writes a generation's table again from stored embeddings, opening no original
    and touching no other generation."""
    with _exclusive():
        target = current_generation() if generation is None else generation
        db = _db()
        name = _table_name(target)
        if name in _table_names(db):
            db.drop_table(name)
        sweep(target)


def optimize() -> None:
    table = _table()
    count = table.count_rows()
    if count >= VECTOR_INDEX_AT and not any(
        "vector" in index.columns for index in table.list_indices()
    ):
        table.create_index(metric="cosine", vector_column_name="vector", replace=True)
    _retrying(table.optimize)


def count() -> int:
    return _table().count_rows()


def where_clause(
    sensitivities: list[str] | None = None,
    document_ids: list[str] | None = None,
    doc_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str | None:
    """The prefilter: applied before anything is scored."""
    parts = []
    if sensitivities is not None:
        parts.append(f"sensitivity IN ({','.join(_quoted(s) for s in sensitivities)})")
    if document_ids is not None:
        listed = ",".join(_quoted(d) for d in document_ids) or "''"
        parts.append(f"document_id IN ({listed})")
    if doc_type is not None:
        parts.append(f"doc_type = {_quoted(doc_type)}")
    if date_from is not None:
        parts.append(f"doc_date >= {_quoted(date_from)}")
    if date_to is not None:
        parts.append(f"doc_date <= {_quoted(date_to)}")
    return " AND ".join(parts) or None


COLUMNS = ["id", "document_id", "revision_id", "sensitivity", "text"]


def search(
    text: str, vector: np.ndarray | None, where: str | None, limit: int
) -> list[dict[str, Any]]:
    """Hybrid when there's a vector; BM25 alone when the model isn't ready yet."""
    table = _table()
    if table.count_rows() == 0:
        return []
    if vector is None:
        query = table.search(text, query_type="fts", fts_columns="text")
    else:
        query = (
            table.search(query_type="hybrid", fts_columns="text")
            .vector(vector.tolist())
            .text(text)
            .rerank(RRFReranker())
        )
    if where:
        query = query.where(where, prefilter=True)
    try:
        return query.select(COLUMNS).limit(limit).to_list()
    except (OSError, RuntimeError, ValueError):
        # A query BM25 can't parse (only punctuation, say) finds nothing.
        logger.info("index search failed for a query")
        return []


def search_every_word(text: str, where: str | None, limit: int) -> list[dict[str, Any]]:
    """BM25 rows holding every word of `text`, as this index's own tokenizer reads
    words, so punctuation, case and accents match exactly as they do in search."""
    table = _table()
    if table.count_rows() == 0:
        return []
    # Every argument given: pydantic's defaults are invisible to the type checker.
    every_word = MatchQuery(
        text,
        "text",
        boost=1.0,
        fuzziness=0,  # exact words, as search matches them
        max_expansions=50,
        operator=FullTextOperator.AND,
        prefix_length=0,
        document_granularity=None,
    )
    query = table.search(every_word, query_type="fts")
    if where:
        query = query.where(where, prefilter=True)
    try:
        return query.select(COLUMNS).limit(limit).to_list()
    except (OSError, RuntimeError, ValueError):
        logger.info("index search failed for a query")
        return []
