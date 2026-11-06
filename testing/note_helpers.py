"""Helpers the note tests share."""

import sqlalchemy as sa

from backend import database
from backend.database import notes


def note_row(note_id: str) -> dict:
    """A note as stored."""
    with database.read() as connection:
        return dict(
            connection.execute(sa.select(notes).where(notes.c.id == note_id))
            .mappings()
            .one()
        )
