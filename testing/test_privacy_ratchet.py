"""A document going Private takes everything derived from it along, by one path."""

import uuid

import pytest
import sqlalchemy as sa

from backend import database, documents, index, sensitivity, sources, states
from testing.queue import drain


@pytest.fixture
def indexed(library, tmp_path):
    """A Standard document, used by a thread, cited by a note, with a memory."""
    folder = tmp_path / "standard"
    folder.mkdir()
    (folder / "lease.txt").write_text(
        "The planted-private-lease says rent is due monthly."
    )
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document_id = connection.execute(sa.select(database.documents.c.id)).scalar()
    thread_ids = [uuid.uuid4().hex for _ in range(3)]
    with database.write() as connection:
        for thread_id, scope in zip(thread_ids, ([], [], [document_id]), strict=True):
            connection.execute(
                database.threads.insert().values(
                    id=thread_id,
                    title="t",
                    scope={"document_ids": scope},
                    mode="standard",
                    created_at=1,
                    updated_at=1,
                )
            )
        connection.execute(
            database.thread_documents.insert().values(
                thread_id=thread_ids[0], document_id=document_id, ordinal=0
            )
        )
        connection.execute(
            database.memories.insert().values(
                id="m",
                kind="Recap",
                text="recap",
                sensitivity=states.STANDARD,
                source_thread_id=thread_ids[0],
                source_label="chat",
                state="active",
                created_at=1,
            )
        )
        connection.execute(
            database.notes.insert().values(
                id="n",
                title="n",
                body="b",
                citations=[{"document_id": document_id}],
                source_label="x",
                sensitivity=states.STANDARD,
                created_at=1,
            )
        )
    return {
        "source": source,
        "document_id": document_id,
        "threads": thread_ids,
        "folder": folder,
        "tmp": tmp_path,
    }


def assert_all_private(state):
    with database.read() as connection:
        rows = {
            "document": connection.execute(
                sa.select(database.documents.c.sensitivity)
            ).scalar(),
            "chunk": connection.execute(
                sa.select(database.chunks.c.sensitivity)
            ).scalar(),
            "card": connection.execute(
                sa.select(database.cards.c.sensitivity)
            ).scalar(),
            "note": connection.execute(
                sa.select(database.notes.c.sensitivity)
            ).scalar(),
            "memory": connection.execute(
                sa.select(database.memories.c.sensitivity)
            ).scalar(),
        }
        tainted = connection.execute(
            sa.select(database.threads.c.id, database.threads.c.private_tainted)
        ).all()
    assert set(rows.values()) == {states.PRIVATE}, rows
    taint = dict(tainted)
    assert taint[state["threads"][0]]
    assert taint[state["threads"][2]]
    assert not taint[state["threads"][1]]
    found = index.search(
        "planted-private-lease", None, index.where_clause([states.STANDARD]), 5
    )
    assert found == []
    assert index.search(
        "planted-private-lease", None, index.where_clause([states.PRIVATE]), 5
    )


def test_making_a_folder_private_moves_everything_before_it_returns(indexed):
    sources.set_sensitivity(indexed["source"]["id"], states.PRIVATE)
    assert_all_private(indexed)
    after = sources.get(indexed["source"]["id"])
    assert not after["mcp_enabled"]
    assert not after["auto_organize"]


def test_a_copy_found_in_a_private_folder_takes_the_same_path(indexed):
    private = indexed["tmp"] / "private"
    private.mkdir()
    (private / "copy.txt").write_text((indexed["folder"] / "lease.txt").read_text())
    sources.add_source(str(private), states.PRIVATE)
    drain()
    assert_all_private(indexed)
    assert len(documents.locations(indexed["document_id"])) == 2


def test_going_back_to_standard_leaves_threads_and_memories_private(indexed):
    source_id = indexed["source"]["id"]
    sources.set_sensitivity(source_id, states.PRIVATE)
    sources.set_sensitivity(source_id, states.STANDARD)
    with database.read() as connection:
        assert (
            connection.execute(sa.select(database.documents.c.sensitivity)).scalar()
            == states.STANDARD
        )
        assert (
            connection.execute(sa.select(database.chunks.c.sensitivity)).scalar()
            == states.STANDARD
        )
        assert (
            connection.execute(sa.select(database.memories.c.sensitivity)).scalar()
            == states.PRIVATE
        )
        assert (
            connection.execute(sa.select(database.notes.c.sensitivity)).scalar()
            == states.PRIVATE
        )
        assert connection.execute(
            sa.select(database.threads.c.private_tainted).where(
                database.threads.c.id == indexed["threads"][0]
            )
        ).scalar()
    assert index.search(
        "planted-private-lease", None, index.where_clause([states.STANDARD]), 5
    )


def test_a_document_stays_private_while_any_location_is(indexed):
    private = indexed["tmp"] / "private"
    private.mkdir()
    (private / "copy.txt").write_text((indexed["folder"] / "lease.txt").read_text())
    other = sources.add_source(str(private), states.PRIVATE)
    drain()
    sources.set_sensitivity(indexed["source"]["id"], states.STANDARD)
    assert documents.get(indexed["document_id"])["sensitivity"] == states.PRIVATE
    sources.set_sensitivity(other["id"], states.STANDARD)
    assert documents.get(indexed["document_id"])["sensitivity"] == states.STANDARD


def test_a_private_folder_refuses_organizing_and_mcp(indexed):
    source_id = indexed["source"]["id"]
    sources.set_sensitivity(source_id, states.PRIVATE)
    for switch in ({"auto_organize": True}, {"mcp_enabled": True}):
        with pytest.raises(Exception, match="Private"):
            sources.set_switches(source_id, **switch)


def test_an_unchanged_sensitivity_records_nothing(indexed):
    with database.write() as connection:
        assert not sensitivity.apply_in(connection, indexed["document_id"])
