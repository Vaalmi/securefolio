"""Writes and reads the log of what was sent to the cloud, and what was refused."""

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

import sqlalchemy as sa

from backend import database, providers
from backend.database import egress_log

SENT = "sent"
ATTEMPTED = "attempted"
FAILED = "failed"
REFUSED = "refused"


@dataclass(frozen=True)
class Entry:
    model: str  # its full id, provider:model
    purpose: str
    outcome: str
    document_ids: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    thread_id: str | None = None
    detail: str | None = None
    input_usd: float | None = None  # the price per million it was accepted at
    output_usd: float | None = None


def _row(entry: Entry) -> dict[str, object]:
    """The provider is read from the model id, so the two can't disagree."""
    return {
        "at": time.time(),
        "provider": providers.split(entry.model)[0],
        **asdict(entry),
    }


def record(entry: Entry) -> int:
    with database.write() as connection:
        made = connection.execute(egress_log.insert().values(**_row(entry)))
    return made.lastrowid


def reserve(entry: Entry, since: float, cap: float) -> int | None:
    """Records an attempt, unless the month's spend is already at the cap. One write
    transaction, so two processes can't both pass on the same figure."""
    with database.write() as connection:
        spent = connection.execute(
            sa.select(sa.func.sum(egress_log.c.cost_usd)).where(
                egress_log.c.at >= since, egress_log.c.outcome.in_((SENT, ATTEMPTED))
            )
        ).scalar()
        if float(spent or 0.0) >= cap:
            return None
        made = connection.execute(egress_log.insert().values(**_row(entry)))
    return made.lastrowid


def settle(entry_id: int, outcome: str, **values: object) -> None:
    """An attempt's result, once it's known. One with no answer stays an attempt, at
    its estimate: the provider may have taken it, so spend can't leave it out."""
    with database.write() as connection:
        connection.execute(
            egress_log.update()
            .where(egress_log.c.id == entry_id)
            .values(outcome=outcome, **values)
        )


Price = tuple[float | None, float | None]


def accepted_price(entry_id: int) -> Price:
    """The price per million a call was accepted at, as its record keeps it."""
    with database.read() as connection:
        row = connection.execute(
            sa.select(egress_log.c.input_usd, egress_log.c.output_usd).where(
                egress_log.c.id == entry_id
            )
        ).first()
    return (row.input_usd, row.output_usd) if row else (None, None)


def settle_open(
    detail: str,
    outcome: str,
    cost_of: Callable[[str, Price], float],
    usage: dict[str, int],
) -> None:
    """Settles the attempts still open under this detail, and no others, each at the
    price it was accepted at."""
    with database.write() as connection:
        rows = connection.execute(
            sa.select(
                egress_log.c.id,
                egress_log.c.model,
                egress_log.c.input_usd,
                egress_log.c.output_usd,
            ).where(egress_log.c.detail == detail, egress_log.c.outcome == ATTEMPTED)
        ).all()
        for row in rows:
            price = (row.input_usd, row.output_usd)
            connection.execute(
                egress_log.update()
                .where(egress_log.c.id == row.id)
                .values(outcome=outcome, cost_usd=cost_of(row.model, price), **usage)
            )


def spent_since(moment: float) -> float:
    with database.read() as connection:
        total = connection.execute(
            sa.select(sa.func.sum(egress_log.c.cost_usd)).where(
                egress_log.c.at >= moment, egress_log.c.outcome.in_((SENT, ATTEMPTED))
            )
        ).scalar()
    return float(total or 0.0)
