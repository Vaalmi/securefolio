"""A stand-in for `run.evaluate`, for tests of the command line: it keeps a run as the
real one does, with a row for each question, and returns the report it's given."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals import kept as keeping

Evaluate = Callable[..., tuple[dict[str, Any], list[dict[str, Any]]]]


def fake_evaluate(
    report: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    seen: list[str | None] | None = None,
) -> Evaluate:
    def evaluate(
        questions: list[dict[str, Any]],
        model: str | None = None,
        kept_in: Path | None = None,
        judge: bool | None = None,  # noqa: ARG001  # the real one's, unused here
        limit_usd: float | None = None,  # noqa: ARG001  # the real one's, unused here
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if seen is not None:
            seen.append(model)
        found = dict(report)
        if kept_in is not None:
            kept = keeping.start(kept_in, model or "default", {"questions": 0})
            for question in questions:
                kept.row({"id": question["id"]})
            found["kept"] = str(kept.folder)
        return found, list(claims or [])

    return evaluate
