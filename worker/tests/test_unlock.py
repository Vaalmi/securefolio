"""Passwords: used in memory, for the job they name, and found nowhere afterward."""

import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend import database, documents, jobs, revisions, sources, states, vault
from testing.queue import drain
from worker import main as worker_main
from worker import unlock

DATA = Path(__file__).parents[2] / "data" / "library"
FIXTURE_KEY = "fixture-pass"  # noqa: S105


@pytest.fixture
def locked(library, tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    for name in ("Locked statement.pdf", "Locked memo.docx"):
        shutil.copy(DATA / "Finance" / name, folder / name)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        rows = connection.execute(sa.select(database.documents)).mappings().all()
    return {row["file_name"]: dict(row) for row in rows}


def bytes_of(path: Path) -> bytes:
    return path.read_bytes()


def test_both_are_skipped_with_a_plain_reason(locked):
    for document in locked.values():
        assert document["status"] == states.SKIPPED_PASSWORD
        assert "password" in document["status_reason"]


@pytest.mark.parametrize(
    ("name", "marker"),
    [("Locked statement.pdf", "basil"), ("Locked memo.docx", "juniper")],
)
def test_unlocking_indexes_it_with_the_file_unchanged(locked, tmp_path, name, marker):
    original = next((tmp_path / "locked").glob(name)).read_bytes()
    job_id = documents.unlock_and_index(locked[name]["id"])
    unlock.receive(job_id, FIXTURE_KEY)
    drain()
    document = documents.get(locked[name]["id"])
    assert document["status"] == states.INDEXED
    assert marker in " ".join(
        e["text"] for e in revisions.active_elements(document["id"])
    )
    assert next((tmp_path / "locked").glob(name)).read_bytes() == original
    assert unlock.held_for(document["id"]) is None


def test_the_password_is_found_nowhere_afterward(locked):
    job_id = documents.unlock_and_index(locked["Locked statement.pdf"]["id"])
    planted = "fixture-pass"
    unlock.receive(job_id, planted)
    drain()
    for path in vault.root().rglob("*"):
        if path.is_file():
            assert planted.encode() not in path.read_bytes(), path
    with database.read() as connection:
        assert planted not in json.dumps(
            [dict(r) for r in connection.execute(sa.select(database.jobs)).mappings()],
            default=str,
        )


def test_a_wrong_password_returns_to_skipped_with_unlock_still_offered(locked):
    document_id = locked["Locked statement.pdf"]["id"]
    job_id = documents.unlock_and_index(document_id)
    unlock.receive(job_id, "wrong")
    drain()
    after = documents.get(document_id)
    assert after["status"] == states.SKIPPED_PASSWORD
    assert "didn't open" in after["status_reason"]
    assert documents.unlock_and_index(document_id)


def test_a_password_for_another_job_doesnt_count(locked, monkeypatch):
    monkeypatch.setattr(unlock, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(unlock.take_for_job, "__defaults__", (0.01,))
    document_id = locked["Locked statement.pdf"]["id"]
    documents.unlock_and_index(document_id)
    unlock.receive("another-job", "fixture-pass")
    drain()
    after = documents.get(document_id)
    assert after["status"] == states.SKIPPED_PASSWORD
    assert "Enter it again" in after["status_reason"]


def test_a_worker_restart_loses_the_password_and_says_so(locked, monkeypatch):
    monkeypatch.setattr(unlock.take_for_job, "__defaults__", (0.01,))
    document_id = locked["Locked statement.pdf"]["id"]
    documents.unlock_and_index(document_id)  # no line ever arrives
    drain()
    assert documents.get(document_id)["status"] == states.SKIPPED_PASSWORD


def test_a_password_with_spaces_works_through_the_json_line(tmp_path, library):
    message = json.dumps({"job_id": "j", "password": "  spaced out  "})
    parsed = json.loads(message)
    unlock.receive(parsed["job_id"], parsed["password"])
    assert unlock.take_for_job("j", "d") == "  spaced out  "
    unlock.forget("d")


def test_a_reindex_with_no_password_is_skipped_and_can_be_unlocked(locked):
    document_id = locked["Locked statement.pdf"]["id"]
    job_id = documents.unlock_and_index(document_id)
    unlock.receive(job_id, "fixture-pass")
    drain()
    active = documents.get(document_id)["active_revision_id"]
    documents.reindex(document_id)
    drain()
    after = documents.get(document_id)
    assert after["status"] == states.INDEXED
    assert after["active_revision_id"] == active
    with database.read() as connection:
        last = (
            connection.execute(
                sa.select(database.jobs)
                .where(
                    database.jobs.c.kind == jobs.INGEST,
                    database.jobs.c.document_id == document_id,
                )
                .order_by(database.jobs.c.created_at.desc())
            )
            .mappings()
            .first()
        )
    assert last is not None
    assert last["state"] == states.SKIPPED
    job_id = documents.unlock_and_index(document_id)
    unlock.receive(job_id, "fixture-pass")
    drain()
    assert documents.get(document_id)["active_revision_id"] != active


def test_an_unreadable_line_on_stdin_is_ignored(monkeypatch):
    import io  # noqa: PLC0415
    import threading  # noqa: PLC0415

    stop = threading.Event()
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('not json\n{"job_id": "z", "password": "p"}\n')
    )
    worker_main._read_stdin(stop)
    assert stop.is_set()
    assert unlock.take_for_job("z", "d", 0) == "p"
    unlock.forget("d")
