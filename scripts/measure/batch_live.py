"""A live batch of real cards, up to 200, on a temporary vault: the estimate shown first
against the true bill. Spends real money, a few cents, and may take up to an hour.
Run: PYTHONPATH=. SECUREFOLIO_VAULT=<temporary> SECUREFOLIO_MODEL_CACHE=... \
  uv run --env-file .env python scripts/measure/batch_live.py"""

import json
import os
import tempfile
import time
from pathlib import Path

import sqlalchemy as sa

from backend import database, jobs, keychain, sources, states, vault
from backend.migrations import run as migrations
from data.build import library
from worker import batches
from worker import main as worker_main

WANTED = 120
POLL_SECONDS = 60
LIMIT_SECONDS = 3 * 3600
REPORT = Path(__file__).parents[2] / ".cache" / "batch_live.json"


def _documents(root: Path) -> None:
    seed = 2000
    while sum(1 for p in root.rglob("*.pdf")) < WANTED:
        library.Builder(root / f"set{seed}", seed, prefix=f"B{seed} ").build_all()
        seed += 1


def main() -> None:
    keychain.get_key = lambda: os.environ["ANTHROPIC_API_KEY"]
    os.environ["LOCAL_MODEL"] = "no-such-model"  # cards stay pending for the batch
    vault.use_model_cache()
    work = Path(tempfile.mkdtemp(prefix="securefolio-batch-"))
    _documents(work / "documents")
    migrations.upgrade()
    sources.ensure_library_source()
    source = sources.add_source(str(work / "documents"), states.STANDARD)
    while (job := jobs.claim()) is not None:
        worker_main.run_job(job)
    estimate = sources.organize_estimate(source["id"])
    sources.set_switches(source["id"], auto_organize=True)
    begun = time.monotonic()
    while time.monotonic() - begun < LIMIT_SECONDS:
        batches.tick()
        with database.read() as connection:
            states_now = list(
                connection.execute(
                    sa.select(database.message_batches.c.state)
                ).scalars()
            )
        if states_now and all(state == batches.APPLIED for state in states_now):
            break
        time.sleep(POLL_SECONDS)
    with database.read() as connection:
        log = (
            connection.execute(
                sa.select(database.egress_log).where(
                    database.egress_log.c.purpose == "card"
                )
            )
            .mappings()
            .all()
        )
        complete = connection.execute(
            sa.select(sa.func.count())
            .select_from(database.cards)
            .where(database.cards.c.state == states.COMPLETE)
        ).scalar_one()
    bill = sum(row["cost_usd"] for row in log)
    REPORT.write_text(
        json.dumps(
            {
                "estimate_usd": estimate["backfill_usd"],
                "bill_usd": round(bill, 4),
                "ratio": round(estimate["backfill_usd"] / bill, 3) if bill else None,
                "batches": len(states_now),
                "outcomes": sorted({row["outcome"] for row in log}),
                "input_tokens": sum(row["input_tokens"] for row in log),
                "output_tokens": sum(row["output_tokens"] for row in log),
                "cards_complete": complete,
                "seconds": round(time.monotonic() - begun),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
