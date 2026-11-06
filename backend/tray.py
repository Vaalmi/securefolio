"""The import tray's rows and counts, read from jobs and documents. Skipped also lists
documents a scan marked with no job."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, states
from backend.database import document_locations, documents, jobs, sources

WORK = ("ingest", "hard_pages")
LIVE = (states.QUEUED, states.RUNNING)
SKIPPED_DOCUMENTS = (
    states.SKIPPED_PASSWORD,
    states.SKIPPED_UNSUPPORTED,
    states.NOT_DOWNLOADED,
)
SKIP_KINDS = {
    states.SKIPPED_PASSWORD: "password",
    states.NOT_DOWNLOADED: "cloud",
    states.SKIPPED_UNSUPPORTED: "unsupported",
}
PASSWORD_CODE = "password"  # noqa: S105
LIMIT = 100
REFUSED_CODE = "refused"
QUEUED_DETAIL = "Queued"
HARD_PAGES_DETAIL = "Reading hard pages on this Mac"
READING_DETAIL = "Reading on this Mac"
DONE_DETAIL = "Indexed"
_NEWER = jobs.alias("newer")


def _folder() -> Any:
    return (
        sa.select(sources.c.name)
        .join(document_locations, document_locations.c.source_id == sources.c.id)
        .where(document_locations.c.document_id == documents.c.id)
        .limit(1)
        .scalar_subquery()
        .label("folder")
    )


def _no_newer_work() -> Any:
    """A finished job counts until a later one for its document supersedes it."""
    return ~sa.exists().where(
        _NEWER.c.document_id == jobs.c.document_id,
        _NEWER.c.kind.in_(WORK),
        _NEWER.c.created_at > jobs.c.created_at,
    )


def _no_newer_claude_request() -> Any:
    """The owner asked for this one, so automatic work doesn't clear its refusal. Asking
    again does."""
    return ~sa.exists().where(
        _NEWER.c.document_id == jobs.c.document_id,
        _NEWER.c.kind == "hard_pages",
        _NEWER.c.created_at > jobs.c.created_at,
        sa.func.json_extract(_NEWER.c.payload, "$.claude_page").is_not(None),
    )


def _live() -> list[Any]:
    return [jobs.c.kind.in_(WORK), jobs.c.state.in_(LIVE)]


def _failed() -> list[Any]:
    return [jobs.c.kind.in_(WORK), jobs.c.state == states.FAILED, _no_newer_work()]


def _completed() -> list[Any]:
    return [
        jobs.c.kind == "ingest",
        jobs.c.state == states.SUCCEEDED,
        documents.c.status == states.INDEXED,
        _no_newer_work(),
    ]


def _skipped_jobs() -> list[Any]:
    """A skip on a document that's still indexed: a re-index that needs its password,
    or a page Claude was refused. The document's status can't say so."""
    return [
        jobs.c.kind.in_(WORK),
        jobs.c.state == states.SKIPPED,
        documents.c.status.not_in(SKIPPED_DOCUMENTS),
        sa.or_(
            sa.and_(jobs.c.error_code != REFUSED_CODE, _no_newer_work()),
            sa.and_(jobs.c.error_code == REFUSED_CODE, _no_newer_claude_request()),
        ),
    ]


def _job_rows(
    connection: Connection, conditions: list[Any], order: tuple[Any, ...], limit: int
) -> list[Any]:
    return list(
        connection.execute(
            sa.select(
                jobs,
                documents.c.file_name,
                documents.c.status.label("document_status"),
                _folder(),
            )
            .join(documents, documents.c.id == jobs.c.document_id)
            .where(*conditions)
            .order_by(*order)
            .limit(limit)
        ).mappings()
    )


def _job_count(connection: Connection, conditions: list[Any]) -> int:
    return connection.execute(
        sa.select(sa.func.count())
        .select_from(jobs.join(documents, documents.c.id == jobs.c.document_id))
        .where(*conditions)
    ).scalar_one()


def _row(
    row: Any, status: str, detail: str, job_id: str | None, skip_kind: str | None
) -> dict[str, Any]:
    return {
        "id": job_id or row["document_id"],
        "job_id": job_id,
        "document_id": row["document_id"],
        "file": row["file_name"],
        "folder": row["folder"] or "",
        "status": status,
        "detail": detail,
        "skip_kind": skip_kind,
    }


def _active_row(row: Any) -> dict[str, Any]:
    if row["state"] != states.RUNNING:
        return _row(row, "Queued", QUEUED_DETAIL, row["id"], None)
    detail = HARD_PAGES_DETAIL if row["kind"] == "hard_pages" else READING_DETAIL
    return _row(row, "Indexing", detail, row["id"], None)


def _skipped_documents(connection: Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(
            documents.c.id.label("document_id"),
            documents.c.file_name,
            documents.c.status.label("document_status"),
            documents.c.status_reason,
            _folder(),
        )
        .where(documents.c.status.in_(SKIPPED_DOCUMENTS))
        .order_by(documents.c.added_at.desc(), documents.c.id)
        .limit(limit)
    ).mappings()
    return [
        _row(
            row,
            "Skipped",
            row["status_reason"] or "This wasn't indexed.",
            None,
            SKIP_KINDS[row["document_status"]],
        )
        for row in rows
    ]


def _skipped_job_row(row: Any) -> dict[str, Any]:
    kind = PASSWORD_CODE if row["error_code"] == PASSWORD_CODE else None
    reason = row["reason"] or "This wasn't indexed."
    return _row(row, "Skipped", reason, row["id"], kind)


def rows(tab: str, limit: int = LIMIT) -> list[dict[str, Any]]:
    latest = (jobs.c.updated_at.desc(), jobs.c.id)
    # Live rows keep their order, so a heartbeat can't move one under the pointer.
    oldest = (jobs.c.created_at, jobs.c.id)
    found: list[dict[str, Any]] = []
    with database.read() as connection:
        if tab in ("all", "active"):
            found += [
                _active_row(r) for r in _job_rows(connection, _live(), oldest, limit)
            ]
        if tab in ("all", "failed"):
            found += [
                _row(
                    r, "Failed", r["reason"] or "This couldn't be read.", r["id"], None
                )
                for r in _job_rows(connection, _failed(), latest, limit)
            ]
        if tab in ("all", "skipped"):
            found += _skipped_documents(connection, limit)
            found += [
                _skipped_job_row(r)
                for r in _job_rows(connection, _skipped_jobs(), latest, limit)
            ]
        if tab in ("all", "completed"):
            found += [
                _row(r, "Completed", DONE_DETAIL, r["id"], None)
                for r in _job_rows(connection, _completed(), latest, limit)
            ]
    return found


def counts() -> dict[str, Any]:
    """The indexing line, each tab's whole count, and when any job last moved, so a
    row whose reason changed is reloaded even if no count did. The run in progress
    began when the oldest live job was queued, so "of N" counts what's since done."""
    with database.read() as connection:
        live = connection.execute(
            sa.select(sa.func.count(), sa.func.min(jobs.c.created_at)).where(
                jobs.c.kind == "ingest", jobs.c.state.in_(LIVE)
            )
        ).one()
        done = 0
        if live[0]:
            done = connection.execute(
                sa.select(sa.func.count()).where(
                    jobs.c.kind == "ingest",
                    jobs.c.state == states.SUCCEEDED,
                    jobs.c.created_at >= live[1],
                )
            ).scalar_one()
        skipped_documents = connection.execute(
            sa.select(sa.func.count())
            .select_from(documents)
            .where(documents.c.status.in_(SKIPPED_DOCUMENTS))
        ).scalar_one()
        return {
            "done": done,
            "total": done + live[0],
            "active": _job_count(connection, _live()),
            "completed": _job_count(connection, _completed()),
            "failed": _job_count(connection, _failed()),
            "skipped": skipped_documents + _job_count(connection, _skipped_jobs()),
            "latest": connection.execute(
                sa.select(sa.func.max(jobs.c.updated_at))
            ).scalar()
            or 0.0,
        }
