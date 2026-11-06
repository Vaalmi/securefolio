"""Copies notes, sessions and saved queries from a v1 `metadata.db` the owner picked.
Opened read-only and immutable, so its bytes and time don't change. Its shape is checked
first: any difference imports nothing and is named. All of it comes in Private."""

import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, refusals, states, threads
from backend.database import messages, notes, saved_queries, v1_imports
from backend.database import threads as thread_table

SHAPE = {
    "notes": {"id", "title", "body", "created_at"},
    "sessions": {"id", "title", "messages", "created_at"},
    "saved_queries": {"id", "query", "created_at"},
}
SOURCE_LABEL = "Imported from DocSynth v1"
SCOPE = {"kind": "library", "include_private": True, "label": "All · Private included"}


def _open(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> dict[str, list[str]]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
        for name in names
    }


def dump_schema(path: Path) -> dict[str, list[str]]:
    """Table and column names only. No value is ever read."""
    connection = _open(path)
    try:
        return _tables(connection)
    finally:
        connection.close()


def differences(found: dict[str, list[str]]) -> list[str]:
    """What's missing from, or unknown in, a table this import needs."""
    named = [f"There's an unknown table {t}." for t in sorted(set(found) - set(SHAPE))]
    for table, wanted in SHAPE.items():
        if table not in found:
            named.append(f"The table {table} is missing.")
            continue
        have = set(found[table])
        named.extend(f"{table} has no column {c}." for c in sorted(wanted - have))
        named.extend(
            f"{table} has an unknown column {c}." for c in sorted(have - wanted)
        )
    return named


def _seen(connection: Connection, kind: str, v1_id: str) -> bool:
    return (
        connection.execute(
            sa.select(v1_imports.c.new_id).where(
                v1_imports.c.kind == kind, v1_imports.c.v1_id == v1_id
            )
        ).first()
        is not None
    )


def _record(connection: Connection, kind: str, v1_id: str, new_id: str) -> None:
    connection.execute(
        v1_imports.insert().values(
            kind=kind, v1_id=v1_id, new_id=new_id, imported_at=time.time()
        )
    )


def _copy_note(connection: Connection, row: sqlite3.Row) -> None:
    new_id = uuid.uuid4().hex
    connection.execute(
        notes.insert().values(
            id=new_id,
            title=row["title"] or "Note",
            body=row["body"] or "",
            citations=[],
            document_id=None,
            source_label=SOURCE_LABEL,
            sensitivity=states.PRIVATE,
            thread_id=None,
            created_at=float(row["created_at"]),
        )
    )
    _record(connection, "note", str(row["id"]), new_id)


def _copy_query(connection: Connection, row: sqlite3.Row) -> None:
    new_id = uuid.uuid4().hex
    connection.execute(
        saved_queries.insert().values(
            id=new_id,
            text=row["query"],
            sensitivity=states.PRIVATE,
            created_at=float(row["created_at"]),
        )
    )
    _record(connection, "query", str(row["id"]), new_id)


def _copy_session(connection: Connection, row: sqlite3.Row) -> None:
    new_id = uuid.uuid4().hex
    at = float(row["created_at"])
    connection.execute(
        thread_table.insert().values(
            id=new_id,
            title=row["title"] or "Chat from v1",
            scope=SCOPE,
            mode="private",
            private_tainted=True,
            created_at=at,
            updated_at=at,
        )
    )
    for ordinal, turn in enumerate(json.loads(row["messages"]), start=1):
        connection.execute(
            messages.insert().values(
                id=uuid.uuid4().hex,
                thread_id=new_id,
                ordinal=ordinal,
                role=(
                    threads.ROLE_USER
                    if turn.get("role") == threads.ROLE_USER
                    else threads.ROLE_ASSISTANT
                ),
                content=str(turn.get("content", "")),
                sensitivity=states.PRIVATE,
                created_at=at,
            )
        )
    _record(connection, "session", str(row["id"]), new_id)


COPIES = (
    ("notes", "note", _copy_note),
    ("sessions", "session", _copy_session),
    ("saved_queries", "query", _copy_query),
)


def import_from(path: Path) -> dict[str, int]:
    """Skips anything imported before, so running it again adds only what's new."""
    if not path.is_file():
        raise refusals.invalid("There's no file there.")
    source = _open(path)
    try:
        difference = differences(_tables(source))
        if difference:
            raise refusals.conflict(
                "This doesn't look like the v1 database this import knows, so nothing "
                "was imported. " + " ".join(difference)
            )
        counts = {table: 0 for table, _, _ in COPIES}
        with database.write() as connection:
            for table, kind, copy in COPIES:
                for row in source.execute(f'SELECT * FROM "{table}"'):  # noqa: S608
                    if not _seen(connection, kind, str(row["id"])):
                        copy(connection, row)
                        counts[table] += 1
    finally:
        source.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--dump-schema", action="store_true")
    given = parser.parse_args()
    if not given.dump_schema:
        parser.error("The import runs from Settings. Here, only --dump-schema.")
    sys.stdout.write(json.dumps(dump_schema(given.path), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
