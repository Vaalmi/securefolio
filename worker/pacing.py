"""How hard the worker may work: utility priority, only on power or above 20%
battery, with a sleep assertion only while jobs are queued on power, and idle models
unloaded after 5 minutes."""

import os
import subprocess
import time
from dataclasses import dataclass, field

import sqlalchemy as sa

from backend import database, embedder, macos, model_store, settings, states
from backend.database import jobs
from worker import page_reader, parse

INDEXING_PAUSED = "indexing_paused"
IDLE_MODEL_SECONDS = 300


@dataclass
class Pacing:
    assertion: subprocess.Popen | None = None
    idle_since: float | None = field(default=None)

    def may_work(self) -> bool:
        return (
            model_store.search_ready()
            and macos.on_power_or_enough_battery()
            and not settings.get(INDEXING_PAUSED)
        )

    def update_sleep_assertion(self) -> None:
        wanted = macos.on_power() and jobs_are_queued()
        if wanted and self.assertion is None:
            self.assertion = macos.hold_sleep_assertion(os.getpid())
        elif not wanted and self.assertion is not None:
            self.assertion.terminate()
            self.assertion = None

    def stop(self) -> None:
        if self.assertion is not None:
            self.assertion.terminate()
            self.assertion = None


def jobs_are_queued() -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(jobs.c.id)
                .where(jobs.c.state.in_((states.QUEUED, states.RUNNING)))
                .limit(1)
            ).first()
            is not None
        )


def unload_idle_models(now: float | None = None) -> None:
    """Each model goes after its own 5 idle minutes."""
    moment = time.monotonic() if now is None else now
    embedder.unload_if_idle(moment)
    page_reader.unload_if_idle(moment, IDLE_MODEL_SECONDS)
    parse.unload_if_idle(moment, IDLE_MODEL_SECONDS)
