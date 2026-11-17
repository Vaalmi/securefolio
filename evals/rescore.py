"""`npm run eval -- --rescore <run>`: a finished kept run scored again with today's
scoring, from its own rows, as a new kept run. It asks no model and opens no vault.
Only a run kept under a scoring version that differs from today's in what a row's
own record gives can be scored again: whether its answer was thrown away, and the
step it went wrong at. Everything else in its rows is as the run recorded it."""

import json
from pathlib import Path
from typing import Any

from evals import compare, figures, run, score, steps
from evals import kept as keeping

CALIBRATION = compare.CALIBRATION
# Scoring versions whose rows today's differs from only in `thrown_away` and `step`:
# 2026-11-13.1 stopped counting the model's own refusal, with its stop, as thrown away.
RESCORABLE = ("2026-11-12.1",)


class CantRescore(SystemExit):
    """This kept run can't be scored again."""


def _questions() -> dict[str, dict[str, Any]]:
    return {q["id"]: q for q in json.loads(CALIBRATION.read_text())}


def rescored_row(question: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """A kept row with what today's scoring tells from its own record told again."""
    answer = {"kind": row.get("kind"), "written": row.get("written")}
    again = {**row, "thrown_away": run.thrown_away(answer)}
    return {**again, "step": steps.step(question, again)}


def _checked(old: keeping.Kept) -> dict[str, Any]:
    manifest = old.manifest
    name = old.folder.name
    if manifest.get("state") != keeping.FINISHED or old.report() is None:
        raise CantRescore(f"{name} isn't a finished run.")
    if manifest.get("question_set") != compare.calibration_set():
        raise CantRescore(f"{name} isn't on the calibration half.")
    if manifest.get("scoring") not in RESCORABLE:
        raise CantRescore(
            f"{name} was scored as {manifest.get('scoring')}, which today's scoring "
            f"({score.SCORING_VERSION}) can't tell again from its rows alone."
        )
    return manifest


def rescore(old: keeping.Kept, root: Path) -> keeping.Kept:
    """The run scored again as a new kept run under `root`, naming the run it came
    from; the old one is left as it was."""
    manifest = _checked(old)
    questions = _questions()
    rows = [rescored_row(questions[row["id"]], row) for row in old.rows()]
    # Everything the run measured that isn't scored from its rows, such as the hard
    # pages and what it spent, is carried over as the run recorded it.
    before = {
        k: v for k, v in (old.report() or {}).items() if k not in ("rows", "kept")
    }
    report = before | run.scored(rows, bool(manifest.get("judged")))
    ended = {"state", "started_at", "ended_at", "local_loaded"}
    again = {k: v for k, v in manifest.items() if k not in ended}
    again |= {"scoring": score.SCORING_VERSION, "rescored_from": old.folder.name}
    new = keeping.start(root, str(manifest["answer_model"]), again)
    for row in rows:
        new.row(row)
    report |= {"kept": str(new.folder), "rescored_from": old.folder.name}
    report["figures"] = figures.own_section(new)  # never the old run's
    new.finish(
        {**report, "rows": rows},
        [row["id"] for row in rows],
        local_loaded=manifest.get("local_loaded"),
    )
    return new
