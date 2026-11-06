"""Privacy: editing a note's heading and text never changes its sensitivity. A Private
document's note, and a note saved from a Private chat, stay Private."""

import time
import uuid

import pytest

from backend import database, states
from backend.database import notes
from testing.note_helpers import note_row


def private_note(**extra) -> str:
    """A Private note: on a Private document, or saved from a Private chat."""
    note_id = uuid.uuid4().hex
    with database.write() as connection:
        connection.execute(
            notes.insert().values(
                **{
                    "id": note_id,
                    "title": "Old heading",
                    "body": "Old text.",
                    "citations": [],
                    "source_label": "Written by you",
                    "sensitivity": states.PRIVATE,
                    "created_at": time.time(),
                    **extra,
                }
            )
        )
    return note_id


@pytest.mark.parametrize(
    "extra",
    [
        {"document_id": "a-private-document"},
        {"thread_id": "a-private-chat", "source_label": "Saved from a chat"},
    ],
    ids=["a Private document's note", "a note saved from a Private chat"],
)
def test_an_edit_leaves_a_private_note_private(library, app_client, extra):
    note_id = private_note(**extra)
    edited = app_client.patch(
        f"/api/notes/{note_id}", json={"heading": "New heading", "body": "New text."}
    )
    assert edited.status_code == 200
    row = note_row(note_id)
    assert row["sensitivity"] == states.PRIVATE
    assert (row["title"], row["body"]) == ("New heading", "New text.")
