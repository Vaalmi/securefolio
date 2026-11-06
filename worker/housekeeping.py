"""The jobs that aren't documents: optimize when the queue drains."""

from typing import Any

from backend import index


def run_optimize(_job: dict[str, Any]) -> None:
    index.optimize()
