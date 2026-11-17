"""A report as `backend/reports.py` saves it, for the tests of your own set."""

from typing import Any

import sqlalchemy as sa

from backend import database
from evals import cli, own_set
from evals.tests.questions import SOL


def document(title: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.title == title
            )
        ).scalar_one()


def reported(**given: Any) -> dict[str, Any]:
    """A report as `backend/reports.py` saves it, on the lease's page 3."""
    return {
        **cli.BLANK_QUESTION,
        "id": "r-1",
        "category": own_set.REPORTED,
        "question": "What is the pet deposit on my apartment lease?",
        "expected_document": "Apartment lease.pdf",
        "expected_document_id": document("Apartment lease"),
        "expected_place": {"kind": "page", "page": 3},
        "note": "",
        "private": False,
        "private_chat": False,
        "rested_on": {"documents": [], "memories": []},
        "reported": {"message": "m", "thread": "t", "model": SOL, "at": "2026-11-15"},
        **given,
    }
