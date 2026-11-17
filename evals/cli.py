"""`npm run eval`: SecureFolio's evaluation over `data/`. --compare fails a run lower
than evals/baseline.json; --sample-citations N writes citations for the owner's own
check.
--questions runs a set of the owner's own, and writes what it finds into their vault.
--model answers with another model, a provider:model id, or local:<tag>, a model
Ollama runs on this Mac; --judge-model names the judge, Claude Opus 5.5 unless it
says; --helper-model names a cloud model's helper; --only-providers names the
providers a run may send to, and refuses it before it starts if it would call
another. --record writes a cloud model's scores to backend/model_measured.json. A
cloud model's run is judged and a local model's isn't, unless --judge or --no-judge
says otherwise: an unjudged local run runs Offline, so nothing leaves the Mac. Every
run prints what it's expected to cost, and the models it calls, before it starts;
--estimate-only prints that and stops. A run that may spend needs --limit-usd: it
stops before the request that would pass it, keeps what it finished, and exits
non-zero. --side-by-side <run> <run> … puts kept runs' scores side by side, and
--search-parts gives recall and MRR with each part of search turned off; neither
asks a model. --baseline-from <run> writes a finished, judged kept run as its model's
own baseline in evals/baselines/; --record-from <run> writes a finished, judged kept
run's scores to backend/model_measured.json, for Settings, and with --record-local-to
<vault> a local model's to that library, tied to the build measured; --rescore
<run> scores a kept run again, from its rows; --rejudge <run> judges a kept run
again with --judge-model, from its rows, asking only the judge. Every run checks
its answers' figures against their passages and writes the marked ones to a sheet
to sort by hand; --figures-from <run> does that for a kept run, and --figures-sorted
<sheet> reads a sorted sheet back. A follow-up is asked as the second turn of its
first question's thread, and scored apart; --first-only leaves the follow-ups out."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from agent import models
from backend import agent_service, model_catalog, vault
from evals import (
    baselines,
    compare,
    figures,
    measured,
    own_set,
    rejudge,
    rescore,
    run,
    score,
    search_parts,
)
from evals.kept import STOPPED_AT_LIMIT, Kept

BASELINE = Path(__file__).with_name("baseline.json")
REPO = Path(__file__).parents[1]
REPORTS = REPO / ".cache" / "evals"
RUNS = "runs"  # each run kept in a folder of its own
FAILED = 1
BLANK_QUESTION = {
    "expected_document": None,
    "expected_page": None,
    "answer": None,
    "documents": [],
    "private": False,
    "passage_facts": {},
    "answer_facts": [],
    "assessed_by": None,
    "follows": None,
    "standalone": None,
    "earlier": [],  # a report's questions before it in its chat, asked first
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


def _refused(given: argparse.Namespace, own_set: bool, judge: bool) -> str | None:
    """Why this run can't start, if it can't."""
    if vault.configured() is None:
        # With none named, the vault is the owner's own library, which the
        # evaluation must never open, let alone bring up to date.
        return (
            f"{vault.VAULT_ENV} isn't set: name the vault to run on, as "
            "`npm run eval` does."
        )
    gated = given.compare or given.write_baseline or given.record
    if own_set and gated:
        return (
            "evals/baseline.json and backend/model_measured.json are for the "
            "calibration set, not a set of your own."
        )
    if not judge and gated:
        return (
            "--compare, --write-baseline and --record need a judged run: add --judge."
        )
    if gated and given.judge_model != models.JUDGE_MODEL:
        return (
            "evals/baseline.json and backend/model_measured.json are judged by "
            f"{models.JUDGE_MODEL}: --compare, --write-baseline and --record take "
            "that judge."
        )
    if given.record and run.local_of(given.model) is not None:
        return "--record is for cloud models; a local model's scores go in STATUS."
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--skip-hard-pages", action="store_true")
    parser.add_argument("--sample-citations", type=int, default=0)
    parser.add_argument(
        "--model",
        help="Answer with this model, as provider:model or local:<Ollama tag>.",
    )
    parser.add_argument(
        "--judge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Have Claude Opus 5.5 judge the citations: on for a cloud model, off "
        "for a local one.",
    )
    parser.add_argument(
        "--judge-model",
        default=models.JUDGE_MODEL,
        help="The model that judges, as provider:model: Claude Opus 5.5 unless "
        "this says.",
    )
    parser.add_argument(
        "--helper-model",
        help="The helper for this run, as provider:model, instead of the vault's.",
    )
    parser.add_argument(
        "--only-providers",
        nargs="+",
        metavar="PROVIDER",
        help="Send only to these providers, such as openai: a run that would call "
        "another is refused before it starts, and the gate refuses its requests.",
    )
    parser.add_argument(
        "--rejudge",
        type=Path,
        metavar="RUN",
        help="Judge a finished, judged kept run again with --judge-model, from its "
        "own rows, as a new kept run. It asks only the judge, and needs --limit-usd.",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Write the scores to backend/model_measured.json for the model answering.",
    )
    parser.add_argument(
        "--limit-usd",
        type=float,
        help="The most this run may spend, in US dollars: needed for any run that "
        "may spend.",
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Print what the run is expected to cost, and stop.",
    )
    parser.add_argument(
        "--side-by-side",
        type=Path,
        nargs="+",
        metavar="RUN",
        help="Put kept runs' scores side by side, from their folders, and stop.",
    )
    parser.add_argument(
        "--baseline-from",
        type=Path,
        metavar="RUN",
        help="Write a finished, judged kept run on the calibration half as its "
        "model's own baseline, evals/baselines/<model>.json, and stop.",
    )
    parser.add_argument(
        "--record-from",
        type=Path,
        metavar="RUN",
        help="Write a finished, judged kept run's scores to "
        "backend/model_measured.json, for Settings, and stop. It asks no model.",
    )
    parser.add_argument(
        "--record-local-to",
        type=Path,
        metavar="VAULT",
        help="With --record-from, write a local model's run's scores to this "
        "library, tied to the build measured, instead. Judged or not.",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        metavar="RUN",
        help="Score a finished kept run again with today's scoring, from its own "
        "rows, as a new kept run, and stop. It asks no model.",
    )
    parser.add_argument(
        "--figures-from",
        type=Path,
        metavar="RUN",
        help="Check a finished kept run's figures against their passages, write the "
        "marked ones to a sheet to sort by hand, and stop. It asks no model.",
    )
    parser.add_argument(
        "--figures-sorted",
        type=Path,
        metavar="SHEET",
        help="Read a sheet of marked figures sorted by hand, print its sorts and "
        "wrong marks, and stop.",
    )
    parser.add_argument(
        "--first-only",
        action="store_true",
        help="Ask the first questions alone, without their follow-ups.",
    )
    parser.add_argument(
        "--search-parts",
        action="store_true",
        help="Print recall@10 and MRR with each part of search turned off, and "
        "stop. It asks no model.",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=run.QUESTIONS,
        help="A question set of your own, outside the repo, on the vault "
        "SECUREFOLIO_VAULT names.",
    )
    return parser


def _stopped(report: dict) -> int:
    """A run its limit stopped is kept as it is: no hard pages, no baseline, no
    record, and no comparison."""
    stopped = sum(1 for row in report["rows"] if row["state"] == score.STOPPED)
    sys.stdout.write(
        f"Stopped at the limit of ${report['limit_usd']:.2f}, after "
        f"{len(report['rows']) - stopped} of the questions; the question it "
        f"stopped is kept as stopped, not wrong. Kept as {report.get('kept')}.\n"
    )
    return FAILED


def _from_kept_runs(given: argparse.Namespace) -> str | None:
    """What a command that reads kept runs alone prints: it asks no model and opens
    no vault. None for every other command."""
    if given.side_by_side:
        return compare.main(given.side_by_side)
    if given.rescore:
        again = rescore.rescore(Kept(given.rescore), REPORTS / RUNS)
        return f"Scored again as {again.folder}\n"
    written = _written(given)
    if written is not None:
        return written
    if given.figures_from:
        kept = Kept(given.figures_from)
        counted = figures.count(kept)
        try:
            sheet = figures.write_sheet(counted, figures.reports_of(kept))
        except figures.SheetSorted as sorted_sheet:
            raise SystemExit(str(sorted_sheet)) from None
        return figures.text_of(counted, sheet)
    if given.figures_sorted:
        return json.dumps(figures.sorted_report(given.figures_sorted), indent=1) + "\n"
    return None


def _written(given: argparse.Namespace) -> str | None:
    """A kept run written as its model's baseline, or as its scores."""
    if given.baseline_from:
        return f"Wrote {baselines.write(Kept(given.baseline_from))}\n"
    if given.record_from:
        return _recorded(Kept(given.record_from), given.record_local_to)
    return None


def _recorded(kept: Kept, vault: Path | None) -> str:
    """A kept run's scores, where Settings reads them: a cloud model's in the app's
    list, a local model's in the library named."""
    if vault is not None:
        return f"Wrote {measured.record_local_to(kept, vault)}\n"
    return f"Recorded {measured.record_from(kept)} in {model_catalog.MEASURED}\n"


def _judge_checked(model: str) -> str | None:
    """A judge is a cloud model with a known price: the run's ceiling counts it."""
    if run.local_of(model) is not None or model_catalog.price(model) is None:
        return f"--judge-model {model}: the judge is a cloud model with a known price."
    return None


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    given = parser.parse_args(arguments)
    if given.record_local_to and not given.record_from:
        parser.error("--record-local-to writes the run --record-from names.")
    printed = _from_kept_runs(given)
    if printed is not None:
        sys.stdout.write(printed)
        return 0
    refused = _judge_checked(given.judge_model)
    if refused:
        parser.error(refused)
    providers = tuple(given.only_providers) if given.only_providers else None
    with (
        agent_service.judged_by(given.judge_model),
        agent_service.only_providers(providers),
        run.helped_by(given.helper_model),
    ):
        return _main(parser, given)


def _rejudged(parser: argparse.ArgumentParser, given: argparse.Namespace) -> int:
    """A kept run judged again: its ceiling shown first, then judged under its limit."""
    old = Kept(given.rejudge)
    rejudge.checked(old, given.judge_model)
    if not agent_service.provider_allowed(given.judge_model):
        raise run.NotAllowed(
            "This run may send only to the providers it names, and would call "
            f"its judge, {given.judge_model}."
        )
    at_most = rejudge.expected_usd(old, given.judge_model)
    sys.stdout.write(
        f"Expected cost: at most ${at_most:.2f}. It calls {given.judge_model} "
        "to judge.\n"
    )
    if given.estimate_only:
        return 0
    if given.limit_usd is None or given.limit_usd <= 0:
        parser.error("--rejudge may spend: give --limit-usd.")
    vault.use_model_cache()
    new, agreement = rejudge.rejudged(
        old, REPORTS / RUNS, given.judge_model, given.limit_usd
    )
    sys.stdout.write(rejudge.text_of(new, agreement))
    return FAILED if new.state == STOPPED_AT_LIMIT else 0


def _main(parser: argparse.ArgumentParser, given: argparse.Namespace) -> int:
    your_set = given.questions != run.QUESTIONS
    judge = run.judging(given.model, given.judge)
    refused = _refused(given, your_set, judge)
    if refused:
        parser.error(refused)
    if given.rejudge:
        return _rejudged(parser, given)
    # Before anything loads a tokenizer or a model: Hugging Face fixes its home when
    # first imported, and the estimate counts tokens with the embedder's tokenizer.
    vault.use_model_cache()
    reports = own_reports(given.questions) if your_set else REPORTS
    whole = not (given.skip_hard_pages or your_set)
    questions = load_questions(given.questions)
    if given.first_only:
        questions = score.first(questions)
    if given.search_parts:
        sys.stdout.write(search_parts.main(questions))
        return 0
    # Estimated as the run will ask them: your own set's reports as Private as what
    # they name is now, and without any whose document is gone.
    asked, _listed = own_set.checked(questions)
    judge = run.judged_run(asked, given.model, given.judge)  # as the run decides it
    expected = run.expected_cost(asked, given.model, judge, reports / RUNS)
    sys.stdout.write(expected.line() + "\n")
    if given.estimate_only:
        return 0
    refused = run.unlimited(expected, given.limit_usd)
    if refused:
        parser.error(refused)
    # As given, so a set of your reports all Private now runs unjudged unless
    # --judge was asked for.
    report, claims = run.evaluate(
        questions, given.model, reports / RUNS, given.judge, given.limit_usd
    )
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "last.json").write_text(json.dumps(report, indent=1))
    (reports / "claims.json").write_text(json.dumps(claims, indent=1))
    if report.get("stopped_at_limit"):
        return _stopped(report)
    # A report whose document is gone was listed, not asked, so it has no row.
    gone = {g["id"] for g in report.get("reported_failures", {}).get("gone", [])}
    _finish(report, [q for q in questions if q["id"] not in gone], reports, whole)
    return _published(given, report, claims, reports, whole)


def _finish(report: dict, questions: list[dict], reports: Path, whole: bool) -> None:
    """The hard pages, when the run measures them, then the kept run finished."""
    kept = Kept(Path(report["kept"]))
    report["figures"] = figures.own_section(kept)
    if whole:
        try:
            report.update(run.hard_pages())
        except BaseException as stopped:
            kept.fail(type(stopped).__name__)
            raise
    # The latest report again, now with its figures, and the hard pages if measured.
    (reports / "last.json").write_text(json.dumps(report, indent=1))
    try:
        kept.finish(
            report,
            [question["id"] for question in questions],
            local_loaded=report.get("local_loaded"),
        )
    except ValueError as missing:  # a row is missing: failed, never left running
        kept.fail(f"{type(missing).__name__}: {missing}")
        raise


def _published(
    given: argparse.Namespace, report: dict, claims: list, reports: Path, whole: bool
) -> int:
    """A finished run's sample, report, baseline, record and comparison."""
    if given.sample_citations:
        chosen = run.sample(claims, given.sample_citations)
        sample = reports / "citations_to_check.json"
        sample.write_text(json.dumps(chosen, indent=1))
        sys.stdout.write(f"Wrote {len(chosen)} citations to {sample}\n")
    shown = {k: v for k, v in report.items() if k != "rows"}
    sys.stdout.write(json.dumps(shown, indent=1) + "\n")
    if "reported_failures" in report:
        sys.stdout.write(own_set.text_of(report["reported_failures"]))
    if given.write_baseline:
        BASELINE.write_text(json.dumps(shown, indent=1) + "\n")
    if given.record:
        today = datetime.datetime.now(datetime.UTC).date().isoformat()
        try:
            run.record_measured(report["answer_model"], report, today)
        except run.NotRecorded as refused:
            sys.stdout.write(f"{refused}\n")
            return FAILED
    # A set of your reports alone has no questions held to the targets: its own
    # scores are the reported failures that now pass.
    reports_alone = "reported_failures" in report and not report.get("questions")
    problems = [] if reports_alone else check(report, given.compare, whole=whole)
    for problem in problems:
        sys.stdout.write(f"MISSED: {problem}\n")
    return FAILED if problems else 0


if __name__ == "__main__":
    sys.exit(main())
