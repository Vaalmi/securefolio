"""Runs the worker's queue in the test's own process, until it's empty."""

from typing import Any

from backend import jobs
from worker import main as worker_main

LIMIT = 500


def drain(stop_after: int = LIMIT) -> list[dict[str, Any]]:
    """Every job run, in order."""
    ran = []
    for _ in range(stop_after):
        job = jobs.claim()
        if job is None:
            return ran
        worker_main.run_job(job)
        ran.append(job)
    raise AssertionError("the queue didn't empty")


def next_job(allowed_kinds: tuple[str, ...] | None = None) -> dict[str, Any]:
    job = jobs.claim(allowed_kinds)
    assert job is not None, "no job was queued"  # noqa: S101
    return job
