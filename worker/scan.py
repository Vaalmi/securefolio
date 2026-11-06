"""The reconcile scan: the truth, where file events are only hints."""

import fnmatch
import logging
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend import database, jobs, originals, sources, states, vault
from backend.database import document_locations, documents
from worker import locations

logger = logging.getLogger(__name__)
IGNORED = (
    ".DS_Store",
    "~$*",
    "*.tmp",
    "*.crdownload",
    "*.part",
    ".~lock*",
    "Thumbs.db",
)
STOP_CHECK_EVERY = 50


class ScanStopped(Exception):
    """The folder was paused or removed, or the job cancelled, during the scan."""


def _ignored(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in IGNORED)


def _inside_vault(path: Path) -> bool:
    return vault.root().resolve() in path.resolve().parents


def _files_to_observe(root: Path, only: list[str] | None) -> list[Path]:
    paths = [Path(p) for p in only] if only else originals.walk(root)
    return [p for p in paths if not _ignored(p) and not _inside_vault(p)]


def _keep_going(job_id: str, source_id: str) -> None:
    if (
        jobs.cancel_requested(job_id)
        or sources.get(source_id)["status"] != states.SCANNING
    ):
        raise ScanStopped


def holds_not_downloaded(source_id: str) -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(documents.c.id)
                .join(
                    document_locations,
                    document_locations.c.document_id == documents.c.id,
                )
                .where(
                    document_locations.c.source_id == source_id,
                    documents.c.status == states.NOT_DOWNLOADED,
                )
                .limit(1)
            ).first()
            is not None
        )


def run(job: dict[str, Any]) -> None:
    """A whole folder, or (from the watcher) the paths in the job's payload."""
    payload = job["payload"]
    source_id = job["source_id"] or payload["source_id"]
    source = sources.get(source_id)
    only = payload.get("paths")
    root = Path(source["path"])
    if not only and not root.is_dir():
        raise FileNotFoundError(f"{source['name']} isn't there.")
    seen_paths: set[str] = set()
    for count, path in enumerate(_files_to_observe(root, only)):
        if count % STOP_CHECK_EVERY == 0 and not only:
            _keep_going(job["id"], source_id)
        seen = originals.look(path)
        if seen is not None:
            try:
                locations.observe(seen, source, bool(payload.get("new_folder")))
            except locations.FolderGone as gone:
                raise ScanStopped from gone
            seen_paths.add(str(path))
    if only:
        gone = {p for p in only if originals.look(Path(p)) is None}
        _mark_listed_gone(source_id, gone)
    else:
        locations.mark_gone(source_id, seen_paths)
        sources.finish_scan(source_id, holds_not_downloaded(source_id))


def _mark_listed_gone(source_id: str, gone: set[str]) -> None:
    with database.read() as connection:
        held = set(
            connection.execute(
                sa.select(document_locations.c.path).where(
                    document_locations.c.source_id == source_id
                )
            ).scalars()
        )
    locations.mark_gone(source_id, held - gone)


def enqueue_timer_scans() -> None:
    """The hourly scan, and the one after a wake."""
    for source in sources.live():
        if source["builtin"] or source["status"] == states.PAUSED:
            continue
        sources.rescan(source["id"], by_owner=False)
