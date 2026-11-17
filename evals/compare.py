"""`npm run eval -- --side-by-side <run> <run> …`: every measured model's score on each
measure, the floor and the gap, on the questions every run scored the same way, from
kept runs alone. It asks no model and opens no vault."""

import json
from pathlib import Path
from typing import Any

from agent.models import JUDGE_MODEL
from evals import kept as keeping
from evals import score

CALIBRATION = Path(__file__).parents[1] / "data/evals/calibration/questions.json"
MEASURES = (
    "recall_at_10",
    "mrr",
    "facts_found",
    "unanswerable",
    "answerable_refused",
    "citation_precision",
    "answers_correct",
)
COUNTS = ("thrown_away", "citations_not_judged")


class CantCompare(SystemExit):
    """The runs given can't be put side by side."""


def _checked(runs: list[keeping.Kept]) -> None:
    if len(runs) < 2:  # noqa: PLR2004  side by side takes two
        raise CantCompare("--side-by-side needs at least two kept runs.")
    for kept in runs:
        if kept.state != keeping.FINISHED:
            raise CantCompare(f"{kept.folder} isn't a finished run ({kept.state}).")
    for key, what in (("question_set", "question sets"), ("scoring", "scoring")):
        if len({kept.manifest.get(key) for kept in runs}) > 1:
            raise CantCompare(f"These runs' {what} differ, so they can't compare.")


def judge_of(kept: keeping.Kept) -> str:
    """The judge a kept run had: Claude Opus 5.5 before runs named theirs."""
    return str(kept.manifest.get("judge_model") or JUDGE_MODEL)


def _scored(kept: keeping.Kept) -> dict[str, dict[str, Any]]:
    """A run's Standard first questions that were scored, by question: follow-ups
    are scored apart."""
    return {
        row["id"]: row
        for row in kept.rows()
        if not row["private"]
        and not row.get("follows")
        and row.get("state", score.ANSWERED) == score.ANSWERED
    }


def _outcome(row: dict[str, Any], judged: bool) -> bool | None:
    """Right or wrong, as every run here can tell it: answers correct when all were
    judged, facts found when any wasn't."""
    return row.get("correct") if judged else row.get("facts_found")


def _split(
    by_run: list[dict[str, dict[str, Any]]], shared: list[str], judged: bool
) -> dict[str, Any]:
    told, right, wrong, split = 0, 0, 0, []
    for question in shared:
        outcomes = [_outcome(rows[question], judged) for rows in by_run]
        if None in outcomes:
            continue
        told += 1
        if all(outcomes):
            right += 1
        elif not any(outcomes):
            wrong += 1
        else:
            split.append(question)
    share = (lambda n: n / told) if told else (lambda _n: 0.0)
    return {
        "told_by": "answers_correct" if judged else "facts_found",
        "questions": told,
        "all_right": share(right),
        "all_wrong": share(wrong),
        "split": share(len(split)),
        "split_questions": split,
    }


def _correct_on(
    by_run: list[dict[str, dict[str, Any]]],
    judged: list[bool],
    shared: list[str],
) -> tuple[list[float | None], int]:
    """Answers correct for each judged run, on the answerable questions every judged
    run could tell: a judge refusal in one run takes that question out of all, so
    each is scored on the same questions. None for an unjudged run, and no
    questions when no run was judged."""
    if not any(judged):
        return [None] * len(judged), 0
    told = [
        question
        for question in shared
        if by_run[0][question]["category"] not in score.NO_ANSWER
        and all(
            rows[question].get("correct") is not None
            for rows, was in zip(by_run, judged, strict=True)
            if was
        )
    ]
    shares: list[float | None] = []
    for rows, was in zip(by_run, judged, strict=True):
        right = [rows[question]["correct"] for question in told]
        shares.append((sum(right) / len(right) if right else 0.0) if was else None)
    return shares, len(told)


def calibration_set() -> str:
    return score.set_hash(json.loads(CALIBRATION.read_text()))


def side_by_side(runs: list[keeping.Kept]) -> dict[str, Any]:
    """Each measure for every run, its floor and its gap, on the Standard questions
    every run scored, and answers correct on those every judged run could tell. A
    judged measure is left out for a run that wasn't judged. The split questions are
    named for the calibration half only. Judged runs whose judges differ are
    set side by side as unjudged: one judge's verdicts never meet another's."""
    _checked(runs)
    by_run = [_scored(kept) for kept in runs]
    shared = sorted(set.intersection(*(set(rows) for rows in by_run)))
    judges = {judge_of(kept) for kept in runs if kept.manifest.get("judged")}
    one_judge = len(judges) <= 1
    judged_all = one_judge and all(kept.manifest.get("judged") for kept in runs)
    columns = []
    for kept, rows in zip(runs, by_run, strict=True):
        judged = one_judge and bool(kept.manifest.get("judged"))
        on_shared = [rows[question] for question in shared]
        report = score.summarize(on_shared, score.verdicts(on_shared), judged)
        columns.append(
            {
                "run": kept.folder.name,
                "model": kept.manifest.get("answer_model"),
                "judged": judged,
                **{name: report.get(name) for name in (*MEASURES, *COUNTS)},
            }
        )
    correct, told = _correct_on(
        by_run, [column["judged"] for column in columns], shared
    )
    for column, share in zip(columns, correct, strict=True):
        column["answers_correct"] = share
    measures = {}
    for name in MEASURES:
        scores = [column[name] for column in columns if column[name] is not None]
        measures[name] = {
            "floor": min(scores) if scores else None,
            "gap": max(scores) - min(scores) if scores else None,
            "runs": len(scores),
        }
    split = _split(by_run, shared, judged_all)
    if runs[0].manifest.get("question_set") != calibration_set():
        split["split_questions"] = None  # named for the calibration half only
    return {
        "questions": len(shared),
        "answers_correct_questions": told,
        "columns": columns,
        "measures": measures,
        "split": split,
        "judges": sorted(judges),
    }


def _shown(value: Any) -> str:
    if value is None:
        return "–"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def table(found: dict[str, Any]) -> str:
    """The tables, in Markdown, for a status page or a report."""
    columns = found["columns"]
    names = [
        f"{c['model']} ({'judged' if c['judged'] else 'unjudged'})" for c in columns
    ]
    told = (
        f"answers correct on the {found['answers_correct_questions']} of them every "
        "judged run's judge could tell."
        if any(column["judged"] for column in columns)
        else "No run here was judged, so answers correct isn't given."
    )
    if len(found.get("judges", [])) > 1:
        told = (
            "these runs were judged by different judges "
            f"({', '.join(found['judges'])}), so only what's measured without the "
            "judge is given."
        )
    lines = [
        f"On the {found['questions']} Standard questions every run scored; {told}",
        "",
        "| Measure | " + " | ".join(names) + " | Floor | Gap |",
        "| --- |" + " --- |" * (len(columns) + 2),
    ]
    for name in (*MEASURES, *COUNTS):
        spread = found["measures"].get(name, {})
        cells = [_shown(column[name]) for column in columns]
        tail = [_shown(spread.get("floor")), _shown(spread.get("gap"))]
        lines.append(f"| {name} | " + " | ".join(cells + tail) + " |")
    split = found["split"]
    lines += [
        "",
        f"Told by {split['told_by']}, on {split['questions']} questions: all right "
        f"{split['all_right']:.3f}, all wrong {split['all_wrong']:.3f}, split "
        f"{split['split']:.3f}.",
    ]
    if split["split_questions"] is not None:
        lines.append(
            "Split questions: " + (", ".join(split["split_questions"]) or "none")
        )
    return "\n".join(lines) + "\n"


def main(folders: list[Path]) -> str:
    return table(side_by_side([keeping.Kept(folder) for folder in folders]))
