"""Notes and chats brought in by the import from the app before SecureFolio keep their
content, but lose the old app's name: the label the import wrote on each note, and the
title it gave a chat that had none, are what the Notes and Chats screens show.

A label or title the owner wrote is never touched: only the import's own two strings
change."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NOTE_LABEL = ("Imported from DocSynth v1", "Imported")
CHAT_TITLE = ("Chat from v1", "Imported chat")


NOTES = sa.text("UPDATE notes SET source_label = :new WHERE source_label = :old")
THREADS = sa.text("UPDATE threads SET title = :new WHERE title = :old")


def _rename(statement: sa.TextClause, old: str, new: str) -> None:
    op.get_bind().execute(statement, {"old": old, "new": new})


def upgrade() -> None:
    _rename(NOTES, *NOTE_LABEL)
    _rename(THREADS, *CHAT_TITLE)


def downgrade() -> None:
    _rename(NOTES, *reversed(NOTE_LABEL))
    _rename(THREADS, *reversed(CHAT_TITLE))
