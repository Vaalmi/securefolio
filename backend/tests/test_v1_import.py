"""The v1 import: a file the owner picked, read-only, all or nothing, all Private."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend import database, notes, refusals, states, threads, v1_import
from data.build import v1


@pytest.fixture
def old(library, tmp_path) -> Path:
    return v1.build(tmp_path / "metadata.db")


def test_it_copies_everything_as_private(old):
    counts = v1_import.import_from(old)
    assert counts == {"notes": 2, "sessions": 1, "saved_queries": 1}
    assert {n["title"] for n in notes.all_notes()} == {"Roof quote", "Lease reminder"}
    assert all(n["sensitivity"] == states.PRIVATE for n in notes.all_notes())
    thread = threads.page()[0]
    assert thread["private_tainted"] is True
    turns = threads.transcript(thread["id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert all(t["sensitivity"] == states.PRIVATE for t in turns)
    with database.read() as connection:
        query = connection.execute(sa.select(database.saved_queries)).mappings().one()
    assert query["text"] == "insurance renewal"
    assert query["sensitivity"] == states.PRIVATE


def test_running_it_again_adds_nothing(old):
    v1_import.import_from(old)
    assert v1_import.import_from(old) == {"notes": 0, "sessions": 0, "saved_queries": 0}
    assert len(notes.all_notes()) == 2


def test_it_leaves_the_file_exactly_as_it_was(old):
    before = (old.read_bytes(), os.stat(old).st_mtime_ns)
    siblings = set(old.parent.iterdir())
    v1_import.import_from(old)
    assert (old.read_bytes(), os.stat(old).st_mtime_ns) == before
    assert set(old.parent.iterdir()) == siblings


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ("DROP TABLE notes", "The table notes is missing."),
        (
            "ALTER TABLE sessions DROP COLUMN messages",
            "sessions has no column messages",
        ),
        ("ALTER TABLE notes ADD COLUMN mood TEXT", "notes has an unknown column mood"),
    ],
)
def test_any_difference_imports_nothing_and_says_which(old, change, named):
    connection = sqlite3.connect(old)
    with connection:
        connection.execute(change)
    connection.close()
    with pytest.raises(refusals.Refused) as refused:
        v1_import.import_from(old)
    assert named in refused.value.detail
    assert notes.all_notes() == []


def test_an_extra_table_imports_nothing(old):
    connection = sqlite3.connect(old)
    with connection:
        connection.execute("CREATE TABLE odd (x)")
    connection.close()
    with pytest.raises(refusals.Refused) as refused:
        v1_import.import_from(old)
    assert "unknown table odd" in refused.value.detail
    assert notes.all_notes() == []


@pytest.mark.parametrize("name", ["meta?data.db", "meta#data.db", "meta data.db"])
def test_an_awkward_file_name_stays_read_only(library, tmp_path, name):
    path = v1.build(tmp_path / name)
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    siblings = set(path.parent.iterdir())
    assert v1_import.import_from(path)["notes"] == 2
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before
    assert set(path.parent.iterdir()) == siblings


def test_a_missing_file_is_refused(library, tmp_path):
    with pytest.raises(refusals.Refused):
        v1_import.import_from(tmp_path / "nothing.db")


def test_dump_schema_writes_names_and_no_value(tmp_path):
    path = v1.build(tmp_path / "metadata.db")
    planted = "zqPLANTEDVALUE4471"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "INSERT INTO notes VALUES ('n9', ?, ?, 1.0)", (planted, planted)
        )
    connection.close()
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "backend.v1_import", str(path), "--dump-schema"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert planted not in done.stdout
    assert json.loads(done.stdout)["notes"] == ["id", "title", "body", "created_at"]


def test_the_cli_only_dumps(tmp_path):
    path = v1.build(tmp_path / "metadata.db")
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "backend.v1_import", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
