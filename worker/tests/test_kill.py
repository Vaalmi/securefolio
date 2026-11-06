"""Killing the app mid-import loses nothing and duplicates nothing."""

import os
import signal
import subprocess
import sys
import time

import pytest
import sqlalchemy as sa

from backend import database, embedder, index, jobs, revisions, sources, states
from backend.build_mode import STUB_MODELS_ENV
from backend.migrations import run as migrations
from testing.queue import drain, next_job
from worker import embedding, publishing
from worker import main as worker_main

FILES = 12


class Crash(BaseException):
    """A process dying here: nothing below catches it, and no cleanup runs."""


@pytest.fixture
def folder(library, tmp_path):
    path = tmp_path / "docs"
    path.mkdir()
    for number in range(FILES):
        (path / f"note{number}.txt").write_text(
            f"Note {number} holds the marker m{number}x."
        )
    return path


def assert_clean() -> None:
    """Each file indexed once, one active revision each, a vector for each chunk."""
    with database.read() as connection:
        docs = connection.execute(sa.select(database.documents)).mappings().all()
        assert len(docs) == FILES
        assert {d["status"] for d in docs} == {states.INDEXED}
        states_seen = (
            connection.execute(sa.select(database.revisions.c.state)).scalars().all()
        )
        assert states_seen.count(states.ACTIVE) == FILES
        assert states.STAGING not in states_seen
        # Publishing again after a crash would have made a second, retired revision.
        assert len(states_seen) == FILES
        chunks = connection.execute(sa.select(database.chunks)).mappings().all()
        assert len(chunks) == FILES
        assert all(chunk["embedding"] for chunk in chunks)
    index.sweep()
    assert index.count() == FILES
    for number in range(FILES):
        assert len(index.search(f"m{number}x", None, None, 5)) == 1


def expire_leases() -> None:
    with database.write() as connection:
        connection.execute(
            database.jobs.update()
            .where(database.jobs.c.state == states.RUNNING)
            .values(lease_expires_at=time.time() - 1)
        )
    worker_main.requeue_expired()


STAGES = {
    "after parsing": (revisions, "write_content"),
    "while embedding": (embedding, "embed_revision"),
    "at publish": (revisions, "publish"),
    "after publish, before the index": (publishing, "queue_followups"),
}


@pytest.mark.parametrize("stage", STAGES)
def test_a_crash_at_each_stage_loses_and_duplicates_nothing(folder, monkeypatch, stage):
    module, name = STAGES[stage]
    real = getattr(module, name)
    crashes = []

    def crash_once(*args, **kwargs):
        if not crashes:
            crashes.append(stage)
            real(*args, **kwargs)
            raise Crash
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, crash_once)
    sources.add_source(str(folder), states.STANDARD)
    with pytest.raises(Crash):
        drain()
    assert crashes
    expire_leases()
    drain()
    assert_clean()


def test_a_crash_partway_through_embedding_keeps_what_was_stored(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    calls = []
    real = embedder.embed_documents

    def counted(texts):
        calls.append(len(texts))
        return real(texts)

    monkeypatch.setattr(embedder, "embed_documents", counted)
    drain()
    assert sum(calls) == FILES
    assert_clean()


def test_a_first_index_whose_attempts_run_out_fails_with_a_reason(folder):
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())  # the scan
    job = next_job()
    from backend import documents  # noqa: PLC0415

    assert documents.claim_ingest(job["document_id"])
    for _ in range(jobs.MAX_ATTEMPTS - 1):
        expire_leases()
        with database.write() as connection:
            connection.execute(
                database.jobs.update()
                .where(database.jobs.c.id == job["id"])
                .values(state=states.RUNNING)
            )
    expire_leases()
    assert jobs.get(job["id"])["state"] == states.FAILED
    after = documents.get(job["document_id"])
    assert after["status"] == states.FAILED
    assert after["status_reason"]


def run_real_worker(vault_path: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "SECUREFOLIO_VAULT": vault_path,
        STUB_MODELS_ENV: "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "worker.main"],
        env=env,
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def queue_is_empty() -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(database.jobs.c.id).where(
                    database.jobs.c.state.in_((states.QUEUED, states.RUNNING)),
                    database.jobs.c.kind.in_((jobs.INGEST, jobs.SCAN)),
                )
            ).first()
            is None
        )


def test_sigkill_of_the_real_worker_mid_import(folder, test_vault):
    sources.add_source(str(folder), states.STANDARD)
    process = run_real_worker(str(test_vault))
    time.sleep(1.5)
    process.send_signal(signal.SIGKILL)
    process.wait()
    expire_leases()
    process = run_real_worker(str(test_vault))
    try:
        deadline = time.monotonic() + 60
        while not queue_is_empty() and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        process.terminate()
        process.wait(timeout=10)
    expire_leases()
    drain()
    assert_clean()


def test_the_worker_stops_when_the_backends_pipe_closes(library, test_vault):
    migrations.upgrade()
    process = run_real_worker(str(test_vault))
    time.sleep(1)
    assert process.stdin is not None
    process.stdin.close()
    assert process.wait(timeout=20) == 0
