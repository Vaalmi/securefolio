"""Migration 0004 on a vault as 0003 left it: the import's own label and chat title
lose the old app's name, and nothing the owner wrote changes."""

import time

import sqlalchemy as sa
from alembic import command

from backend import database
from backend.migrations import run as migrations


def a_vault_at_0003() -> None:
    config = migrations._config()  # noqa: SLF001
    with database.engine().connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")


def a_note(note_id: str, label: str) -> None:
    with database.write() as connection:
        connection.execute(
            database.notes.insert().values(
                id=note_id,
                title="A note",
                body="Its words",
                citations=[],
                source_label=label,
                sensitivity="private",
                created_at=time.time(),
            )
        )


def a_chat(thread_id: str, title: str) -> None:
    now = time.time()
    with database.write() as connection:
        connection.execute(
            database.threads.insert().values(
                id=thread_id,
                title=title,
                scope={"kind": "library"},
                mode="private",
                created_at=now,
                updated_at=now,
            )
        )


def test_the_imports_label_and_title_lose_the_old_name():
    a_vault_at_0003()
    a_note("imported", "Imported from DocSynth v1")
    a_note("mine", "Written by you")
    a_chat("untitled", "Chat from v1")
    a_chat("named", "The lease, from v1")
    migrations.upgrade()
    with database.read() as connection:
        labels = dict(
            connection.execute(
                sa.select(database.notes.c.id, database.notes.c.source_label)
            ).all()
        )
        titles = dict(
            connection.execute(
                sa.select(database.threads.c.id, database.threads.c.title)
            ).all()
        )
        bodies = connection.execute(sa.select(database.notes.c.body)).scalars().all()
    assert labels == {"imported": "Imported", "mine": "Written by you"}
    assert titles == {"untitled": "Imported chat", "named": "The lease, from v1"}
    assert bodies == ["Its words", "Its words"]
