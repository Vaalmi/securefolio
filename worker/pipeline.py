"""The ingest job's stages: read, parse, embed, publish. Each is idempotent, and
cancel is checked between them. Every terminal outcome leaves the document, its
staging revision and any password where they belong."""

import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from backend import (
    database,
    documents,
    index,
    jobs,
    objects,
    originals,
    refusals,
    revisions,
    settings,
    states,
)
from backend.database import document_locations
from worker import embedding, parse, publishing, unlock
from worker.content import element_rows, estimate_tokens

logger = logging.getLogger(__name__)
STAGE_PARSE = "parse"
STAGE_EMBED = "embed"
STAGE_PUBLISH = "publish"
LOCKED_REASON = "This file is password-protected. Unlock it to index it."
WRONG_KEY_REASON = "That password didn't open this file. Try again."
LOST_KEY_REASON = "The password didn't reach the worker. Enter it again."
CHANGED_REASON = "This file changed while it was being read. Rescan to try again."
INTERNAL_REASON = "Something went wrong while reading this file."
MAX_BYTES = 2 * 1024**3
OFFLINE_CODE = "offline"


class Cancelled(Exception):
    """The owner cancelled this job between stages."""


class Skipped(Exception):
    def __init__(self, reason: str, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _next_stage(job_id: str, stage: str) -> None:
    if not jobs.advance_stage(job_id, stage):
        raise Cancelled


def _materialize(document: dict[str, Any]) -> None:
    """The owner chose to fetch this file. Asks for it, then records what's now on
    disk; a dropped file becomes a managed copy like any other. Offline is checked
    again here: the file provider's own process would otherwise reach the network."""
    if settings.get(settings.OFFLINE):
        raise Skipped("Offline mode is on, so this wasn't downloaded.", OFFLINE_CODE)
    for row in documents.locations(document["id"]):
        path = Path(row["path"])
        before = originals.look(path)
        if before is None or not before.dataless:
            continue
        originals.materialize(path)
        seen = originals.look(path)
        if seen is None or seen.dataless:
            raise FileNotFoundError(document["file_name"])
        _record_downloaded(row, seen, path)


def _record_downloaded(row: dict[str, Any], seen: originals.Seen, path: Path) -> None:
    values: dict[str, Any] = {
        "dataless": False,
        "inode": seen.inode,
        "size_bytes": seen.size,
        "mtime": seen.mtime,
    }
    if row["source_id"] == states.LIBRARY_SOURCE_ID:
        _, copy = objects.copy_in(path)
        values |= {"path": str(copy), "storage_mode": states.MANAGED}
    with database.write() as connection:
        connection.execute(
            document_locations.update()
            .where(document_locations.c.id == row["id"])
            .values(**values)
        )


def _unlocked(job: dict[str, Any], document: dict[str, Any], data: bytes) -> bytes:
    kind = document["file_kind"]
    if not unlock.needs_password(kind, data):
        return data
    if not job["payload"].get("unlock"):
        raise Skipped(LOCKED_REASON, "password")
    password = unlock.take_for_job(job["id"], document["id"])
    if password is None:
        raise Skipped(LOST_KEY_REASON, "password")
    try:
        return unlock.decrypt(kind, data, password)
    except unlock.WrongPassword as error:
        raise Skipped(WRONG_KEY_REASON, "password") from error


def _snapshots(document: dict[str, Any]) -> Iterator[tuple[bytes, str]]:
    """Each readable location's bytes and hash, managed copies first."""
    rows = documents.locations(document["id"])
    rows.sort(key=lambda row: row["storage_mode"] != states.MANAGED)
    for row in rows:
        path = Path(row["path"])
        seen = originals.look(path)
        if seen is None or seen.dataless:
            continue
        if seen.size > MAX_BYTES:
            raise parse.CantRead("This file is over the 2 GB limit.")
        data = originals.read_bytes(path)
        yield data, hashlib.sha256(data).hexdigest()


def _verified_snapshot(document: dict[str, Any]) -> tuple[bytes, str]:
    """The bytes every later stage works from, with their own hash. Any location still
    holding the document's content will do. If the only location changed, the hash
    follows. If several did, or another document holds that content, identity is the
    scan's to sort out, so this stops and asks for one."""
    known = document["content_sha256"]
    found = list(_snapshots(document))
    if not found:
        raise FileNotFoundError(document["file_name"])
    for data, sha256 in found:
        if sha256 == known:
            return data, sha256
    data, sha256 = found[0]
    with database.write() as connection:
        holder = documents.by_hash_in(connection, sha256)
        if len(documents.locations(document["id"])) > 1 or (
            holder and holder["id"] != document["id"]
        ):
            _ask_for_a_scan(connection, document["id"])
            raise parse.CantRead(CHANGED_REASON)
        documents.set_content_hash_in(connection, document["id"], sha256)
    return data, sha256


def _ask_for_a_scan(connection: Any, document_id: str) -> None:
    rows = connection.execute(
        document_locations.select().where(
            document_locations.c.document_id == document_id
        )
    ).mappings()
    for row in rows:
        if row["source_id"] != states.LIBRARY_SOURCE_ID:
            jobs.enqueue_in(
                connection,
                jobs.SCAN,
                jobs.PRIORITY_CHANGE,
                source_id=row["source_id"],
                payload={"paths": [row["path"]]},
            )


def _chunk_rows(
    revision_id: str, ids: list[str], pieces: list[Any]
) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{revision_id[:16]}-{ordinal}",
            "ordinal": ordinal,
            "element_ids": [ids[o] for o in chunk.element_ordinals],
            "text": chunk.text,
            "token_count": estimate_tokens(chunk.text),
        }
        for ordinal, chunk in enumerate(pieces)
    ]


def _stage_content(
    job: dict[str, Any], document: dict[str, Any]
) -> tuple[str, int | None]:
    """A crash after parsing carries on from what's stored."""
    staged = revisions.staged_for_job(job["id"])
    if staged and revisions.has_content(staged):
        return staged, None
    _next_stage(job["id"], STAGE_PARSE)
    data, sha256 = _verified_snapshot(document)
    plain = _unlocked(job, document, data)
    parsed = parse.parse(document["file_kind"], plain, document["file_name"])
    revision_id = revisions.stage(document["id"], job["id"], sha256)
    rows, ids = element_rows(parsed.elements)
    chunks = _chunk_rows(revision_id, ids, parsed.chunks)
    revisions.write_content(revision_id, document["id"], rows, chunks)
    publishing.store_page_facts(revision_id, parsed.page_facts)
    return revision_id, parsed.pages


def _finish_published(job: dict[str, Any], document: dict[str, Any]) -> None:
    """After publish: the index write and what follows, safe to repeat after a crash."""
    index.replay_ops()
    publishing.queue_followups(document["id"], job["source_id"], document["file_kind"])
    if document["file_kind"] != "PDF":
        unlock.forget(document["id"])  # only a PDF's hard pages still need it
    jobs.succeed(job["id"])


def _run_stages(job: dict[str, Any], reindexing: bool) -> None:
    document = documents.get(job["document_id"])
    if job["payload"].get("download"):
        _materialize(document)
    revision_id, pages = _stage_content(job, document)
    _next_stage(job["id"], STAGE_EMBED)
    embedding.embed_revision(revision_id)
    _next_stage(job["id"], STAGE_PUBLISH)
    try:
        revisions.publish(revision_id, job["id"], not reindexing, pages)
    except refusals.Refused as refused:
        if jobs.cancel_requested(job["id"]):
            raise Cancelled from refused
        raise parse.CantRead(CHANGED_REASON) from refused
    _finish_published(job, documents.get(document["id"]))


def _is_evicted(document: dict[str, Any]) -> bool:
    """A file with no local copy left at all, checked before any read."""
    rows = documents.locations(document["id"])
    here = [originals.look(Path(row["path"])) for row in rows]
    return bool(rows) and not any(seen and not seen.dataless for seen in here)


def _end_without_publishing(job: dict[str, Any], document_id: str, keep: bool) -> None:
    revisions.discard_for_job(job["id"])
    unlock.forget_job(job["id"])
    if not keep:
        unlock.forget(document_id)


def run(job: dict[str, Any]) -> None:
    document = documents.get(job["document_id"])
    if revisions.published_by_job(job["id"]):
        _finish_published(job, document)  # a crash came after publish
        return
    reindexing = document["status"] == states.INDEXED
    if not job["payload"].get("download") and _is_evicted(document):
        if reindexing:
            jobs.fail(job["id"], "This file isn't on this Mac now.", "not_here")
        else:
            documents.mark_evicted_before_reading(document["id"])
            jobs.skip(job["id"], "This file isn't on this Mac yet.", "not_downloaded")
        return
    claimed = reindexing or documents.claim_ingest(document["id"])
    if not claimed and documents.get(document["id"])["status"] != states.INDEXING:
        jobs.fail(job["id"], "This document can't be indexed now.", "state")
        return
    _run_with_outcomes(job, document["id"], reindexing)


def _run_with_outcomes(job: dict[str, Any], document_id: str, reindexing: bool) -> None:
    try:
        _run_stages(job, reindexing)
    except Cancelled:
        _end_without_publishing(job, document_id, False)
        if not reindexing:
            documents.mark_cancelled(document_id)
        jobs.finish_cancelled(job["id"])
    except Skipped as skipped:
        _end_without_publishing(job, document_id, False)
        if not reindexing and skipped.code == OFFLINE_CODE:
            documents.mark_evicted_before_reading(document_id)
        elif not reindexing:
            documents.mark_needs_password(document_id, skipped.reason)
        jobs.skip(job["id"], skipped.reason, skipped.code)
    except (parse.CantRead, OSError) as error:
        reason = str(error) if isinstance(error, parse.CantRead) else None
        _fail(
            job,
            document_id,
            reindexing,
            reason or "SecureFolio couldn't open this file.",
        )
    except Exception:
        logger.exception("ingest failed")  # the type alone reaches the log
        _fail(job, document_id, reindexing, INTERNAL_REASON)


def _fail(job: dict[str, Any], document_id: str, reindexing: bool, reason: str) -> None:
    _end_without_publishing(job, document_id, False)
    if not reindexing:
        documents.fail_ingest(document_id, reason, "unreadable")
    jobs.fail(job["id"], reason, "unreadable")
