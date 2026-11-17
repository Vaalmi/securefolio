"""The scope a question speaks of: the documents it names as chosen. The run asks in
it, and search is measured in it."""

from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend import database

THESE = "these"


def selected(question: dict[str, Any]) -> list[str]:
    """Documents a question speaks of as chosen, "which of these two": the scope it
    asks and searches in."""
    if THESE not in question["question"].casefold() or not question["documents"]:
        return []
    titles = [Path(path).stem for path in question["documents"]]
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(database.documents.c.id).where(
                    database.documents.c.title.in_(titles)
                )
            ).scalars()
        )
