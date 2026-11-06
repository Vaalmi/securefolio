"""Documents and their locations: every move a document makes, as a conditional
update. The status says what the library holds; work on a document with an active
revision is a job, and the status stays indexed."""

import json
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import (
    database,
    index,
    jobs,
    objects,
    originals,
    refusals,
    search_row,
    sensitivity,
    settings,
    states,
)
from backend.database import document_locations, documents
from backend.index import OP_DELETE

KINDS = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".pptx": "PPTX",
    ".xlsx": "XLSX",
    ".csv": "CSV",
    ".md": "MD",
    ".markdown": "MD",
    ".txt": "TXT",
    ".html": "HTML",
    ".htm": "HTML",
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".heic": "HEIC",
    ".webp": "WEBP",
    ".eml": "EML",
}
FIRST_INDEX = (states.QUEUED, states.INDEXING)


def kind_of(path: Path) -> str | None:
    return KINDS.get(path.suffix.lower())


def title_of(path: Path) -> str:
    return path.stem.replace("_", " ")


def create_in(connection: Connection, path: Path, status: str, **fields: Any) -> str:
    document_id = uuid.uuid4().hex
    values = {
        "sensitivity": states.STANDARD,
        "file_kind": kind_of(path) or path.suffix.lstrip(".").upper() or "File",
        **fields,
    }
    connection.execute(
        documents.insert().values(
            id=document_id,
            title=title_of(path),
            file_name=path.name,
            status=status,
            added_at=time.time(),
            **values,
        )
    )
    return document_id


def add_location_in(
    connection: Connection,
    document_id: str,
    source_id: str,
    seen: originals.Seen,
    storage: tuple[str, str],
) -> None:
    """Adds a place this document lives, with (storage mode, its sensitivity)."""
    mode, level = storage
    connection.execute(
        document_locations.insert().values(
            id=uuid.uuid4().hex,
            document_id=document_id,
            source_id=source_id,
            path=str(seen.path),
            inode=seen.inode,
            size_bytes=seen.size,
            mtime=seen.mtime,
            storage_mode=mode,
            sensitivity=level,
            dataless=seen.dataless,
        )
    )
    search_row.refresh_in(connection, document_id)


def get(document_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(documents).where(documents.c.id == document_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise refusals.not_found("There's no such document.")
    return dict(row)


def locations(document_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(document_locations).where(
                    document_locations.c.document_id == document_id
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _move(
    connection: Connection, document_id: str, sources: tuple[str, ...], **values: Any
) -> bool:
    moved = connection.execute(
        documents.update()
        .where(documents.c.id == document_id, documents.c.status.in_(sources))
        .values(**values)
    )
    return moved.rowcount == 1


def claim_ingest(document_id: str) -> bool:
    """Queued or failed to indexing, for a first index. False for a re-index."""
    with database.write() as connection:
        return _move(
            connection,
            document_id,
            (states.QUEUED, states.FAILED),
            status=states.INDEXING,
        )


def requeue_after_lease_in(connection: Connection, job: dict[str, Any]) -> None:
    """A first index whose worker died goes back to the queue, or fails on its third
    attempt with the job."""
    if job["kind"] != jobs.INGEST or job["document_id"] is None:
        return
    if job["state"] == states.FAILED:
        _move(
            connection,
            job["document_id"],
            FIRST_INDEX,
            status=states.FAILED,
            status_reason=job["reason"],
            error_code=job["error_code"],
        )
    else:
        _move(connection, job["document_id"], (states.INDEXING,), status=states.QUEUED)


def mark_needs_password(document_id: str, reason: str) -> None:
    with database.write() as connection:
        _move(
            connection,
            document_id,
            (states.INDEXING,),
            status=states.SKIPPED_PASSWORD,
            status_reason=reason,
            error_code="password",
        )


def fail_ingest(document_id: str, reason: str, code: str) -> None:
    with database.write() as connection:
        _move(
            connection,
            document_id,
            (states.INDEXING,),
            status=states.FAILED,
            status_reason=reason,
            error_code=code,
        )


def mark_unsupported(document_id: str, reason: str) -> None:
    with database.write() as connection:
        _move(
            connection,
            document_id,
            (states.INDEXING,),
            status=states.SKIPPED_UNSUPPORTED,
            status_reason=reason,
        )


def mark_evicted_before_reading(document_id: str) -> None:
    with database.write() as connection:
        _move(
            connection,
            document_id,
            (states.QUEUED, states.INDEXING),
            status=states.NOT_DOWNLOADED,
            status_reason="This file isn't on this Mac yet.",
        )


def mark_cancelled(document_id: str) -> None:
    with database.write() as connection:
        _move(connection, document_id, FIRST_INDEX, status=states.CANCELLED)


def cancel_job(job_id: str) -> None:
    """The tray's Cancel. A queued job stops now; a running one within one stage."""
    with database.write() as connection:
        job = (
            connection.execute(
                sa.select(database.jobs).where(database.jobs.c.id == job_id)
            )
            .mappings()
            .first()
        )
        if job is None:
            raise refusals.not_found("There's no such job.")
        left_in = jobs.request_cancel_in(connection, job_id)
        if left_in == states.CANCELLED and job["document_id"]:
            _move(connection, job["document_id"], FIRST_INDEX, status=states.CANCELLED)


def retry_job(job_id: str) -> None:
    with database.write() as connection:
        job = (
            connection.execute(
                sa.select(database.jobs).where(database.jobs.c.id == job_id)
            )
            .mappings()
            .first()
        )
        if job is None:
            raise refusals.not_found("There's no such job.")
        jobs.retry_in(connection, job_id)
        if job["document_id"]:
            _move(
                connection,
                job["document_id"],
                (states.FAILED, states.CANCELLED),
                status=states.QUEUED,
                status_reason=None,
                error_code=None,
            )


def read_content(document: dict[str, Any]) -> bytes:
    """The content, from a managed copy if there is one, never touching a location
    that isn't on this Mac."""
    rows = locations(document["id"])
    managed = [r for r in rows if r["storage_mode"] == states.MANAGED]
    for row in [*managed, *rows]:
        path = Path(row["path"])
        seen = originals.look(path)
        if seen is not None and not seen.dataless:
            return originals.read_bytes(path)
    raise FileNotFoundError(document["file_name"])


def _source_of(connection: Connection, document_id: str) -> str | None:
    return connection.execute(
        sa.select(document_locations.c.source_id).where(
            document_locations.c.document_id == document_id
        )
    ).scalar()


def _queue_ingest_in(
    connection: Connection,
    document_id: str,
    priority: int,
    payload: dict[str, Any] | None = None,
) -> str:
    return jobs.enqueue_in(
        connection,
        jobs.INGEST,
        priority,
        document_id=document_id,
        source_id=_source_of(connection, document_id),
        key=f"ingest:{document_id}",
        payload=payload,
    )


def queue_ingest_in(connection: Connection, document_id: str, priority: int) -> str:
    return _queue_ingest_in(connection, document_id, priority)


def requeue_in(connection: Connection, document_id: str, priority: int) -> str | None:
    """A changed file on a failed, cancelled or password-skipped document."""
    if _move(
        connection,
        document_id,
        (states.FAILED, states.CANCELLED, states.SKIPPED_PASSWORD),
        status=states.QUEUED,
        status_reason=None,
        error_code=None,
    ):
        return _queue_ingest_in(connection, document_id, priority)
    return None


def reindex_in(connection: Connection, document_id: str, priority: int) -> str:
    state = connection.execute(
        sa.select(documents.c.status).where(documents.c.id == document_id)
    ).scalar()
    if state != states.INDEXED:
        raise refusals.conflict("Only an indexed document can be indexed again.")
    return _queue_ingest_in(connection, document_id, priority, {"reindex": True})


def reindex(document_id: str) -> str:
    with database.write() as connection:
        return reindex_in(connection, document_id, jobs.PRIORITY_CHANGE)


def unlock_and_index(document_id: str) -> str:
    """Queues the job the password will be sent for. A first index moves back to
    queued; a skipped re-index stays indexed."""
    with database.write() as connection:
        status = connection.execute(
            sa.select(documents.c.status).where(documents.c.id == document_id)
        ).scalar()
        payload = {"unlock": True}
        if status == states.SKIPPED_PASSWORD:
            _move(
                connection,
                document_id,
                (states.SKIPPED_PASSWORD,),
                status=states.QUEUED,
                status_reason=None,
                error_code=None,
            )
        elif status == states.INDEXED and _last_reindex_skipped(
            connection, document_id
        ):
            payload = {"unlock": True, "reindex": True}
        else:
            raise refusals.conflict("This document isn't waiting for a password.")
        return _queue_ingest_in(connection, document_id, jobs.PRIORITY_DROP, payload)


def _last_reindex_skipped(connection: Connection, document_id: str) -> bool:
    table = database.jobs
    row = connection.execute(
        sa.select(table.c.state, table.c.error_code)
        .where(table.c.document_id == document_id, table.c.kind == jobs.INGEST)
        .order_by(table.c.created_at.desc())
        .limit(1)
    ).first()
    return (
        row is not None and row.state == states.SKIPPED and row.error_code == "password"
    )


def download_and_index(document_id: str) -> str:
    """The owner's choice to fetch a file that isn't on this Mac. It stays indexed if
    it was, and its job reads the file once it arrives."""
    if settings.get(settings.OFFLINE):
        raise refusals.conflict("Offline mode is on, so this can't be downloaded now.")
    with database.write() as connection:
        doc = (
            connection.execute(
                sa.select(documents).where(documents.c.id == document_id)
            )
            .mappings()
            .first()
        )
        if doc is None:
            raise refusals.not_found("There's no such document.")
        dataless = connection.execute(
            sa.select(document_locations.c.id).where(
                document_locations.c.document_id == document_id,
                document_locations.c.dataless.is_(True),
            )
        ).first()
        payload = {"download": True}
        if doc["status"] == states.NOT_DOWNLOADED:
            _move(
                connection, document_id, (states.NOT_DOWNLOADED,), status=states.QUEUED
            )
        elif doc["status"] == states.INDEXED and dataless:
            payload = {"download": True, "reindex": True}
        else:
            raise refusals.conflict("This file is already on this Mac.")
        return _queue_ingest_in(connection, document_id, jobs.PRIORITY_DROP, payload)


def mark_missing_in(connection: Connection, document_id: str) -> None:
    """When every location is gone. Notes and threads stay; nothing is purged."""
    left = connection.execute(
        sa.select(document_locations.c.id).where(
            document_locations.c.document_id == document_id,
            document_locations.c.missing.is_(False),
        )
    ).first()
    if left is None:
        _move(
            connection,
            document_id,
            (
                states.INDEXED,
                states.FAILED,
                states.CANCELLED,
                states.NOT_DOWNLOADED,
                states.SKIPPED_PASSWORD,
                states.SKIPPED_UNSUPPORTED,
                states.QUEUED,
            ),
            status=states.MISSING,
        )


def found_again_in(connection: Connection, document_id: str) -> None:
    """A missing document with a location again: indexed if it has a revision, and
    otherwise what a new file at that path would be."""
    row = connection.execute(
        sa.select(documents.c.active_revision_id, documents.c.status).where(
            documents.c.id == document_id
        )
    ).first()
    if row is None or row.status != states.MISSING:
        return
    if row.active_revision_id:
        _move(connection, document_id, (states.MISSING,), status=states.INDEXED)
        return
    here = connection.execute(
        sa.select(document_locations.c.path, document_locations.c.dataless).where(
            document_locations.c.document_id == document_id,
            document_locations.c.missing.is_(False),
        )
    ).first()
    if here is None:
        return
    if here.dataless:
        _move(connection, document_id, (states.MISSING,), status=states.NOT_DOWNLOADED)
    elif kind_of(Path(here.path)) is None:
        _move(
            connection,
            document_id,
            (states.MISSING,),
            status=states.SKIPPED_UNSUPPORTED,
        )
    elif _move(connection, document_id, (states.MISSING,), status=states.QUEUED):
        _queue_ingest_in(connection, document_id, jobs.PRIORITY_CHANGE)


def relocate(document_id: str, new_path: str, confirmed_new_version: bool) -> None:
    """The owner points at a moved file. The hash must match, unless they confirm
    it's a new version."""
    path = Path(new_path)
    seen = originals.look(path)
    if seen is None:
        raise refusals.invalid("There's no file at that path.")
    doc = get(document_id)
    if doc["status"] != states.MISSING:
        raise refusals.conflict("This document isn't missing.")
    sha256 = None if seen.dataless else originals.hash_file(path)
    known_hash = doc["content_sha256"]
    changed = known_hash is not None and sha256 != known_hash
    if changed and not confirmed_new_version:
        raise refusals.invalid("That file's content is different from the one indexed.")
    with database.write() as connection:
        connection.execute(
            document_locations.update()
            .where(document_locations.c.document_id == document_id)
            .values(
                path=str(path),
                inode=seen.inode,
                size_bytes=seen.size,
                mtime=seen.mtime,
                missing=False,
                dataless=seen.dataless,
            )
        )
        search_row.refresh_in(connection, document_id)
        if changed:
            connection.execute(
                documents.update()
                .where(documents.c.id == document_id)
                .values(content_sha256=sha256)
            )
        found_again_in(connection, document_id)
        if changed and doc["active_revision_id"]:
            reindex_in(connection, document_id, jobs.PRIORITY_CHANGE)


def remove_from_library(document_ids: list[str]) -> None:
    """Never touches a file. Vectors go; notes and threads stay. A scan won't add it
    back unless the owner does."""
    with database.write() as connection:
        for document_id in document_ids:
            connection.execute(
                documents.update()
                .where(
                    documents.c.id == document_id, documents.c.status != states.REMOVED
                )
                .values(status=states.REMOVED, active_revision_id=None)
            )
            connection.execute(
                database.jobs.update()
                .where(
                    database.jobs.c.document_id == document_id,
                    database.jobs.c.state == states.QUEUED,
                )
                .values(state=states.CANCELLED)
            )
            connection.exec_driver_sql(
                "DELETE FROM documents_fts WHERE document_id = ?", (document_id,)
            )
            connection.execute(
                index.index_ops.insert().values(
                    kind=OP_DELETE, document_id=document_id, created_at=time.time()
                )
            )
    index.replay_ops()


PAGE_REVISION_CHANGED = (
    "This document changed since its page was shown. Look at the page again, then send "
    "it."
)
PAGE_OLDER_WAITING = (
    "A read of this page from before the document changed is still waiting. Send it "
    "again in a moment."
)
PAGE_ALREADY_WAITING = (
    "A read of this page is already waiting, at another model or price. It goes as it "
    "was sent."
)


def queue_page_reading(
    document_id: str,
    reading: str,
    page: int,
    shown: dict[str, Any] | None = None,
    revision_id: str | None = None,
) -> str:
    """One page, read again on this Mac or in the cloud, in the hard-page path. A cloud
    read carries the model and price its cost was shown at, and is refused if the
    document's revision isn't the one shown (revision_id), or if a read of this page
    already waits at another model or price."""
    with database.write() as connection:
        document = connection.execute(
            sa.select(
                documents.c.status, documents.c.pages, documents.c.active_revision_id
            ).where(documents.c.id == document_id)
        ).first()
        if document is None:
            raise refusals.not_found("There's no such document.")
        if document.status != states.INDEXED:
            raise refusals.conflict("This document isn't indexed yet.")
        if page < 1 or (document.pages is not None and page > document.pages):
            raise refusals.invalid("This document has no such page.")
        if revision_id is not None and revision_id != document.active_revision_id:
            raise refusals.conflict(PAGE_REVISION_CHANGED)
        key = f"page:{document_id}:{reading}:{page}"
        if shown:
            waiting = jobs.live_payload(connection, key)
            # As stored: a price is kept as a list.
            sent = json.loads(json.dumps(shown))
            if waiting is not None and waiting.get("revision_id") != (
                document.active_revision_id
            ):
                # Waiting for the revision before this one, which it won't be sent for.
                raise refusals.conflict(PAGE_OLDER_WAITING)
            if waiting is not None and any(
                waiting.get(field) != value for field, value in sent.items()
            ):
                raise refusals.conflict(PAGE_ALREADY_WAITING)
        return jobs.enqueue_in(
            connection,
            jobs.HARD_PAGES,
            jobs.PRIORITY_DROP,
            document_id=document_id,
            key=key,
            payload={
                reading: page,
                "revision_id": document.active_revision_id,
                **(shown or {}),
            },
        )


def trash_managed_copy(document_id: str) -> None:
    """Applies only to a copy SecureFolio made. Removes that location, and the document
    with it when no other location is left."""
    with database.write() as connection:
        row = (
            connection.execute(
                sa.select(document_locations).where(
                    document_locations.c.document_id == document_id,
                    document_locations.c.storage_mode == states.MANAGED,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise refusals.conflict("SecureFolio holds no copy of this document.")
        sha256 = connection.execute(
            sa.select(documents.c.content_sha256).where(documents.c.id == document_id)
        ).scalar()
        connection.execute(
            document_locations.delete().where(document_locations.c.id == row["id"])
        )
        others = connection.execute(
            sa.select(document_locations.c.id).where(
                document_locations.c.document_id == document_id
            )
        ).first()
    if sha256:
        objects.move_to_trash(sha256)
    if others is None:
        remove_from_library([document_id])


def set_starred(document_id: str, starred: bool) -> None:
    with database.write() as connection:
        connection.execute(
            documents.update()
            .where(documents.c.id == document_id)
            .values(starred=starred)
        )


def mark_opened(document_id: str) -> None:
    with database.write() as connection:
        connection.execute(
            documents.update()
            .where(documents.c.id == document_id)
            .values(last_opened_at=time.time())
        )


def set_content_hash_in(connection: Connection, document_id: str, sha256: str) -> None:
    connection.execute(
        documents.update()
        .where(documents.c.id == document_id)
        .values(content_sha256=sha256)
    )


def by_hash_in(connection: Connection, sha256: str) -> dict[str, Any] | None:
    row = (
        connection.execute(
            sa.select(documents).where(documents.c.content_sha256 == sha256)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def import_paths(paths: list[str], level: str) -> list[dict[str, str]]:
    """Files or folders dropped on the app: each file is copied in and gets its own
    job, except a file that isn't on this Mac, which is listed and read by nothing."""
    results = []
    for given in paths:
        path = Path(given)
        files = originals.walk(path) if path.is_dir() else [path]
        for file in files:
            results.append(_import_file(file, level))
    return results


def _import_file(path: Path, level: str) -> dict[str, str]:
    seen = originals.look(path)
    if seen is None:
        raise refusals.invalid(f"Can't read {path.name}.")
    if seen.dataless:
        return _import_placeholder(seen, level)
    if kind_of(path) is None:
        return _import_unsupported(seen, level)
    sha256, copy = objects.copy_in(path)
    stored = originals.Seen(copy, seen.inode, seen.size, seen.mtime, False)
    return _record_import(seen, stored, sha256, level)


def _import_placeholder(seen: originals.Seen, level: str) -> dict[str, str]:
    with database.write() as connection:
        document_id = create_in(
            connection,
            seen.path,
            states.NOT_DOWNLOADED,
            status_reason="This file isn't on this Mac yet.",
            sensitivity=level,
        )
        add_location_in(
            connection,
            document_id,
            states.LIBRARY_SOURCE_ID,
            seen,
            (states.REFERENCED, level),
        )
    return {"document_id": document_id, "job_id": ""}


def _import_unsupported(seen: originals.Seen, level: str) -> dict[str, str]:
    with database.write() as connection:
        document_id = create_in(
            connection,
            seen.path,
            states.SKIPPED_UNSUPPORTED,
            status_reason="SecureFolio doesn't read this type of file.",
            sensitivity=level,
        )
        add_location_in(
            connection,
            document_id,
            states.LIBRARY_SOURCE_ID,
            seen,
            (states.REFERENCED, level),
        )
    return {"document_id": document_id, "job_id": ""}


def _record_import(
    seen: originals.Seen, stored: originals.Seen, sha256: str, level: str
) -> dict[str, str]:
    with database.write() as connection:
        known = by_hash_in(connection, sha256)
        if known and known["status"] != states.REMOVED:
            document_id = known["id"]
            job_id = ""
        else:
            document_id = known["id"] if known else None
            if document_id:
                _move(connection, document_id, (states.REMOVED,), status=states.QUEUED)
            else:
                document_id = create_in(
                    connection,
                    seen.path,
                    states.QUEUED,
                    content_sha256=sha256,
                    size_bytes=seen.size,
                    sensitivity=level,
                )
            job_id = queue_ingest_in(connection, document_id, jobs.PRIORITY_DROP)
        _add_managed_location_in(connection, document_id, stored, level)
        sensitivity.apply_in(connection, document_id)
    sensitivity.follow_in_index()
    return {"document_id": document_id, "job_id": job_id}


def _add_managed_location_in(
    connection: Connection, document_id: str, stored: originals.Seen, level: str
) -> None:
    held = (
        connection.execute(
            sa.select(document_locations).where(
                document_locations.c.document_id == document_id,
                document_locations.c.source_id == states.LIBRARY_SOURCE_ID,
                document_locations.c.path == str(stored.path),
            )
        )
        .mappings()
        .first()
    )
    if held is None:
        add_location_in(
            connection,
            document_id,
            states.LIBRARY_SOURCE_ID,
            stored,
            (states.MANAGED, level),
        )
    elif level == states.PRIVATE:
        connection.execute(
            document_locations.update()
            .where(document_locations.c.id == held["id"])
            .values(sensitivity=states.PRIVATE)
        )
