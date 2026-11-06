"""Nightly backup and restore. `library.db` through SQLite's backup API, the managed
copies, a manifest of where the watched files are, and the tombstone ledger. The search
index isn't copied: a restore rebuilds it from stored embeddings, reading no file."""

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend import database, index, memories, rebase, refusals, settings, vault
from backend.database import document_locations, memory_tombstones
from backend.database import sources as source_table
from backend.migrations import run as migrations

logger = logging.getLogger(__name__)
KEEP = 7
DAY_SECONDS = 24 * 3600
MANIFEST = "manifest.json"
LIBRARY = "library.db"
LEDGER = "memory_tombstones.jsonl"
OBJECTS = "objects"  # shared by every backup here, so only new ones are copied
PARTIAL = ".partial"
NAME_FORMAT = "%Y%m%d-%H%M%S"
NO_SUCH = "There's no such backup."
UNREADABLE = "This backup can't be read, so nothing was changed."
TEMPORARY = ".tmp"
_one_at_a_time = threading.Lock()


def folder() -> Path:
    chosen = settings.get(settings.BACKUP_FOLDER)
    return Path(chosen) if chosen else vault.backups_dir()


def _copy_library(target: Path) -> None:
    source = sqlite3.connect(vault.library_db())
    copy = sqlite3.connect(target)
    try:
        with copy:
            source.backup(copy)
    finally:
        copy.close()
        source.close()


def _copy_whole(source: Path, target: Path) -> None:
    """Appears under its name only once complete, so an existing file can be trusted."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}{TEMPORARY}")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_missing(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_file() and not path.name.endswith(TEMPORARY):
            copy = target / path.relative_to(source)
            if not copy.exists():
                _copy_whole(path, copy)


def _watched() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(
                source_table.c.path.label("folder"),
                document_locations.c.path,
                document_locations.c.document_id,
            ).join(source_table, source_table.c.id == document_locations.c.source_id)
        ).mappings()
        return [dict(row) for row in rows]


def _size_of(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def back_up(now: float | None = None, keep: str | None = None) -> dict[str, Any]:
    """A backup appears whole or not at all: written beside its name, then renamed. Each
    has a name of its own. `keep` is one retention mustn't remove just yet."""
    moment = time.time() if now is None else now
    home = folder()
    with _one_at_a_time:
        home.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime(NAME_FORMAT, time.localtime(moment))
        name = f"{stamp}-{uuid.uuid4().hex[:6]}"
        partial = home / f"{name}{PARTIAL}"
        partial.mkdir()
        _copy_library(partial / LIBRARY)
        _copy_missing(vault.objects_dir(), home / OBJECTS)
        if vault.tombstone_ledger().exists():
            shutil.copy2(vault.tombstone_ledger(), partial / LEDGER)
        (partial / MANIFEST).write_text(
            json.dumps(
                {
                    "created_at": moment,
                    "vault": str(vault.root().resolve()),
                    "watched": _watched(),
                }
            )
        )
        done = home / name
        partial.replace(done)
        _keep_the_latest(home, keep)
    return {"id": name, "created_at": moment, "size_bytes": _size_of(done)}


def _keep_the_latest(home: Path, keep: str | None) -> None:
    for old in list_backups(home)[KEEP:]:
        if old["id"] != keep:
            shutil.rmtree(home / old["id"], ignore_errors=True)


def list_backups(home: Path | None = None) -> list[dict[str, Any]]:
    where = home or folder()
    if not where.is_dir():
        return []
    found = []
    for path in where.iterdir():
        manifest = path / MANIFEST
        if manifest.is_file():
            found.append(
                {
                    "id": path.name,
                    "created_at": json.loads(manifest.read_text())["created_at"],
                    "size_bytes": _size_of(path),
                }
            )
    return sorted(found, key=lambda row: -row["created_at"])


def run_now(now: float | None = None) -> dict[str, Any]:
    """Back up now, saying what happened where Settings can show it."""
    moment = time.time() if now is None else now
    try:
        made = back_up(moment)
    except (OSError, sqlite3.Error) as failed:
        logger.exception("the backup failed")
        last = {
            "at": moment,
            "error": f"The backup failed: {failed}",
        }
    else:
        last = {"at": moment, "error": None, "size_bytes": made["size_bytes"]}
    settings.put(settings.BACKUP_LAST, last)
    return last


def run_if_due(now: float | None = None) -> bool:
    moment = time.time() if now is None else now
    if not settings.get(settings.BACKUP_NIGHTLY):
        return False
    last = settings.get(settings.BACKUP_LAST) or {}
    if moment - last.get("at", 0.0) < DAY_SECONDS:
        return False
    run_now(moment)
    return True


def _ledger_hashes(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["text_hash"] for line in lines if line.strip()}


def _current_tombstones() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(sa.select(memory_tombstones)).mappings()
        return [dict(row) for row in rows]


def _apply_tombstones(kept: list[dict[str, Any]], ledger: set[str]) -> None:
    """The union of what's deleted now and what the backup knew, so a memory deleted
    since the backup doesn't come back with it."""
    now = time.time()
    with database.write() as connection:
        for row in kept:
            connection.execute(
                sa.insert(memory_tombstones).prefix_with("OR IGNORE").values(**row)
            )
        for text_hash in ledger:
            connection.execute(
                sa.insert(memory_tombstones)
                .prefix_with("OR IGNORE")
                .values(text_hash=text_hash, embedding=None, created_at=now)
            )
    gone = {row["text_hash"] for row in kept} | ledger
    for row in memories.all_active():
        if memories.normalized_hash(row["text"]) in gone:
            memories.delete(row["id"], row["embedding"])
    vault.tombstone_ledger().write_text(
        "".join(json.dumps({"text_hash": h}) + "\n" for h in sorted(gone)),
        encoding="utf-8",
    )


def _has_the_schema(columns: dict[str, set[str]]) -> bool:
    """Every table and column this version has. No release has shipped, so there's no
    older shape to accept; the first migration after one changes this to ask for it."""
    return all(
        name in columns and {c.name for c in table.columns} <= columns[name]
        for name, table in database.metadata.tables.items()
    )


def _checked_snapshot(chosen: Path) -> None:
    """Before anything changes: it opens, it's whole, and it's a library."""
    database_file = chosen / LIBRARY
    if not database_file.is_file():
        raise refusals.conflict(UNREADABLE)
    snapshot = sqlite3.connect(f"{database_file.resolve().as_uri()}?mode=ro", uri=True)
    try:
        whole = snapshot.execute("PRAGMA integrity_check").fetchone()
        names = [
            row[0]
            for row in snapshot.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
        columns = {
            name: {row[1] for row in snapshot.execute(f'PRAGMA table_info("{name}")')}
            for name in names
        }
    except sqlite3.Error as failed:
        raise refusals.conflict(UNREADABLE) from failed
    finally:
        snapshot.close()
    if whole != ("ok",) or not _has_the_schema(columns):
        raise refusals.conflict(UNREADABLE)


def restore(backup_id: str) -> None:
    """Saves the current library first, then brings the backup back, with the deletions
    of both, and rebuilds the index with no parsing, embedding or watched file read. The
    caller has stopped everything else that writes."""
    home = folder()
    chosen = home / Path(backup_id).name
    manifest = chosen / MANIFEST
    if not manifest.is_file():
        raise refusals.not_found(NO_SUCH)
    _checked_snapshot(chosen)
    old_root = json.loads(manifest.read_text()).get("vault")
    kept = _current_tombstones()
    ledger = _ledger_hashes(vault.tombstone_ledger()) | _ledger_hashes(chosen / LEDGER)
    back_up(keep=chosen.name)
    source = sqlite3.connect(chosen / LIBRARY)
    live = sqlite3.connect(vault.library_db())
    try:
        with live:
            source.backup(live)
    finally:
        live.close()
        source.close()
    database.dispose()
    migrations.upgrade()
    # The manifest names the old vault resolved; paths may be stored through a link
    # to it, so the form the restored records use counts too.
    rebase.library_paths(
        [
            (rebase.recorded_root() or "", str(vault.root())),
            (old_root or "", str(vault.root().resolve())),
        ]
    )
    _apply_tombstones(kept, ledger)
    _copy_missing(home / OBJECTS, vault.objects_dir())
    index.rebuild()
    _keep_the_latest(home, None)
