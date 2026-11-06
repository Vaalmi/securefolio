"""`npm run eval`: SecureFolio's evaluation over `data/`. --compare fails a run lower
than evals/baseline.json; --sample-citations N writes citations for the owner's own
check.
--questions runs a set of the owner's own, and writes what it finds into their vault.
--model answers with another model, a provider:model id, the judge still Claude Opus
5.5; --record writes its scores to backend/model_measured.json."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from evals import run, score

BASELINE = Path(__file__).with_name("baseline.json")
REPO = Path(__file__).parents[1]
REPORTS = REPO / ".cache" / "evals"
FAILED = 1
BLANK_QUESTION = {
    "expected_document": None,
    "expected_page": None,
    "answer": None,
    "documents": [],
    "private": False,
    "passage_facts": {},
    "answer_facts": [],
}


def check(
    report: dict,
    against_baseline: bool,
    baseline: Path = BASELINE,
    whole: bool = True,
) -> list[str]:
    problems = score.misses(report, whole)
    if against_baseline:
        problems += score.regressions(report, json.loads(baseline.read_text()))
    return problems


def load_questions(path: Path) -> list[dict]:
    """A set of your own needs only what the fixtures' fields say: the rest default."""
    return [{**BLANK_QUESTION, **question} for question in json.loads(path.read_text())]


def own_reports(questions: Path) -> Path:
    """A run over the owner's documents writes their passages, so none of it may sit
    inside the checkout, where anyone or anything reading the repo could see it."""
    vault = os.environ.get("SECUREFOLIO_VAULT")
    if not vault:
        raise SystemExit(
            "--questions needs SECUREFOLIO_VAULT set to the vault it asks about."
        )
    for path in (Path(vault), questions):
        if path.resolve().is_relative_to(REPO.resolve()):
            raise SystemExit(f"{path} is inside the repo; choose a folder outside it.")
    return Path(vault) / "evals"


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--skip-hard-pages", action="store_true")
    parser.add_argument("--sample-citations", type=int, default=0)
    parser.add_argument("--model", help="Answer with this model, as provider:model.")
    parser.add_argument(
        "--record",
        action="store_true",
        help="Write the scores to backend/model_measured.json for the model answering.",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=run.QUESTIONS,
        help="A question set of your own, outside the repo, on the vault "
        "SECUREFOLIO_VAULT names.",
    )
    given = parser.parse_args(arguments)
    own_set = given.questions != run.QUESTIONS
    if own_set and (given.compare or given.write_baseline or given.record):
        parser.error(
            "evals/baseline.json and backend/model_measured.json are for the "
            "calibration set, not a set of your own."
        )
    reports = own_reports(given.questions) if own_set else REPORTS
    whole = not (given.skip_hard_pages or own_set)
    questions = load_questions(given.questions)
    report, claims = run.evaluate(questions, given.model)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "last.json").write_text(json.dumps(report, indent=1))
    (reports / "claims.json").write_text(json.dumps(claims, indent=1))
    if whole:
        report.update(run.hard_pages())
        (reports / "last.json").write_text(json.dumps(report, indent=1))
    if given.sample_citations:
        chosen = run.sample(claims, given.sample_citations)
        sample = reports / "citations_to_check.json"
        sample.write_text(json.dumps(chosen, indent=1))
        sys.stdout.write(f"Wrote {len(chosen)} citations to {sample}\n")
    shown = {k: v for k, v in report.items() if k != "rows"}
    sys.stdout.write(json.dumps(shown, indent=1) + "\n")
    if given.write_baseline:
        BASELINE.write_text(json.dumps(shown, indent=1) + "\n")
    if given.record:
        today = datetime.datetime.now(datetime.UTC).date().isoformat()
        try:
            run.record_measured(report["answer_model"], report, today)
        except run.NotRecorded as refused:
            sys.stdout.write(f"{refused}\n")
            return FAILED
    problems = check(report, given.compare, whole=whole)
    for problem in problems:
        sys.stdout.write(f"MISSED: {problem}\n")
    return FAILED if problems else 0


if __name__ == "__main__":
    sys.exit(main())
