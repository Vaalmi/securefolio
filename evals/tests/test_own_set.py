"""Your own set: the answers "This was wrong" saved, asked again. A report's earlier
questions go first, in its own thread; it passes only where the new answer cites the
picked document at the picked place, with any fact typed; a report whose document is
gone is listed and not asked; and its scores are reported apart. The main set is scored
as it was before."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from backend import threads, vault
from evals import cli, estimate, own_set, run, score
from evals import kept as keeping
from evals.tests.fake_runs import fake_evaluate
from evals.tests.main_answers import ANSWERS
from evals.tests.questions import QUESTION, SOL
from evals.tests.reported import document, reported

BEFORE = json.loads(Path(__file__).with_name("main_set_before.json").read_text())
MAIN_IDS = ["q1", "q2", "q3", "q4", "q20", "q50", "f1"]


def main_questions() -> dict[str, dict[str, Any]]:
    every = json.loads(run.QUESTIONS.read_text())
    return {q["id"]: {**cli.BLANK_QUESTION, **q} for q in every}


def kept_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return keeping.Kept(Path(report["kept"])).rows()


def lines(start: int, end: int) -> dict[str, Any]:
    return {"kind": "lines", "start": start, "end": end}


def cells(sheet: str, at: str) -> dict[str, Any]:
    return {"kind": "cells", "sheet": sheet, "cells": at}


def sheet(name: str, at: str) -> dict[str, Any]:
    return {"kind": "sheet", "sheet": name, "range": at}


PLACES = [
    ({"kind": "page", "page": 3}, {"kind": "pdf", "page": 3}, True),
    ({"kind": "page", "page": 3}, {"kind": "pdf", "page": 2}, False),
    ({"kind": "slide", "slide": 2}, {"kind": "slide", "slide": 2}, True),
    ({"kind": "message", "message": 1}, {"kind": "message", "message": 2}, False),
    (lines(4, 9), lines(9, 12), True),
    (lines(4, 9), lines(10, 12), False),
    (lines(4, 9), {"kind": "pdf", "page": 4}, False),
    (cells("Bills", "B4:F4"), sheet("Bills", "D2:D9"), True),
    (cells("Bills", "B4:F4"), sheet("Bills", "G1:H9"), False),
    (cells("Bills", "B4:F4"), sheet("Rent", "B4"), False),
    (cells("Bills", "AA10"), sheet("Bills", "Z9:AB11"), True),
]


@pytest.mark.parametrize(("place", "locator", "at"), PLACES)
def test_a_citation_is_at_the_place_by_its_kind(place, locator, at):
    assert own_set.at_the_place(place, locator) is at


def test_a_report_of_a_document_and_a_page_fails_on_the_very_answer_reported(
    lease, tmp_path
):
    """The answer reported, as its thread saved it: citing the lease is no longer
    enough, as it was for a lookup with no expected answer. Only the page it cites
    passes, and only with the fact typed in its words."""
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = kept_rows(report)
    answer = threads.transcript(row["thread_id"])[-1]["answer"]
    cited = answer["citations"][0]
    page = cited["locator"]["page"]
    assert row["citations"][0]["document_id"] == cited["document_id"], "kept by id"
    # The old check: a lookup with no expected answer, the document itself owed.
    lookup = {
        **cli.BLANK_QUESTION,
        **QUESTION,
        "category": "find",
        "expected_document": f"{cited['title']}.pdf",
        "expected_page": None,
        "answer": None,
        "assessed_by": score.BY_DOCUMENT,
    }
    assert run._facts_found(lookup, answer), "the old check passed it"  # noqa: SLF001

    def found(place: int, facts: list[str] | None = None) -> bool | None:
        (asked,), _listed = own_set.checked(
            [
                reported(
                    expected_document=f"{cited['title']}.pdf",
                    expected_document_id=cited["document_id"],
                    expected_place={"kind": "page", "page": place},
                    answer_facts=facts or [],
                )
            ]
        )
        return run._facts_found(asked, answer)  # noqa: SLF001

    assert found(page + 1) is False, "the reported answer fails at another page"
    assert found(page) is True
    assert found(page, ["$299.11"]) is True
    assert found(page, ["$300.00"]) is False


def test_a_reports_earlier_questions_are_asked_first_in_its_own_thread(lease, tmp_path):
    earlier = ["When does my apartment lease end?", "Who signed it?"]
    report, _claims = run.evaluate(
        [reported(earlier=earlier)], SOL, tmp_path / "runs", limit_usd=50.0
    )
    (row,) = kept_rows(report)
    asked = [
        m["content"]
        for m in threads.transcript(row["thread_id"])
        if m["role"] == threads.ROLE_USER
    ]
    assert asked == [*earlier, reported()["question"]]
    assert row["earlier"] == 2
    answers = [
        m["answer"] for m in threads.transcript(row["thread_id"]) if m.get("answer")
    ]
    assert len(answers) == 3
    assert answers[0]["cost_usd"] > 0
    assert row["cost_usd"] == pytest.approx(sum(a["cost_usd"] for a in answers))
    assert report["reported_failures"]["asked"] == 1
    assert report["questions"] == 0, "scored apart, not in the main scores"
    assert row["state"] == score.ANSWERED
    assert row["reported"] is True
    assert report["reported_failures"]["asked"] == 1


def test_a_report_whose_earlier_question_got_no_answer_isnt_asked(
    lease, monkeypatch, tmp_path
):
    asked = []

    def timed_out(_client, question, thread=None):
        asked.append(question["question"])
        return {"thread_id": thread, "times": dict(run._NO_TIMES), "timed_out": True}  # noqa: SLF001

    monkeypatch.setattr(run, "_ask", timed_out)
    report, _claims = run.evaluate(
        [reported(earlier=["When does it end?", "Who signed it?"])],
        SOL,
        tmp_path / "runs",
        limit_usd=50.0,
    )
    assert asked == ["When does it end?"], "nothing after the turn with no answer"
    (row,) = kept_rows(report)
    assert row["state"] == score.FAILED
    assert report["reported_failures"]["failed"] == 1


def test_a_report_whose_document_is_gone_is_listed_and_not_asked(lease, tmp_path):
    gone = reported(id="r-gone", expected_document_id="0" * 32)
    report, _claims = run.evaluate(
        [reported(), gone], SOL, tmp_path / "runs", limit_usd=50.0
    )
    assert [row["id"] for row in kept_rows(report)] == ["r-1"]
    assert report["reported_failures"]["gone"] == [
        {"id": "r-gone", "why": "Apartment lease.pdf is no longer in the library"}
    ]


def test_a_report_naming_any_document_gone_is_listed_and_not_asked(lease, tmp_path):
    """Its picked document is there, but one its
    answer rested on is gone, so its sensitivity can't be read: not asked."""
    rested = {"documents": [document("Apartment lease"), "0" * 32], "memories": []}
    gone = reported(id="r-gone", rested_on=rested)
    report, _claims = run.evaluate(
        [reported(), gone], SOL, tmp_path / "runs", limit_usd=50.0
    )
    assert [row["id"] for row in kept_rows(report)] == ["r-1"]
    assert report["reported_failures"]["gone"] == [
        {
            "id": "r-gone",
            "why": "a document its answer rested on is no longer in the library",
        }
    ]


def test_your_sets_scores_are_reported_apart_as_failures_that_now_pass(lease, tmp_path):
    report, _claims = run.evaluate(
        [reported(), reported(id="r-2", answer_facts=["$300.00"])],
        SOL,
        tmp_path / "runs",
        limit_usd=50.0,
    )
    found = report["reported_failures"]
    assert found["asked"] == 2
    assert found["now_pass"] == sum(1 for r in kept_rows(report) if r["facts_found"])
    text = own_set.text_of(found)
    assert text.startswith(f"{found['now_pass']} of 2 reported failures now pass.")
    assert "isn't a measure of how good SecureFolio is" in text


def test_a_report_is_refused_with_follows_or_without_its_document(lease):
    with pytest.raises(run.CantAsk, match="can't follow another"):
        run.evaluate([QUESTION, reported(follows="q1", earlier=["Hi?"])], SOL)
    with pytest.raises(run.CantAsk, match="names neither"):
        run.evaluate([reported(expected_document_id=None)], SOL)
    # Never asked letter by letter.
    with pytest.raises(run.CantAsk, match="a list of questions"):
        run.evaluate([reported(earlier="Hi?")], SOL)


def test_the_main_sets_pass_check_is_as_it_was(library):
    questions = main_questions()
    found = {
        i: [run._facts_found(questions[i], answer) for answer in answers]  # noqa: SLF001
        for i, answers in ANSWERS.items()
    }
    assert found == BEFORE["facts_found"]


def test_the_main_sets_scores_on_a_run_are_as_they_were(lease, tmp_path):
    questions = main_questions()
    report, _claims = run.evaluate(
        [questions[i] for i in MAIN_IDS], SOL, tmp_path / "runs", limit_usd=50.0
    )
    shown = {
        k: v
        for k, v in report.items()
        if k not in ("rows", "kept", "spent_usd", "local_loaded")
    }
    told = ("id", "state", "facts_found", "correct", "step", "kind", "rank")
    rows = [{k: r.get(k) for k in told} for r in report["rows"]]
    assert json.loads(json.dumps({"report": shown, "rows": rows})) == BEFORE["run"]
    assert "reported_failures" not in report


def test_an_ordinary_run_never_reads_your_sets_file(lease, tmp_path, monkeypatch):
    mine = vault.root() / "evals" / "my_questions.json"
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text("[]")
    mine.chmod(0)  # any read of it fails
    read: list[str] = []
    real = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *given, **named: (
            read.append(path.name) or real(path, *given, **named)
        ),
    )
    try:
        report, _claims = run.evaluate(
            [QUESTION], SOL, tmp_path / "runs", limit_usd=50.0
        )
    finally:
        mine.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert report["questions"] == 1
    assert "my_questions.json" not in read
    assert cli.load_questions(run.QUESTIONS)[0]["id"] == "q1", "the repo's set"


def test_a_run_of_your_set_prints_how_many_reported_failures_now_pass(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("SECUREFOLIO_VAULT", str(tmp_path / "vault"))
    mine = tmp_path / "my_questions.json"
    mine.write_text("[]")
    found = {
        "asked": 3,
        "now_pass": 1,
        "failed": 0,
        "stopped": 0,
        "became_private": [{"id": "r-2", "why": "Diagnosis is in a Private folder"}],
        "gone": [{"id": "r-3", "why": "Old.pdf is no longer in the library"}],
        "note": own_set.NOT_A_MEASURE,
    }
    made = {"question_set": "mine", "reported_failures": found}
    monkeypatch.setattr(cli.run, "evaluate", fake_evaluate(made, []))
    cli.main(["--questions", str(mine), "--limit-usd", "5"])
    printed = capsys.readouterr().out
    assert (
        "1 of 3 reported failures now pass. These are answers you reported" in printed
    )
    assert "isn't a measure of how good SecureFolio is" in printed
    assert "r-2, because Diagnosis is in a Private folder" in printed
    assert "Not asked: r-3, as Old.pdf is no longer in the library" in printed


def test_a_run_of_your_set_with_a_gone_document_finishes(lease, tmp_path, capsys):
    """The run lists the report whose document is gone and keeps a finished run of
    the rest: it has no row for the one it didn't ask."""
    mine = tmp_path / "my_questions.json"
    gone = reported(id="r-gone", expected_document_id="0" * 32)
    mine.write_text(json.dumps([reported(), gone]))
    ended = cli.main(["--questions", str(mine), "--model", SOL, "--limit-usd", "5"])
    printed = capsys.readouterr().out
    assert ended == 0, "a set of reports alone isn't held to the targets"
    assert "MISSED" not in printed
    assert "of 1 reported failures now pass" in printed
    assert "Not asked: r-gone" in printed
    (run_folder,) = (vault.root() / "evals" / "runs").iterdir()
    assert keeping.Kept(run_folder).state == keeping.FINISHED


def test_the_estimate_counts_a_reports_earlier_questions(lease, tmp_path):
    """Each earlier question is answered too, though never judged."""

    def parts(question: dict[str, Any]) -> dict[str, float]:
        return estimate.expected([question], SOL, SOL, True, tmp_path).parts

    alone, after_two = parts(reported()), parts(reported(earlier=["A?", "B?"]))
    rounded = 1e-3  # each part is rounded to four places
    answers, helpers = estimate.ANSWERS, estimate.HELPERS
    assert after_two[answers] == pytest.approx(3 * alone[answers], abs=rounded)
    assert after_two[helpers] == pytest.approx(3 * alone[helpers], abs=rounded)
    assert after_two[estimate.JUDGE] == alone[estimate.JUDGE]
