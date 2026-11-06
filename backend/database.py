"""The engine on library.db and its table definitions, in SQLAlchemy Core."""

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from backend import vault

BUSY_TIMEOUT_MS = 5000

metadata = sa.MetaData()

settings = sa.Table(
    "settings",
    metadata,
    sa.Column("key", sa.String, primary_key=True),
    sa.Column("value", sa.JSON, nullable=False),
)


sources = sa.Table(
    "sources",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("path", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("sensitivity", sa.String, nullable=False),
    sa.Column("auto_organize", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("mcp_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("status_reason", sa.String),
    sa.Column(
        "return_to_paused", sa.Boolean, nullable=False, server_default=sa.false()
    ),
    sa.Column("last_scan_at", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False),
)
sa.Index(
    "sources_live_path",
    sources.c.path,
    unique=True,
    sqlite_where=sources.c.status != "removed",
)

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("file_name", sa.String, nullable=False),
    sa.Column("file_kind", sa.String, nullable=False),
    sa.Column("content_sha256", sa.String, index=True),
    sa.Column("active_revision_id", sa.String),
    sa.Column("status", sa.String, nullable=False, index=True),
    sa.Column("status_reason", sa.String),
    sa.Column("error_code", sa.String),
    sa.Column("sensitivity", sa.String, nullable=False, index=True),
    sa.Column("starred", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("size_bytes", sa.Integer, index=True),
    sa.Column("pages", sa.Integer),
    sa.Column("doc_date", sa.String, index=True),
    sa.Column("doc_type", sa.String, index=True),
    sa.Column("added_at", sa.Float, nullable=False, index=True),
    sa.Column("last_opened_at", sa.Float, index=True),
)

sa.Index("documents_lower_title", sa.func.lower(documents.c.title))

document_locations = sa.Table(
    "document_locations",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("document_id", sa.ForeignKey("documents.id"), nullable=False, index=True),
    sa.Column("source_id", sa.ForeignKey("sources.id"), nullable=False, index=True),
    sa.Column("path", sa.String, nullable=False, index=True),
    sa.Column("inode", sa.Integer),
    sa.Column("size_bytes", sa.Integer),
    sa.Column("mtime", sa.Float),
    sa.Column("storage_mode", sa.String, nullable=False),
    sa.Column("sensitivity", sa.String, nullable=False),
    sa.Column("missing", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("dataless", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.UniqueConstraint("source_id", "path"),
)

revisions = sa.Table(
    "revisions",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("document_id", sa.ForeignKey("documents.id"), nullable=False, index=True),
    sa.Column("job_id", sa.String, index=True),
    sa.Column("content_sha256", sa.String, nullable=False),
    sa.Column("parser_version", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False, index=True),
    sa.Column("created_at", sa.Float, nullable=False),
)

elements = sa.Table(
    "elements",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("revision_id", sa.ForeignKey("revisions.id"), nullable=False, index=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("text_hash", sa.String, nullable=False, index=True),
    sa.Column("locator", sa.JSON, nullable=False),
    sa.Column("reading_state", sa.String),
    sa.Column("reading_reason", sa.String),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
)

chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("revision_id", sa.ForeignKey("revisions.id"), nullable=False, index=True),
    sa.Column("document_id", sa.ForeignKey("documents.id"), nullable=False, index=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("element_ids", sa.JSON, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("token_count", sa.Integer, nullable=False),
    sa.Column("embedding", sa.LargeBinary),
    sa.Column("sensitivity", sa.String, nullable=False),
)

page_readings = sa.Table(
    "page_readings",
    metadata,
    sa.Column("revision_id", sa.ForeignKey("revisions.id"), primary_key=True),
    sa.Column("page", sa.Integer, primary_key=True),
    sa.Column("reasons", sa.JSON, nullable=False),
    sa.Column("state", sa.String, nullable=False),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("document_id", sa.String, index=True),
    sa.Column("source_id", sa.String, index=True),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column("state", sa.String, nullable=False, index=True),
    sa.Column("stage", sa.String),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("lease_expires_at", sa.Float),
    sa.Column(
        "cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()
    ),
    sa.Column("error_code", sa.String),
    sa.Column("reason", sa.String),
    sa.Column("idempotency_key", sa.String),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)
sa.Index(
    "jobs_live_key",
    jobs.c.idempotency_key,
    unique=True,
    sqlite_where=jobs.c.state.in_(("queued", "running")),
)
sa.Index("jobs_claim", jobs.c.state, jobs.c.priority, jobs.c.created_at)

job_events = sa.Table(
    "job_events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("job_id", sa.ForeignKey("jobs.id"), nullable=False, index=True),
    sa.Column("at", sa.Float, nullable=False),
    sa.Column("event", sa.String, nullable=False),
)

cards = sa.Table(
    "cards",
    metadata,
    sa.Column("document_id", sa.ForeignKey("documents.id"), primary_key=True),
    sa.Column("revision_id", sa.String),
    sa.Column("state", sa.String, nullable=False, index=True),
    sa.Column("state_reason", sa.String),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False, server_default=""),
    sa.Column("doc_type", sa.String, nullable=False, server_default="Other"),
    sa.Column("dates", sa.JSON, nullable=False),
    sa.Column("people", sa.JSON, nullable=False),
    sa.Column("organizations", sa.JSON, nullable=False),
    sa.Column("amounts", sa.JSON, nullable=False),
    sa.Column("tags", sa.JSON, nullable=False),
    sa.Column("edited_fields", sa.JSON, nullable=False),
    sa.Column("written_by", sa.String, nullable=False),
    sa.Column("embedding", sa.LargeBinary),
    sa.Column("sensitivity", sa.String, nullable=False),
)

collections = sa.Table(
    "collections",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)

collection_items = sa.Table(
    "collection_items",
    metadata,
    sa.Column("collection_id", sa.ForeignKey("collections.id"), primary_key=True),
    sa.Column("document_id", sa.ForeignKey("documents.id"), primary_key=True),
)

notes = sa.Table(
    "notes",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("citations", sa.JSON, nullable=False),
    sa.Column("document_id", sa.String, index=True),
    sa.Column("source_label", sa.String, nullable=False),
    sa.Column("sensitivity", sa.String, nullable=False),
    sa.Column("thread_id", sa.String),
    sa.Column("created_at", sa.Float, nullable=False),
)

saved_queries = sa.Table(
    "saved_queries",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("sensitivity", sa.String, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
)

threads = sa.Table(
    "threads",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("scope", sa.JSON, nullable=False),
    sa.Column("mode", sa.String, nullable=False),
    sa.Column("private_tainted", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("memory_off", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("summary", sa.Text, nullable=False, server_default=""),
    sa.Column("summary_version", sa.Integer, nullable=False, server_default="0"),
    sa.Column("summary_through", sa.Integer, nullable=False, server_default="0"),
    sa.Column("memory_through", sa.Integer, nullable=False, server_default="0"),
    sa.Column("ask_when_indexed", sa.Text),
    sa.Column("imported", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False, index=True),
)

thread_documents = sa.Table(
    "thread_documents",
    metadata,
    sa.Column("thread_id", sa.ForeignKey("threads.id"), primary_key=True),
    sa.Column("document_id", sa.String, primary_key=True, index=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
)

thread_evidence = sa.Table(
    "thread_evidence",
    metadata,
    sa.Column("thread_id", sa.ForeignKey("threads.id"), primary_key=True),
    sa.Column("document_id", sa.String, primary_key=True, index=True),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("thread_id", sa.ForeignKey("threads.id"), nullable=False, index=True),
    sa.Column("run_id", sa.String, index=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("role", sa.String, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("answer", sa.JSON),
    sa.Column("sensitivity", sa.String, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.UniqueConstraint("run_id", "role"),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("thread_id", sa.ForeignKey("threads.id"), nullable=False, index=True),
    sa.Column("state", sa.String, nullable=False),
    sa.Column("checkpoint_id", sa.String),
    sa.Column("card", sa.JSON),
    sa.Column("error", sa.String),
    sa.Column("question", sa.Text),
    sa.Column("undo", sa.JSON),
    # The owner's answer to each kind of card this run asked, with the card it answers,
    # so the run reads what was agreed to rather than replaying the question.
    sa.Column("answered", sa.JSON),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)
sa.Index(
    "runs_one_active",
    runs.c.thread_id,
    unique=True,
    sqlite_where=runs.c.state.in_(("running", "paused")),
)

run_events = sa.Table(
    "run_events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("run_id", sa.ForeignKey("runs.id"), nullable=False, index=True),
    sa.Column("event", sa.String, nullable=False),
    sa.Column("data", sa.JSON, nullable=False),
)

memories = sa.Table(
    "memories",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("embedding", sa.LargeBinary),
    sa.Column("sensitivity", sa.String, nullable=False, index=True),
    sa.Column("source_thread_id", sa.String, index=True),
    sa.Column("source_message_ids", sa.JSON),
    sa.Column("source_label", sa.String, nullable=False),
    sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("state", sa.String, nullable=False, index=True),
    sa.Column("expires_at", sa.Float),
    sa.Column("last_used_at", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False),
)

memory_tombstones = sa.Table(
    "memory_tombstones",
    metadata,
    sa.Column("text_hash", sa.String, primary_key=True),
    sa.Column("embedding", sa.LargeBinary),
    sa.Column("created_at", sa.Float, nullable=False),
)

message_batches = sa.Table(
    "message_batches",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("submission_id", sa.String, nullable=False, unique=True),
    sa.Column("provider_id", sa.String),
    # Which provider and model it went to: polled, settled and logged against these,
    # whatever the helper model is set to by the time it ends.
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("model", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False, index=True),
    sa.Column("marked_at", sa.Float),
    sa.Column("next_poll_at", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False),
)

batch_items = sa.Table(
    "batch_items",
    metadata,
    sa.Column("batch_id", sa.ForeignKey("message_batches.id"), primary_key=True),
    sa.Column("document_id", sa.String, primary_key=True),
    sa.Column("revision_id", sa.String, nullable=False),
    sa.Column("excerpt_hash", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False),
)

egress_log = sa.Table(
    "egress_log",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("at", sa.Float, nullable=False, index=True),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("model", sa.String, nullable=False),  # its full id, provider:model
    sa.Column("purpose", sa.String, nullable=False),
    sa.Column("outcome", sa.String, nullable=False),
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
    sa.Column("document_ids", sa.JSON, nullable=False),
    sa.Column("thread_id", sa.String),
    sa.Column("detail", sa.String),
    # The price per million tokens the call was accepted at, kept until it settles: a
    # refresh of OpenRouter's list while it's out can't change or lose it.
    sa.Column("input_usd", sa.Float),
    sa.Column("output_usd", sa.Float),
)

# A snapshot of OpenRouter's published list, with the prices SecureFolio may call at.
openrouter_models = sa.Table(
    "openrouter_models",
    metadata,
    sa.Column("id", sa.String, primary_key=True),  # OpenRouter's own id, no prefix
    sa.Column("name", sa.String, nullable=False),
    sa.Column("input_usd", sa.Float),  # per million tokens; None when it has no price
    sa.Column("output_usd", sa.Float),
    sa.Column("images", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("structured", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("listed", sa.Boolean, nullable=False, server_default="1"),
    sa.Column("fetched_at", sa.Float, nullable=False),
)

v1_imports = sa.Table(
    "v1_imports",
    metadata,
    sa.Column("kind", sa.String, primary_key=True),
    sa.Column("v1_id", sa.String, primary_key=True),
    sa.Column("new_id", sa.String, nullable=False),
    sa.Column("imported_at", sa.Float, nullable=False),
)

model_downloads = sa.Table(
    "model_downloads",
    metadata,
    sa.Column("model", sa.String, primary_key=True),
    sa.Column("state", sa.String, nullable=False),
    sa.Column("bytes_done", sa.Integer, nullable=False, server_default="0"),
    sa.Column("bytes_total", sa.Integer, nullable=False, server_default="0"),
    sa.Column("reason", sa.String),
    sa.Column("updated_at", sa.Float, nullable=False),
)

index_generations = sa.Table(
    "index_generations",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("model", sa.String, nullable=False),
    sa.Column("model_revision", sa.String, nullable=False),
    sa.Column("query_instruction", sa.String, nullable=False),
    sa.Column("document_instruction", sa.String, nullable=False),
    sa.Column("dimension", sa.Integer, nullable=False),
    sa.Column("metric", sa.String, nullable=False),
    sa.Column("truncation", sa.Integer, nullable=False),
    sa.Column("state", sa.String, nullable=False),
)

index_ops = sa.Table(
    "index_ops",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("document_id", sa.String, nullable=False),
    sa.Column("revision_id", sa.String),
    sa.Column("created_at", sa.Float, nullable=False),
)

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
    "document_id UNINDEXED, title, file_name, path, summary, entities, "
    "tokenize='unicode61 remove_diacritics 2')"
)


def create_tables(connection: Connection, names: list[str]) -> None:
    """Creates tables by name from the definitions above. No release has shipped,
    so definitions are still edited in place; after one, a change needs its own
    explicit migration."""
    tables = [metadata.tables[name] for name in names]
    metadata.create_all(connection, tables=tables)


def _on_connect(connection: Any, _record: Any) -> None:
    # pysqlite's own transaction handling would defeat BEGIN IMMEDIATE.
    connection.isolation_level = None
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


_engines: dict[str, Engine] = {}


def engine() -> Engine:
    """The engine for the current vault, one per path in the process."""
    path = str(vault.library_db())
    if path not in _engines:
        built = sa.create_engine(f"sqlite:///{path}")
        event.listen(built, "connect", _on_connect)
        _engines[path] = built
    return _engines[path]


class Sealed(Exception):
    """A write attempted while a restore has the library to itself."""


_seal = threading.Condition()
_sealed_by: list[int] = []
_writing = [0]
_generation = [0]  # moves on when a restore ends
_bound_to: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "library_generation", default=None
)


def bind_to_this_library() -> None:
    """For a long-lived task. Anything it started, however late it finishes, may write
    only to the library it began on. A thread inherits this; a cancel can't stop one."""
    _bound_to.set(_generation[0])


@contextmanager
def exclusive(wait_seconds: float) -> Iterator[None]:
    """Only this thread may write until it's done. Waits for writes under way, including
    those in threads a cancelled task left running, and refuses any that start after."""
    with _seal:
        _sealed_by.append(threading.get_ident())
        if not _seal.wait_for(lambda: _writing[0] == 0, wait_seconds):
            _sealed_by.clear()
            raise Sealed
    try:
        yield
    finally:
        with _seal:
            _sealed_by.clear()
            _generation[0] += 1


@contextmanager
def _counted() -> Iterator[None]:
    with _seal:
        if _sealed_by and _sealed_by[0] != threading.get_ident():
            raise Sealed
        bound = _bound_to.get()
        if bound is not None and bound != _generation[0]:
            raise Sealed
        _writing[0] += 1
    try:
        yield
    finally:
        with _seal:
            _writing[0] -= 1
            _seal.notify_all()


def dispose() -> None:
    """Lets go of open connections, after the file under them was replaced."""
    for built in _engines.values():
        built.dispose()


@contextmanager
def write() -> Iterator[Connection]:
    """A transaction that holds the write lock from its start."""
    with _counted(), engine().connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        connection.exec_driver_sql("COMMIT")


@contextmanager
def read() -> Iterator[Connection]:
    with engine().connect() as connection:
        yield connection
