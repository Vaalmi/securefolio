"""Ledgers for the sheet-columns tests: a Standard one and a Private one of 40 rows,
past a sheet's first chunk of 25, and a stub Claude that searches for one row."""

import uuid

import sqlalchemy as sa

from backend import database, sources, states
from testing.ask_helpers import default_reply, plan_reply, system_of
from testing.queue import drain

ROWS = 40  # past the first chunk's 25
WANTED = "Larkspur"  # one row's payee, past the first chunk
PLAN = (
    '{"query": "Larkspur", "kind": "lookup", "date_from": null, "date_to": null, '
    '"keywords": []}'
)


def planted() -> str:
    return f"zq{uuid.uuid4().hex[:12]}"


def ledger(columns: list[str]) -> str:
    rows = [",".join(columns)]
    for n in range(1, ROWS + 1):
        payee = WANTED if n == ROWS - 5 else f"Payee{n}"
        rows.append(f"2026-01-{n % 28 + 1:02d},{payee},${n}.00")
    return "\n".join(rows) + "\n"


def add_ledgers(tmp_path) -> list[str]:
    """A Standard ledger, and a Private one whose column names are planted, which
    it gives back."""
    secret = [planted(), planted(), planted()]
    for name, level, columns in (
        ("plain", states.STANDARD, ["Date", "Payee", "Amount"]),
        ("hidden", states.PRIVATE, secret),
    ):
        folder = tmp_path / name
        folder.mkdir()
        (folder / f"{name} ledger.csv").write_text(ledger(columns))
        sources.add_source(str(folder), level)
    drain()
    return secret


def document_id(title: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.title == title
            )
        ).scalar_one()


def claude_finding(query: str):
    """A stub Claude that plans a search for `query` and cites what it's given."""

    def reply(path: str, body: dict) -> dict:
        if "search query that stands alone" in system_of(body):
            return plan_reply(body, query)
        return default_reply(path, body)

    return reply
