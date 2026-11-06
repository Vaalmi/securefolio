"""Hashes content and records each location. A path counts as a move only when the
old one is gone; the strictest sensitivity of a document's locations wins at once."""

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, documents, jobs, originals, sensitivity, states
from backend.database import document_locations

UNSUPPORTED = "SecureFolio doesn't read this type of file."
NOT_HERE = "This file isn't on this Mac yet."


class FolderGone(Exception):
    """The folder stopped being watched during this scan."""


def _current_level(connection: Connection, source_id: str) -> str:
    """The folder's sensitivity now, inside the transaction that writes the location,
    so a Private move during a scan can't be undone by rows it hadn't reached."""
    row = connection.execute(
        sa.select(database.sources.c.sensitivity, database.sources.c.status).where(
            database.sources.c.id == source_id
        )
    ).first()
    if row is None or row.status == states.REMOVED:
        raise FolderGone
    return row.sensitivity


def _located(
    connection: Connection, source_id: str, path: str
) -> dict[str, Any] | None:
    row = (
        connection.execute(
            sa.select(document_locations).where(
                document_locations.c.source_id == source_id,
                document_locations.c.path == path,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _unchanged(row: dict[str, Any], seen: originals.Seen) -> bool:
    return (
        row["inode"] == seen.inode
        and row["size_bytes"] == seen.size
        and row["mtime"] == seen.mtime
        and not row["missing"]
        and bool(row["dataless"]) == seen.dataless
    )


def observe(
    seen: originals.Seen, source: dict[str, Any], new_folder: bool = False
) -> None:
    """A file a scan or event found. Reads it only when its stat says it must."""
    with database.read() as connection:
        row = _located(connection, source["id"], str(seen.path))
    if row and _unchanged(row, seen):
        return
    if row:
        _observe_known(row, seen)
    elif seen.dataless:
        _record_placeholder(seen, source)
    elif documents.kind_of(seen.path) is None:
        _record_unsupported(seen, source)
    else:
        _observe_new(seen, source, new_folder)
    sensitivity.follow_in_index()


def _update_location(
    connection: Connection, row: dict[str, Any], seen: originals.Seen
) -> None:
    connection.execute(
        document_locations.update()
        .where(document_locations.c.id == row["id"])
        .values(
            inode=seen.inode,
            size_bytes=seen.size,
            mtime=seen.mtime,
            missing=False,
            dataless=seen.dataless,
        )
    )


def _observe_known(row: dict[str, Any], seen: originals.Seen) -> None:
    document = documents.get(row["document_id"])
    if seen.dataless:
        with database.write() as connection:
            _update_location(connection, row, seen)
            documents.found_again_in(connection, document["id"])
        return
    if document["status"] == states.NOT_DOWNLOADED:
        _downloaded_placeholder(row, seen, document)
        return
    sha256 = originals.hash_file(seen.path)
    with database.write() as connection:
        _update_location(connection, row, seen)
        documents.found_again_in(connection, document["id"])
        if sha256 != document["content_sha256"]:
            _content_changed(connection, row, seen, document, sha256)


def _downloaded_placeholder(
    row: dict[str, Any], seen: originals.Seen, document: dict[str, Any]
) -> None:
    """The file arrived on its own: it becomes what a new file would be."""
    supported = documents.kind_of(seen.path) is not None
    sha256 = originals.hash_file(seen.path) if supported else None
    with database.write() as connection:
        _update_location(connection, row, seen)
        status = states.QUEUED if supported else states.SKIPPED_UNSUPPORTED
        connection.execute(
            database.documents.update()
            .where(database.documents.c.id == document["id"])
            .values(
                status=status,
                content_sha256=sha256,
                status_reason=None if supported else UNSUPPORTED,
            )
        )
        if supported:
            documents.queue_ingest_in(connection, document["id"], jobs.PRIORITY_CHANGE)


def _content_changed(
    connection: Connection,
    row: dict[str, Any],
    seen: originals.Seen,
    document: dict[str, Any],
    sha256: str,
) -> None:
    others = connection.execute(
        sa.select(document_locations.c.id).where(
            document_locations.c.document_id == document["id"],
            document_locations.c.id != row["id"],
            document_locations.c.missing.is_(False),
        )
    ).first()
    if others:
        # Another location still holds the old content: this path is a new document.
        connection.execute(
            document_locations.delete().where(document_locations.c.id == row["id"])
        )
        level = _current_level(connection, row["source_id"])
        _record_content(connection, seen, row["source_id"], level, sha256)
        return
    known = documents.by_hash_in(connection, sha256)
    if known and known["id"] != document["id"]:
        # These bytes are already a document, which may be Private: this path joins it,
        # by the same rule as any new location, and the old content has no copy left.
        connection.execute(
            document_locations.delete().where(document_locations.c.id == row["id"])
        )
        level = _current_level(connection, row["source_id"])
        _record_content(connection, seen, row["source_id"], level, sha256)
        documents.mark_missing_in(connection, document["id"])
        return
    documents.set_content_hash_in(connection, document["id"], sha256)
    if document["status"] == states.INDEXED:
        documents.reindex_in(connection, document["id"], jobs.PRIORITY_CHANGE)
    else:
        documents.requeue_in(connection, document["id"], jobs.PRIORITY_CHANGE)


def _record_content(
    connection: Connection,
    seen: originals.Seen,
    source_id: str,
    level: str,
    sha256: str,
) -> None:
    known = documents.by_hash_in(connection, sha256)
    if known:
        documents.add_location_in(
            connection, known["id"], source_id, seen, (states.REFERENCED, level)
        )
        sensitivity.apply_in(connection, known["id"])
        return
    document_id = documents.create_in(
        connection,
        seen.path,
        states.QUEUED,
        content_sha256=sha256,
        size_bytes=seen.size,
        sensitivity=level,
    )
    documents.add_location_in(
        connection, document_id, source_id, seen, (states.REFERENCED, level)
    )
    documents.queue_ingest_in(connection, document_id, jobs.PRIORITY_BULK)


def _record_placeholder(seen: originals.Seen, source: dict[str, Any]) -> None:
    with database.write() as connection:
        level = _current_level(connection, source["id"])
        document_id = documents.create_in(
            connection,
            seen.path,
            states.NOT_DOWNLOADED,
            status_reason=NOT_HERE,
            sensitivity=level,
        )
        documents.add_location_in(
            connection, document_id, source["id"], seen, (states.REFERENCED, level)
        )


def _record_unsupported(seen: originals.Seen, source: dict[str, Any]) -> None:
    with database.write() as connection:
        level = _current_level(connection, source["id"])
        document_id = documents.create_in(
            connection,
            seen.path,
            states.SKIPPED_UNSUPPORTED,
            status_reason=UNSUPPORTED,
            sensitivity=level,
        )
        documents.add_location_in(
            connection, document_id, source["id"], seen, (states.REFERENCED, level)
        )


def _gone_location(connection: Connection, document_id: str) -> dict[str, Any] | None:
    """A location of this document whose file no longer exists at its path."""
    rows = (
        connection.execute(
            sa.select(document_locations).where(
                document_locations.c.document_id == document_id
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        if row["storage_mode"] == states.REFERENCED and not Path(row["path"]).exists():
            return dict(row)
    return None


def _observe_new(
    seen: originals.Seen, source: dict[str, Any], new_folder: bool
) -> None:
    sha256 = originals.hash_file(seen.path)
    with database.write() as connection:
        level = _current_level(connection, source["id"])
        known = documents.by_hash_in(connection, sha256)
        if known and known["status"] == states.REMOVED and not new_folder:
            return  # the owner removed it; a scan doesn't add it back
        if known is None:
            _record_content(connection, seen, source["id"], level, sha256)
            return
        if known["status"] == states.REMOVED:
            connection.execute(
                database.documents.update()
                .where(database.documents.c.id == known["id"])
                .values(status=states.QUEUED)
            )
            documents.queue_ingest_in(connection, known["id"], jobs.PRIORITY_BULK)
        gone = _gone_location(connection, known["id"])
        if gone:
            _move_location(connection, gone, seen, source)
        else:
            documents.add_location_in(
                connection, known["id"], source["id"], seen, (states.REFERENCED, level)
            )
        documents.found_again_in(connection, known["id"])
        sensitivity.apply_in(connection, known["id"])


def _move_location(
    connection: Connection,
    row: dict[str, Any],
    seen: originals.Seen,
    source: dict[str, Any],
) -> None:
    connection.execute(
        document_locations.update()
        .where(document_locations.c.id == row["id"])
        .values(
            source_id=source["id"],
            path=str(seen.path),
            inode=seen.inode,
            size_bytes=seen.size,
            mtime=seen.mtime,
            sensitivity=_current_level(connection, source["id"]),
            missing=False,
        )
    )


def mark_gone(source_id: str, seen_paths: set[str]) -> None:
    """Locations under a folder a full scan didn't find."""
    with database.write() as connection:
        rows = (
            connection.execute(
                sa.select(document_locations).where(
                    document_locations.c.source_id == source_id,
                    document_locations.c.missing.is_(False),
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            if row["path"] not in seen_paths:
                connection.execute(
                    document_locations.update()
                    .where(document_locations.c.id == row["id"])
                    .values(missing=True)
                )
                documents.mark_missing_in(connection, row["document_id"])
